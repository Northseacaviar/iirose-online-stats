"""iirose 在线状态监测器入口:采集器 + 本地仪表盘。"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from logging.handlers import RotatingFileHandler
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
        "enabled": False,  # 与发布的 config.yaml 一致:默认网页脚本模式,避免删配置后静默切回 WS
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


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并配置:override 中的子 dict 与 base 逐键合并,不整体覆盖。

    用户只写部分字段(如 ws: {port: 8443})时,hosts/enabled 等默认项仍保留;
    否则浅合并会导致改坏一个字段、丢光同层默认值的启动崩溃。
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict:
    config = dict(_DEFAULT_CONFIG)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            config = _deep_merge(config, yaml.safe_load(f) or {})
    return config


def _num(value, default: float, minimum: float) -> float:
    """配置数值兜底:非数字或低于下限时退回默认值,防配置改坏导致忙循环/崩溃。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v >= minimum else default


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    # 5MB × 3 轮转:7×24 运行下日志不会无限增长撑满磁盘
    handlers = [
        RotatingFileHandler(
            log_dir / "collector.log", encoding="utf-8",
            maxBytes=5 * 1024 * 1024, backupCount=3,
        )
    ]
    if sys.stdout is not None:  # pythonw(开机自启静默运行)下无控制台,只写文件
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


async def _guarded(factory: Callable[[], Awaitable[None]], name: str) -> None:
    """后台任务守护:异常退出记日志并 5 秒后重启,单点故障不拖垮整个监测。

    正常返回视为任务主动结束(如 hosts 未配置),不再重启;
    被取消(CancelledError)直接上抛,由主流程负责退出。
    """
    log = logging.getLogger("iirose")
    while True:
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s 异常退出,5 秒后重启", name)
            await asyncio.sleep(5.0)


async def main() -> None:
    setup_logging()
    log = logging.getLogger("iirose")
    config = load_config(ROOT / "config.yaml")

    db = Database(ROOT / config["database"])
    userlist = UserList()

    ws_cfg = config["ws"]
    acct = config.get("account", {})
    ws_enabled = bool(ws_cfg.get("enabled", False))
    anomaly_cfg = config.get("anomaly") or {}
    interval_seconds = _num(config["interval_seconds"], 60.0, 1.0)  # ≤0 会导致忙循环/前端死循环
    if ws_enabled:
        client = IIRoseClient(
            userlist,
            hosts=ws_cfg["hosts"],
            port=ws_cfg["port"],
            username=acct.get("username", ""),
            # 环境变量优先(加固选项):config.yaml 中可不再存明文密码
            password=os.environ.get("IIROSE_PASSWORD", acct.get("password", "")),
            room=acct.get("room", "5ce6a4b520a90"),
        )
        sampler = Sampler(
            client,
            db,
            interval_seconds=interval_seconds,
            anomaly_config=anomaly_cfg,
        )
    else:
        client = None
        sampler = None

    web_cfg = config["http"]
    app = create_app(
        db,
        web_dir=ROOT / "web",
        anomaly_config=anomaly_cfg,
        js_dir=ROOT.parent / "browser-js",  # 网页 JS 唯一来源
        interval_seconds=interval_seconds,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=web_cfg["host"], port=int(web_cfg["port"]))
    await site.start()

    log.info("仪表盘: http://%s:%d 采集间隔:%ds", web_cfg["host"], web_cfg["port"], config["interval_seconds"])
    tasks = []
    if ws_enabled:
        log.info("WS 端点: wss://%s:%d(账号 %s)", ", ".join(ws_cfg["hosts"]), ws_cfg["port"], acct.get("username") or "(未配置)")
        tasks = [
            asyncio.create_task(_guarded(client.run, "ws-client"), name="ws-client"),
            asyncio.create_task(_guarded(sampler.run, "sampler"), name="sampler"),
        ]
    else:
        log.info("WS 采集已禁用(ws.enabled=false):数据仅来自网页 JS 脚本上报;服务常驻,打开网页即开始采集")
    try:
        if tasks:
            # _guarded 内部已重启兜底;仍以 return_exceptions 收尾,异常只记录不拖垮整个服务
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for task, result in zip(tasks, results):
                if isinstance(result, Exception):
                    log.error("任务 %s 以异常结束: %r", task.get_name(), result)
        else:
            await asyncio.Event().wait()  # 只托管仪表盘与上报 API,常驻不退出
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)  # 等任务真正退出再清理
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
