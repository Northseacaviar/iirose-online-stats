"""时间范围 API 测试:自定义日期区间(start/end)+ 全局数据起点(data_start)。

口径:
- 自定义区间 [开始日 00:00, 结束日次日 00:00) —— 结束日含当天整天;
- 数据起点(配置 data_start)之前的样本不统计、不显示,但样本仍在库里;
- 预设 range 照旧,只是同样被数据起点顶住。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from aiohttp.test_utils import TestClient, TestServer

from storage.db import Database
from web.server import create_app, data_start_in_future, normalize_data_start

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_START = "2026-09-30T00:00:00"

# 一条"正式采集期"的样本(10/1 之后)与两条试跑期样本(9/17–9/20)
_SEED = [
    "2026-09-17T10:00:00",
    "2026-09-20T22:00:00",
    "2026-09-29T23:59:59",
    "2026-09-30T00:00:00",
    "2026-10-01T08:00:00",
]


def _client(db: Database, data_start: str | None = None) -> TestClient:
    return TestClient(TestServer(create_app(db, WEB_DIR, data_start=data_start)))


def _seed(db: Database, stamps: list[str]) -> None:
    for ts in stamps:
        db.insert_sample(ts, 100, 98, 10, 20, 30, 400.0)


def _ts_of(body: dict) -> list[str]:
    return [row["ts"] for row in body["samples"]]


def test_impossible_date_reports_date_error(tmp_path):
    """不存在的日期要报"日期不存在"。

    回归:`2026-13-45` 字典序上大于 `2026-10-01`,校验顺序若先比字符串后解析日期,
    就会把"日期写错了"误报成"开始日期不能晚于结束日期"。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            for query, expect in (
                ("start=2026-13-45&end=2026-10-01", "日期不存在"),
                ("start=2026-99-99&end=2026-10-01", "日期不存在"),
                ("start=0000-01-01&end=2026-10-01", "日期不存在"),
                ("start=2026-10-09&end=2026-10-01", "开始日期不能晚于结束日期"),
            ):
                resp = await cli.get(f"/api/series?{query}")
                body = await resp.json()
                assert resp.status == 400, query
                assert expect in body["error"], (query, body["error"])
    asyncio.run(run())


def test_data_start_in_future_helper():
    """起点晚于当前时间:启动提醒与前端提示共用的判定。"""
    assert data_start_in_future("2999-01-01T00:00:00") is True
    assert data_start_in_future("2020-01-01T00:00:00") is False
    assert data_start_in_future(None) is False
    assert data_start_in_future("") is False


def test_run_warns_when_data_start_in_future(caplog):
    """启动时起点若在未来,要 WARNING 一声(否则空图看着像程序坏了)。"""
    import logging

    from run import _warn_future_data_start

    log = logging.getLogger("iirose.test")
    _warn_future_data_start(log, "2999-01-01T00:00:00")
    _warn_future_data_start(log, "2020-01-01T00:00:00")  # 正常起点不该出声
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1, [r.getMessage() for r in warns]
    assert "晚于当前时间" in warns[0].getMessage()


def test_custom_range_is_day_inclusive(tmp_path):
    """选 10/1 一天:含 10/1 全天,不含 10/2 0 点。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, [
            "2026-09-30T23:50:00",
            "2026-10-01T00:00:00",
            "2026-10-01T12:30:00",
            "2026-10-01T23:59:59",
            "2026-10-02T00:00:00",
        ])
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?start=2026-10-01&end=2026-10-01")
            body = await resp.json()
        assert resp.status == 200
        assert body["range"] == "custom"
        assert _ts_of(body) == [
            "2026-10-01T00:00:00", "2026-10-01T12:30:00", "2026-10-01T23:59:59"
        ]
        assert body["start"] == "2026-10-01T00:00:00"
        assert body["end"] == "2026-10-02T00:00:00"
    asyncio.run(run())


def test_custom_range_spans_multiple_days(tmp_path):
    """选 9/30–10/1:两天整天,9/29 与 10/2 的样本都不进。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get(
                "/api/series?start=2026-09-30&end=2026-10-01")).json()
        assert _ts_of(body) == ["2026-09-30T00:00:00", "2026-10-01T08:00:00"]
    asyncio.run(run())


def test_custom_range_without_dates_falls_back_to_preset(tmp_path):
    """只给一个参数也算自定义:开始日缺省 = 数据起点,结束日缺省 = 到现在。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?end=2026-10-01")).json()
            assert body["range"] == "custom"
            assert body["start"] == DATA_START          # 缺省起点 = 数据起点
            assert body["end"] == "2026-10-02T00:00:00"  # 10/1 整天
            assert _ts_of(body) == ["2026-09-30T00:00:00", "2026-10-01T08:00:00"]

            body = await (await cli.get("/api/series?start=2026-10-01")).json()
            assert body["start"] == "2026-10-01T00:00:00"
            assert body["end"] is None                   # 缺省终点 = 现在(前端跟着刷新)
            assert _ts_of(body) == ["2026-10-01T08:00:00"]
    asyncio.run(run())


def test_range_ending_today_is_open_ended(tmp_path):
    """结束日含今天时上界交给"现在",不画到明天 0 点的空白。"""
    from datetime import datetime, timedelta

    async def run():
        db = Database(tmp_path / "t.db")
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
        _seed(db, [stamp])
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get(
                f"/api/series?start={yesterday}&end={today}")).json()
        assert body["end"] is None
        assert _ts_of(body) == [stamp]
    asyncio.run(run())


def test_data_start_hides_earlier_samples(tmp_path):
    """9/30 0 点之前的样本(试跑期)任何范围都不返回;自定义起点被顶到数据起点。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?range=all")).json()
            assert body["data_start"] == DATA_START
            assert body["start"] == DATA_START
            assert _ts_of(body) == ["2026-09-30T00:00:00", "2026-10-01T08:00:00"]

            body = await (await cli.get(
                "/api/series?start=2026-09-25&end=2026-10-01")).json()
            assert body["start"] == DATA_START  # 9/25 < 起点 → 顶到 9/30
            assert _ts_of(body) == ["2026-09-30T00:00:00", "2026-10-01T08:00:00"]
    asyncio.run(run())


