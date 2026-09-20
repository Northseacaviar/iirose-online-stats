"""ws_client 边界:hosts 空配置应优雅退出,而非除零崩溃。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.userlist import UserList
from collector.ws_client import IIRoseClient


def test_empty_hosts_exits_gracefully():
    async def run():
        client = IIRoseClient(UserList(), hosts=[], username="u", password="p")
        await client.run()  # 不应抛 ZeroDivisionError,日志报错后直接返回
    asyncio.run(run())


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
