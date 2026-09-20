"""iirose WebSocket 客户端:连接、账号登录、心跳、收包分发、断线重连。

参考 iirosebot ws_iirose/ws.py 的主机轮询 + 退避重连。
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging

import websockets

from .handshake import build_login
from .protocol import check_login_error, decode_frame, parse_join_record, parse_snapshot, split_frame
from .userlist import UserList

HEARTBEAT_INTERVAL = 2.0  # 网页客户端每 2s 发 "c" 保活

# wss 端点在 Cloudflare 之后,需带浏览器同款 Origin/UA 头
_WS_HEADERS = {
    "Origin": "https://iirose.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


class IIRoseClient:
    def __init__(
        self,
        userlist: UserList,
        hosts: list[str],
        port: int = 443,
        username: str = "",
        password: str = "",
        room: str = "5ce6a4b520a90",
        log: logging.Logger | None = None,
    ) -> None:
        self.userlist = userlist
        self.hosts = hosts
        self.port = port
        self.username = username
        self.password = password
        self.room = room
        self.log = log or logging.getLogger("iirose.ws")
        # 采样可用性:连接后收到过至少一次非空快照才置 True
        self.has_data = False
        self.connected = asyncio.Event()
        self._hb_task: asyncio.Task | None = None

    async def run(self) -> None:
        """常驻运行:连接失败/断开 → 退避重连,主机轮询。"""
        if not self.hosts:
            self.log.error("ws.hosts 未配置,采集器退出(请检查 config.yaml)")
            return
        backoff = 5.0
        host_index = 0
        while True:
            host = self.hosts[host_index % len(self.hosts)]
            try:
                await self._session(host)
                backoff = 5.0  # 会话正常结束(极少发生)重置退避
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("[%s] 连接异常", host)
            finally:
                self.connected.clear()
                self.has_data = False
            host_index += 1
            self.log.info("%.0fs 后重连(下一主机 %s)", backoff, self.hosts[host_index % len(self.hosts)])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self, host: str) -> None:
        # 443 = 线上 wss 端点;其他端口(如本地 mock 测试)用明文 ws
        scheme = "wss" if self.port == 443 else "ws"
        url = f"{scheme}://{host}:{self.port}"
        self.log.info("连接 %s …", url)
        async with websockets.connect(
            url,
            open_timeout=15,
            proxy=None,  # 直连,不走系统代理(代理路径不支持 WS 附加头)
            additional_headers=_WS_HEADERS,
        ) as ws:
            self.log.info("已连接 %s,发送登录包", url)
            await ws.send(build_login(self.username, self.password, self.room))
            self.connected.set()
            self._hb_task = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    await self._handle_frame(ws, raw)
            finally:
                self._hb_task.cancel()
                # 等待心跳任务真正结束:避免遗留"Task exception was never retrieved"警告
                with contextlib.suppress(asyncio.CancelledError):
                    await self._hb_task

    async def _heartbeat(self, ws) -> None:
        """每 2s 发 `c`(网页客户端唯一保活方式)。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if ws.state.name == "OPEN":
                await ws.send("c")

    async def _handle_frame(self, ws, raw: bytes) -> None:
        text = decode_frame(raw)
        if text.startswith("%"):
            # 快照(或登录错误):照协议先回 >#
            await ws.send(">#")
            err = check_login_error(text)
            if err:
                code, msg = err
                self.log.error("登录失败(%s):%s", code, msg)
                raise ConnectionError(f"login error {code}: {msg}")
            records = parse_snapshot(text)
            if records:
                self.userlist.rebuild(records)
                self.has_data = True
                self.log.info("快照更新:%d 名在线用户", len(records))
            else:
                self.log.warning("收到空快照,忽略(保留现有状态)")
            return
        for msg in split_frame(text):
            self._dispatch(msg)

    def _dispatch(self, msg: str) -> None:
        if msg.startswith("u11"):
            record = parse_join_record(msg[3:])
            if record is not None:
                self.userlist.upsert(record)
        elif msg.startswith("u10"):
            self.userlist.remove(msg[3:])
        elif msg.startswith("u2"):
            pass  # 房间列表更新,不需要
        elif msg.startswith('"'):
            self._legacy_event(msg)
        # 其余(聊天/弹幕/股票等)与本产品无关,忽略

    def _legacy_event(self, msg: str) -> None:
        """旧版 `"`+时间戳 12 字段事件的兜底维护(参考 iirosebot)。

        [3] 事件码:'1 加入 / '2 换房 / '3 离开。
        仅在快照/u 频道不完善时起兜底作用:加入者状态未知,记为 "*"(Entering),
        下次 % 快照会校正。
        """
        fields = msg.split(">")
        if len(fields) < 4:
            return
        code = fields[3]
        name = html.unescape(fields[2]).strip().lower()
        if not name:
            return
        if code == "'1" and name not in self.userlist:
            # 构造最小记录:字段布局与快照一致,状态记 "*"
            rec = ["", "0", name, name, "", "", "", "", "", "", "", "*", ""]
            self.userlist.upsert(rec)
        elif code == "'3":
            self.userlist.remove(name)
        elif code == "'2" and len(fields) > 11:
            self.userlist.move(name, fields[11].lstrip("'").strip())