def test_data_start_does_not_truncate_preset_windows(tmp_path):
    """预设 24h 的窗口本来就晚于起点,不应被起点改动(只在样本上生效)。"""
    from datetime import datetime, timedelta

    async def run():
        db = Database(tmp_path / "t.db")
        recent = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        _seed(db, ["2026-10-02T10:00:00", recent])
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?range=24h")).json()
        assert body["start"] != DATA_START
        assert body["start"][:10] >= DATA_START[:10]
        assert _ts_of(body) == [recent]
    asyncio.run(run())


def test_empty_data_start_hides_nothing(tmp_path):
    """data_start 为 None / "" 时不设起点:全部样本照常返回(库里那批试跑数据也在)。"""
    async def run():
        for value in (None, ""):
            db = Database(tmp_path / f"t-{value or 'none'}.db")
            _seed(db, _SEED)
            async with _client(db, value) as cli:
                body = await (await cli.get("/api/series?range=all")).json()
            assert body["data_start"] is None
            assert _ts_of(body) == _SEED
    asyncio.run(run())


def test_latest_respects_data_start(tmp_path):
    """样本全在起点之前时,/api/latest 不吐旧样本(瓦片显示 —),而不是显示试跑期数字。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, ["2026-09-17T10:00:00", "2026-09-20T22:00:00"])
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/latest")).json()
        assert body["sample"] is None
        assert body["data_start"] == DATA_START
    asyncio.run(run())


@pytest.mark.parametrize("query", [
    "start=2026-9-1&end=2026-10-01",      # 未补零
    "start=abc&end=2026-10-01",
    "start=2026-10-01T00:00:00&end=2026-10-02",  # 只认日期
    "start=2026-13-45&end=2026-10-01",    # 月份非法
    "start=2026-02-30&end=2026-10-01",    # 该月没有这一天
    "start=2026-10-09&end=2026-10-01",    # 开始晚于结束
])
def test_bad_date_params_rejected(tmp_path, query):
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get(f"/api/series?{query}")
            body = await resp.json()
        assert resp.status == 400, query
        assert body["ok"] is False and body["error"]
        # 报错信息须是自家口径,不能把 Python 内部原文(英文)透出去
        assert "does not match format" not in body["error"], query
    asyncio.run(run())


@pytest.mark.parametrize("query", [
    "end=9999-12-31",
    "start=2026-10-01&end=9999-12-31",
    "start=9999-12-31&end=9999-12-31",
])
def test_end_date_overflow_rejected(tmp_path, query):
    """结束日 9999-12-31(次日会越界)必须 400 —— 曾因只捕 ValueError 而穿透成 500。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get(f"/api/series?{query}")
            body = await resp.json()
        assert resp.status == 400, query
        assert body["ok"] is False and body["error"]
        assert "must be in" not in body["error"], query
    asyncio.run(run())


def test_date_re_only_ascii_digits():
    """自定义范围只认 ASCII 日期:Unicode 数字(阿拉伯-印度数字、全角数字)不算日期。"""
    from web.server import _DATE_RE

    assert _DATE_RE.match("2026-10-01")
    for bad in ("٢٠٢٦-١٠-٠١", "2026-1０-01", "2026-10-01T00:00:00", "2026-10-01 "):
        assert not _DATE_RE.match(bad), bad


def test_range_entirely_before_data_start_rejected(tmp_path):
    """选 9/17–9/20(全是试跑期):报"早于数据起点"而不是含糊的 400。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, _SEED)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?start=2026-09-17&end=2026-09-20")
            body = await resp.json()
        assert resp.status == 400
        assert DATA_START[:10] in body["error"]
    asyncio.run(run())


def test_normalize_data_start_forms():
    assert normalize_data_start(None) is None
    assert normalize_data_start("") is None
    assert normalize_data_start("   ") is None
    assert normalize_data_start("2026-09-30") == DATA_START
    assert normalize_data_start("2026-09-30T00:00") == DATA_START
    assert normalize_data_start("2026-09-30T00:00:00") == DATA_START
    assert normalize_data_start("2026-09-30T05:04") == "2026-09-30T05:04:00"
    assert normalize_data_start("2026-9-30") == DATA_START  # 手写配置:未补零也认
    # 配置写坏 → 启动即失败,不静默忽略
    for bad in ("30/09/2026", "2026-13-45", "2026-02-30", "yesterday", "2026-09-30 00:00"):
        with pytest.raises(ValueError):
            normalize_data_start(bad)


def test_db_query_and_latest_bounds(tmp_path):
    """db 层的下界含、上界不含;latest 也按下界筛。"""
    db = Database(tmp_path / "t.db")
    _seed(db, _SEED)
    rows = db.query(since_ts="2026-09-30T00:00:00", until_ts="2026-10-01T00:00:00")
    assert [r["ts"] for r in rows] == ["2026-09-30T00:00:00"]
    rows = db.query(until_ts="2026-09-30T00:00:00")
    assert [r["ts"] for r in rows] == _SEED[:3]
    assert db.latest(since_ts=DATA_START)["ts"] == "2026-10-01T08:00:00"
    assert db.latest(since_ts="2026-10-02T00:00:00") is None
    assert db.latest()["ts"] == "2026-10-01T08:00:00"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
