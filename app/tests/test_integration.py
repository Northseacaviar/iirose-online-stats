"""集成测试:本地 mock WS 服务器回放协议消息,验证 客户端→用户列表→采样→入库 全链路。"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import websockets

from collector.sampler import Sampler
from collector.userlist import UserList
from collector.ws_client import IIRoseClient
from storage.db import Database

# 快照用户:u1 状态9、u2 状态5、u3 状态4、u4 挂机""、u5 进入"*"
_SNAPSHOT_USERS = [
    "cartoon/1>1>u1>u1>roomA>0>>0>uid00000000001>0>0>9>0>0>0",
    "cartoon/1>1>u2>u2>roomA>0>>0>uid00000000002>0>0>5>0>0>0",
    "cartoon/1>1>u3>u3>roomB>0>>0>uid00000000003>0>0>4>0>0>0",
    "cartoon/1>1>u4>u4>roomB>0>>0>uid00000000004>0>0>>0>0>0",
    "cartoon/1>1>u5>u5>roomC>0>>0>uid00000000005>0>0>*>0>0>0",
]
_JOIN = "cartoon/1>1>u6>u6>roomC>0>>0>uid00000000006>0>0>8>0>0>0"
_LEAVE_NAME = "u3"
_LEGACY_JOIN = '"1699999999>pic>legacy_user>\'1>0>0>0>0>uid00000000007>0>0>0'

RECEIVED = []  # mock 服务器收到的消息(供断言)


async def mock_handler(ws):
    login = await asyncio.wait_for(ws.recv(), 5)
    RECEIVED.append(login)
    assert login.startswith("*")
    packet = json.loads(login[1:])
    assert packet["n"] == "testuser"
    assert packet["p"] == hashlib.md5(b"testpass").hexdigest()

    await ws.send('%*"' + "<".join(_SNAPSHOT_USERS) + "'")
    ack = await asyncio.wait_for(ws.recv(), 5)
    RECEIVED.append(ack)
    assert ack == ">#"  # 快照回执

    await ws.send("u11" + _JOIN)
    await ws.send("u10" + _LEAVE_NAME)
    await ws.send(_LEGACY_JOIN)

    # 收心跳(客户端每 2s 发 c),直到连接关闭
    while True:
        try:
            msg = await asyncio.wait_for(ws.recv(), 5)
            RECEIVED.append(msg)
        except Exception:
            break


async def _wait_until(predicate, timeout=8.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_full_pipeline(tmp_path):
    # 启动 mock 服务器
    server = await websockets.serve(mock_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    db = Database(tmp_path / "test.db")
    userlist = UserList()
    client = IIRoseClient(
        userlist, hosts=["127.0.0.1"], port=port,
        username="testuser", password="testpass", room="5ce6a4b520a90",
    )
    sampler = Sampler(client, db, interval_seconds=0.3)

    tasks = [
        asyncio.create_task(client.run()),
        asyncio.create_task(sampler.run()),
    ]
    try:
        # 等全链路生效:快照 + u11 + u10 + legacy 事件 → 6 名用户
        assert await _wait_until(lambda: client.has_data and len(userlist) == 6), (
            f"用户列表未达预期: {len(userlist)}"
        )
        # 等采样器落库
        assert await _wait_until(lambda: db.latest() is not None)
        row = db.latest()
        # online=6(u1..u6 + legacy)
        # chatting: u1(9) u2(5) u6(8) = 3
        # active: u3 已被 u10 删除 → 0
        # away: u4 = 1; entering: u5 + legacy = 2
        assert (row["online"], row["chatting"], row["active"], row["away"], row["entering"]) == (6, 3, 0, 1, 2), row
        # 握手回执已发生;心跳周期 2s,稍等它出现(须在清理之前)
        assert any(m == ">#" for m in RECEIVED)
        assert await _wait_until(lambda: any(m == "c" for m in RECEIVED), timeout=4.0)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
