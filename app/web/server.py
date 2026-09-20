"""本地 Web 仪表盘服务(aiohttp):序列数据 API + 静态页面托管。"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from collector.anomaly import DEFAULT_CONFIG, reject_reason
from storage.db import Database

# 时间范围参数 → 回溯时长(秒)
_RANGES = {
    "1h": 3600,
    "3h": 3 * 3600,
    "8h": 8 * 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "all": None,
}

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
) -> web.Application:
    app = web.Application(middlewares=[cors_private_network])
    anomaly_cfg = anomaly_config or {}
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

    async def api_series(request: web.Request) -> web.Response:
        rng = request.query.get("range", "24h")
        seconds = _RANGES.get(rng, _RANGES["24h"])
        since = None
        if seconds is not None:
            since = (datetime.now() - timedelta(seconds=seconds)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        rows = await asyncio.to_thread(db.query, since)
        # interval_seconds 供前端按采样节拍重建时间线、显示数据缺口
        return web.json_response(
            {"range": rng, "samples": rows, "interval_seconds": interval_seconds}
        )

    async def api_latest(_request: web.Request) -> web.Response:
        row = await asyncio.to_thread(db.latest)
        return web.json_response({"sample": row})

    async def api_ingest(request: web.Request) -> web.Response:
        """浏览器侧上报入口(POST JSON:{online,chatting,active,away,entering})。

        时间戳取服务器本机时钟(与 WS 采样同源);同秒已有采样则不覆盖(WS 优先)。
        """
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):  # list/字符串载荷:payload[k] 会抛 TypeError→500
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)
        keys = ("online", "chatting", "active", "away", "entering")
        try:
            raw = [payload[k] for k in keys]
        except (KeyError, TypeError):
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)
        # 严格校验:拒绝 bool(True 会被 int() 当 1)、小数(int(1.9) 静默截断为 1)、
        # NaN/Infinity(json 标准字面量,int() 会抛 ValueError→500)
        def _bad(v) -> bool:
            if isinstance(v, bool):
                return True
            if isinstance(v, int):
                return False
            if isinstance(v, float):
                return not math.isfinite(v) or v != int(v)
            return True

        if any(_bad(v) for v in raw):
            return web.json_response({"ok": False, "error": "bad fields"}, status=400)
        values = [int(v) for v in raw]
        if any(v < 0 or v > _MAX_SAMPLE_VALUE for v in values):
            return web.json_response({"ok": False, "error": "out of range"}, status=400)
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 异常检测:偏离自身近窗口内均值过大的样本剔除(脚本更新/断连等)
        window = anomaly_cfg.get("window_seconds", DEFAULT_CONFIG["window_seconds"])
        since = (datetime.now() - timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")
        recent = await asyncio.to_thread(db.query, since)
        reason = reject_reason(dict(zip(keys, values)), recent, anomaly_cfg)
        if reason:
            log.warning("浏览器上报异常,已剔除(%s)", reason)
            return web.json_response({"ok": True, "written": False, "rejected": reason})
        written = await asyncio.to_thread(db.insert_sample_ignore, ts, *values)
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
