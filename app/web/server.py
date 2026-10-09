"""本地 Web 仪表盘服务(aiohttp):序列数据 API + 静态页面托管。"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from collector.anomaly import DEFAULT_CONFIG, reject_reason
from storage.db import Database

# 时间戳格式:与本机采样、SQLite 里的 ts 列同一套(本地时间,秒精度)
_TS_FMT = "%Y-%m-%dT%H:%M:%S"

# 时间范围参数 → 回溯时长(秒)
_RANGES = {
    "1h": 3600,
    "3h": 3 * 3600,
    "8h": 8 * 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "all": None,
}

# 自定义范围只认日期(YYYY-MM-DD),前端用的 <input type="date"> 即此形态
# 用 [0-9] 而非 \d:\d 在 str 模式下也匹配阿拉伯-印度数字/全角数字,那种串不是日期
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def normalize_data_start(value) -> str | None:
    """配置项 data_start(数据起点)→ 归一化时间戳,或 None(不设起点)。

    接受 YYYY-MM-DD / YYYY-MM-DDTHH:MM / YYYY-MM-DDTHH:MM:SS 三种写法(月/日未补零
    也认,配置是手写的);空值(None / "")表示不设起点。其余形态抛 ValueError ——
    配置写坏应启动即失败,而不是静默忽略、让人以为起点生效了。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime(_TS_FMT)
        except ValueError:
            continue
    raise ValueError(f"数据起点无法解析(期望 YYYY-MM-DD[THH:MM[:SS]]):{value!r}")


def _now_ts() -> str:
    return datetime.now().strftime(_TS_FMT)


def _day_start(date_str: str) -> str:
    """日期 → 该日 00:00:00 的时间戳;日期不存在则抛 ValueError(自家口径的文案)。"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime(_TS_FMT)
    except ValueError as exc:
        raise ValueError(f"日期不存在:{date_str}") from exc


def _day_after(date_str: str) -> str:
    """日期 → 次日 00:00:00 的时间戳(自定义范围的结束日含当天整天)。

    9999-12-31 加一天会越界(OverflowError)——按参数错误处理,不让它穿透成 500。
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"日期不存在:{date_str}") from exc
    try:
        return (day + timedelta(days=1)).strftime(_TS_FMT)
    except OverflowError as exc:
        raise ValueError("结束日期超出可处理范围(最晚 9999-12-30)") from exc


# 跨域白名单:上报脚本只在 iirose 页面注入,其余来源一律不放行
# (任意其他网页不得读写本机接口,防统计库投毒)
_ALLOWED_ORIGINS = {"https://iirose.com", "https://www.iirose.com"}

# 单样本数值上限:在线人数不可能达到的量级,同时防 sqlite 整数溢出
_MAX_SAMPLE_VALUE = 1_000_000_000


@web.middleware
async def cors_private_network(request: web.Request, handler):
    """CORS + Chrome Private Network Access:仅放行 iirose(https)页面向本机上报。

    页面是公网 HTTPS,POST 到 http://127.0.0.1 属私有网络请求,预检需回
    `Access-Control-Allow-Private-Network: true` 才放行;OPTIONS 由中间件直接应答。
    Origin 白名单外的一律不回 CORS 头,浏览器会拦截其跨域请求;
    本机同源请求(仪表盘/测试)不带 Origin,不受影响。
    """
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    origin = request.headers.get("Origin")
    if origin in _ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


