"""浏览器侧上报链路测试:ingest API + CORS/PNA 头 + 同秒不覆盖 + 异常剔除。"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp.test_utils import TestClient, TestServer

from storage.db import Database
from web.server import create_app

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

SAMPLE = {"online": 156, "chatting": 19, "active": 31, "away": 67, "entering": 3}


def _client(db: Database) -> TestClient:
    return TestClient(TestServer(create_app(db, WEB_DIR)))


def _seed_baseline(db: Database, n: int = 4, step: int = 60) -> None:
    """种入近窗口内基线:online≈150 chatting≈20 active≈30 away≈60 entering≈3。"""
    now = datetime.now()
    for i in range(n, 0, -1):
        ts = (now - timedelta(seconds=i * step)).strftime("%Y-%m-%dT%H:%M:%S")
        db.insert_sample(ts, 150 + i, 20, 30, 60, 3)


def test_ingest_writes_row(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            resp = await cli.post("/api/ingest", json=SAMPLE)
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True and body["written"] is True
            assert body["ts"]  # 服务器本机时钟生成
        latest = db.latest()
        assert latest["ts"] == body["ts"]
        for key in SAMPLE:
            assert latest[key] == SAMPLE[key]
    asyncio.run(run())


def test_ingest_preflight_has_cors_and_pna_headers(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            resp = await cli.options(
                "/api/ingest",
                headers={
                    "Origin": "https://iirose.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Private-Network": "true",
                },
            )
            assert resp.status == 204
            # 白名单来源:回显 Origin(不再是无差别放行的 *)
            assert resp.headers["Access-Control-Allow-Origin"] == "https://iirose.com"
            assert resp.headers["Access-Control-Allow-Private-Network"] == "true"
    asyncio.run(run())


def test_cors_rejects_foreign_origin(tmp_path):
    """白名单外的网页不应拿到 CORS 头,浏览器会拦截其跨域请求。"""
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            resp = await cli.options(
                "/api/ingest",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert resp.status == 204
            assert "Access-Control-Allow-Origin" not in resp.headers
    asyncio.run(run())


def test_ingest_rejects_bad_payload(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            r1 = await cli.post("/api/ingest", data="not json")
            r2 = await cli.post("/api/ingest", json={"online": 1})
            r3 = await cli.post(
                "/api/ingest", json={"online": 1, "chatting": -2, "active": 0,
                                     "away": 0, "entering": 0}
            )
            assert r1.status == 400 and r2.status == 400 and r3.status == 400
        assert db.latest() is None
    asyncio.run(run())


def test_ingest_rejects_invalid_number_types(tmp_path):
    """小数(防静默截断)、bool(防当 1)、字符串、超大整数(防 sqlite 溢出)一律拒绝。"""
    async def run():
        db = Database(tmp_path / "t.db")
        base = dict(SAMPLE)
        cases = [
            {**base, "online": 1.9},
            {**base, "online": True},
            {**base, "online": "156"},
            {**base, "online": 10**12},
        ]
        async with _client(db) as cli:
            for payload in cases:
                resp = await cli.post("/api/ingest", json=payload)
                assert resp.status == 400, payload
        assert db.latest() is None
    asyncio.run(run())


def test_insert_ignore_keeps_first_row(tmp_path):
    db = Database(tmp_path / "t.db")
    ts = "2026-09-17T12:00:00"
    assert db.insert_sample_ignore(ts, 100, 1, 2, 3, 4) is True
    assert db.insert_sample_ignore(ts, 999, 9, 9, 9, 9) is False  # 同秒:WS 值保留
    row = db.latest()
    assert row["online"] == 100 and row["entering"] == 4


def test_ingest_row_appears_in_series_api(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            await cli.post("/api/ingest", json=SAMPLE)
            resp = await cli.get("/api/series?range=24h")
            rows = (await resp.json())["samples"]
        assert len(rows) == 1 and rows[0]["online"] == SAMPLE["online"]
    asyncio.run(run())


def test_series_api_supports_all_ranges(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db) as cli:
            for rng in ("1h", "3h", "8h", "24h", "7d", "all"):
                resp = await cli.get(f"/api/series?range={rng}")
                assert resp.status == 200, rng
                assert (await resp.json())["range"] == rng
    asyncio.run(run())


def test_ingest_rejects_anomalous_sample(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        _seed_baseline(db)
        before = db.latest()["ts"]
        async with _client(db) as cli:
            resp = await cli.post("/api/ingest", json={
                "online": 40, "chatting": 5, "active": 8, "away": 15, "entering": 0})
            body = await resp.json()
            assert resp.status == 200
            assert body["ok"] is True and body["written"] is False
            assert "rejected" in body
        assert db.latest()["ts"] == before  # 未写入
    asyncio.run(run())


def test_ingest_accepts_normal_sample_with_baseline(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        _seed_baseline(db)
        async with _client(db) as cli:
            resp = await cli.post("/api/ingest", json={
                "online": 151, "chatting": 21, "active": 29, "away": 59, "entering": 2})
            body = await resp.json()
            assert body["written"] is True
    asyncio.run(run())


def test_ingest_single_metric_spike_accepted(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        _seed_baseline(db)
        async with _client(db) as cli:
            resp = await cli.post("/api/ingest", json={
                "online": 151, "chatting": 21, "active": 29, "away": 59, "entering": 15})
            body = await resp.json()
            assert body["written"] is True  # 只有 entering 尖峰:放行
    asyncio.run(run())


def test_ingest_stale_baseline_not_used(tmp_path):
    """窗口外(>5 分钟)的旧样本不参与基线:首条新样本直接放行。"""
    async def run():
        db = Database(tmp_path / "t.db")
        old = (datetime.now() - timedelta(seconds=900)).strftime("%Y-%m-%dT%H:%M:%S")
        db.insert_sample(old, 40, 5, 8, 15, 0)  # 很久以前的异常值,不应作基线
        async with _client(db) as cli:
            resp = await cli.post("/api/ingest", json=SAMPLE)
            body = await resp.json()
            assert body["written"] is True
    asyncio.run(run())


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
