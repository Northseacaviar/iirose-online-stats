"""iirose 在线状态监测器入口:采集器 + 本地仪表盘。"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import yaml
from aiohttp import web

from collector.sampler import Sampler
from collector.userlist import UserList
from collector.ws_client import IIRoseClient
from storage.db import Database
from web.server import create_app

ROOT = Path(__file__).resolve().parent

_DEFAULT_CONFIG = {
    "interval_seconds": 60,
    "http": {"host": "127.0.0.1", "port": 8080},
    "ws": {
        "hosts": ["m.iirose.com", "m1.iirose.com", "m2.iirose.com", "m8.iirose.com"],
        "port": 443,
    },
    "account": {
        "username": "",
        "password": "",
        "room": "5ce6a4b520a90",
    },
    "database": "data/iirose_stats.db",
}


def load_config(path: Path) -> dict:
    config = _DEFAULT_CONFIG
    if path.exists():
        with open(path, encoding="utf-8") as f:
            config.update(yaml.safe_load(f) or {})
    return config


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "collector.log", encoding="utf-8"),
        ],
    )


async def main() -> None:
    setup_logging()
    log = logging.getLogger("iirose")
    config = load_config(ROOT / "config.yaml")

    db = Database(ROOT / config["database"])
    userlist = UserList()

    ws_cfg = config["ws"]
    acct = config.get("account", {})
    client = IIRoseClient(
        userlist,
        hosts=ws_cfg["hosts"],
        port=ws_cfg["port"],
        username=acct.get("username", ""),
        password=acct.get("password", ""),
        room=acct.get("room", "5ce6a4b520a90"),
    )
    anomaly_cfg = config.get("anomaly") or {}
    sampler = Sampler(
        client,
        db,
        interval_seconds=float(config["interval_seconds"]),
        anomaly_config=anomaly_cfg,
    )

    web_cfg = config["http"]
    app = create_app(
        db,
        web_dir=ROOT / "web",
        anomaly_config=anomaly_cfg,
        js_dir=ROOT.parent / "browser-js",  # 网页 JS 唯一来源
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=web_cfg["host"], port=int(web_cfg["port"]))
    await site.start()

    log.info("仪表盘: http://%s:%d 采集间隔:%ds", web_cfg["host"], web_cfg["port"], config["interval_seconds"])
    log.info("WS 端点: wss://%s:%d(账号 %s)", ", ".join(ws_cfg["hosts"]), ws_cfg["port"], acct.get("username") or "(未配置)")

    tasks = [
        asyncio.create_task(client.run(), name="ws-client"),
        asyncio.create_task(sampler.run(), name="sampler"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