def create_app(
    db: Database,
    web_dir: Path,
    anomaly_config: dict | None = None,
    js_dir: Path | None = None,
    interval_seconds: float = 60,
    data_start: str | None = None,
) -> web.Application:
    app = web.Application(middlewares=[cors_private_network])
    anomaly_cfg = anomaly_config or {}
    # 数据起点:早于该时刻的样本不统计、不显示(样本仍保留在库里,可改小或清空配置后再看)
    data_start_ts = normalize_data_start(data_start)
    log = logging.getLogger("iirose.web")

    async def collector_js(_request: web.Request) -> web.StreamResponse:
        """浏览器侧上报脚本(browser-js/ 目录是唯一来源)。

        /js/collector.js 为规范地址;保留 /static/collector.js 兼容旧配置。
        no-cache:脚本常更新,强制每次页面加载重新校验,避免浏览器拿到旧版。
        """
        return web.FileResponse(
            js_dir / "collector.js", headers={"Cache-Control": "no-cache"}
        )

    async def index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(web_dir / "dashboard.html")

    def _window(request: web.Request) -> tuple[str, str | None, str | None]:
        """请求参数 → (范围名, 生效下界, 生效上界)。

        下界含、上界不含;None 表示该端不设限(上界 None 即"到现在",由前端跟着刷新)。
        带 start/end 时走自定义范围:start/end 均为日期,结束日含当天整天;
        不带时走预设 range。两侧都先被数据起点顶住 —— 早于起点的样本一律不返回。
        """
        start_q = (request.query.get("start") or "").strip()
        end_q = (request.query.get("end") or "").strip()
        if not start_q and not end_q:
            name = request.query.get("range", "24h")
            seconds = _RANGES.get(name, _RANGES["24h"])
            since = None if seconds is None else (
                datetime.now() - timedelta(seconds=seconds)
            ).strftime(_TS_FMT)
            if since is None or (data_start_ts and since < data_start_ts):
                since = data_start_ts
            return name, since, None

        for label, value in (("start", start_q), ("end", end_q)):
            if value and not _DATE_RE.match(value):
                raise ValueError(f"{label} 需为 YYYY-MM-DD 日期")
        if start_q and end_q and start_q > end_q:
            raise ValueError("开始日期不能晚于结束日期")
        since = _day_start(start_q) if start_q else data_start_ts
        if since and data_start_ts and since < data_start_ts:
            since = data_start_ts
        until = _day_after(end_q) if end_q else None
        if until and until > _now_ts():
            until = None  # 结束日含今天:上界交回"现在",不画未来那段空白
        if since and until and since >= until:
            # 只有一种可能:所选区间整个落在数据起点之前(开始晚于结束已在上面拦掉)
            raise ValueError(f"所选区间早于数据起点({since[:10]}),没有可显示的样本")
        return "custom", since, until

    async def api_series(request: web.Request) -> web.Response:
        try:
            rng, since, until = _window(request)
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        rows = await asyncio.to_thread(db.query, since, None, until)
        # interval_seconds 供前端按采样节拍重建时间线、显示数据缺口
        # data_start/start/end:前端据此画固定时间窗口(自定义范围)与提示数据起点
        return web.json_response(
            {
                "range": rng,
                "samples": rows,
                "interval_seconds": interval_seconds,
                "data_start": data_start_ts,
                "start": since,
                "end": until,
            }
        )

    async def api_latest(_request: web.Request) -> web.Response:
        # 数据起点之后的最近一条;起点之前无样本时返回 None(前端显示"—")
        row = await asyncio.to_thread(db.latest, data_start_ts)
        return web.json_response({"sample": row, "data_start": data_start_ts})

    async def api_ingest(request: web.Request) -> web.Response:
        """浏览器侧上报入口(POST JSON:{online,real,chatting,active,away,heat})。

        时间戳取服务器本机时钟(与 WS 采样同源);同秒已有采样则不覆盖(WS 优先)。
        """
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):  # list/字符串载荷:payload[k] 会抛 TypeError→500
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)
        # 人数指标必须是整数;热度是实数(站点分数含 .5)
        int_keys = ("online", "real", "chatting", "active", "away")
        float_keys = ("heat",)
        keys = int_keys + float_keys
        try:
            raw = {k: payload[k] for k in keys}
        except (KeyError, TypeError):
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)

        def _bad_int(v) -> bool:
            # 拒 bool(True 会被 int() 当 1)、小数(int(1.9) 静默截断为 1)、
            # NaN/Infinity(json 标准字面量,int() 会抛 ValueError→500)
            if isinstance(v, bool):
                return True
            if isinstance(v, int):
                return False
            if isinstance(v, float):
                return not math.isfinite(v) or v != int(v)
            return True

        def _bad_float(v) -> bool:
            # 热度:允许小数,但必须是有限实数
            if isinstance(v, bool):
                return True
            if isinstance(v, (int, float)):
                return not math.isfinite(v)
            return True

        if any(_bad_int(raw[k]) for k in int_keys) or any(
            _bad_float(raw[k]) for k in float_keys
        ):
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)
        counts = [int(raw[k]) for k in int_keys]
        heat = round(float(raw[float_keys[0]]), 1)
        if any(v < 0 or v > _MAX_SAMPLE_VALUE for v in [*counts, heat]):
            return web.json_response({"ok": False, "error": "out of range"}, status=400)
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 异常检测:偏离自身近窗口内均值过大的样本剔除(脚本更新/断连等)
        window = anomaly_cfg.get("window_seconds", DEFAULT_CONFIG["window_seconds"])
        since = (datetime.now() - timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")
        recent = await asyncio.to_thread(db.query, since)
        sample = dict(zip(int_keys, counts)) | {"heat": heat}
        reason = reject_reason(sample, recent, anomaly_cfg)
        if reason:
            log.warning("浏览器上报异常,已剔除(%s)", reason)
            return web.json_response({"ok": True, "written": False, "rejected": reason})
        written = await asyncio.to_thread(
            db.insert_sample_ignore, ts, *counts, heat
        )
        return web.json_response({"ok": True, "written": written, "ts": ts})

    app.router.add_get("/", index)
    app.router.add_get("/api/series", api_series)
    app.router.add_get("/api/latest", api_latest)
    app.router.add_post("/api/ingest", api_ingest)
    if js_dir is not None:
        app.router.add_get("/js/collector.js", collector_js)
        app.router.add_get("/static/collector.js", collector_js)  # 兼容旧配置地址
    app.router.add_static("/static", web_dir / "static")
    return app
