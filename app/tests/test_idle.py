"""网页脚本模式空闲自动退出(idle_watchdog)测试。"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from run import idle_watchdog
from web.server import BrowserActivity


def test_watchdog_exits_after_idle_exceeded():
    """超过阈值无上报 → 看门狗返回(程序随之优雅退出)。"""
    activity = BrowserActivity()
    activity.last_ingest = time.time() - 3600  # 假装 1 小时没有网页上报
    # 0.05 分钟 = 3 秒阈值,1.5 秒后第一次检查即触发;10 秒内必须正常返回
    asyncio.run(asyncio.wait_for(idle_watchdog(activity, 0.05), timeout=10))


def test_watchdog_keeps_running_while_fresh():
    """持续有上报 → 看门狗不退出(等待超时 = 仍存活)。"""
    activity = BrowserActivity()  # 刚启动,视为刚活跃
    # 0.2 分钟 = 12 秒阈值,6 秒时第一次检查(idle≈6s < 12s)→ 不返回;
    # wait_for 8 秒超时抛 TimeoutError 即证明看门狗还守着
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(idle_watchdog(activity, 0.2), timeout=8))
