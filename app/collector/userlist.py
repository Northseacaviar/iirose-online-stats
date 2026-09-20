"""全站用户列表维护 + stats 五项指标计算。

计算公式与网页终端 stats 指令完全一致(messages_decoded.js tools(1)):
遍历 userJson 全部用户,按字段[11] 状态字符分桶:
  数字 5..9 → Chatting(近 10 分钟有动作,9 最新)
  数字 0..4 → Active(较久无动作)
  ""        → Away
  "*"       → Entering
  "a"       → A.I.(不计入五项指标)
Online = 用户列表总长度。
"""
from __future__ import annotations

import html

# 状态字符 → 桶下标(与客户端 d[] 数组下标一致)
_STATUS_BUCKET = {"": 10, "*": 11, "a": 12}
_DIGITS = set("0123456789")


class UserList:
    """以「小写用户名」为键的用户表(与网页客户端 userJson 的键一致)。"""

    def __init__(self) -> None:
        self._users: dict[str, list[str]] = {}

    def __len__(self) -> int:
        return len(self._users)

    def __contains__(self, name: str) -> bool:
        return name.strip().lower() in self._users

    def rebuild(self, records: list[list[str]]) -> None:
        """全量重建(来自 % 快照)。records 每条为 > 分字段的 list。"""
        users: dict[str, list[str]] = {}
        for rec in records:
            if len(rec) < 12:
                continue
            name = rec[2].strip().lower()
            if name:
                users[name] = rec
        self._users = users

    def upsert(self, record: list[str]) -> None:
        """加入或更新(来自 u11 消息)。"""
        if len(record) < 12:
            return
        name = record[2].strip().lower()
        if name:
            self._users[name] = record

    def remove(self, name: str) -> None:
        """按用户名删除(来自 u10 消息)。"""
        self._users.pop(name.strip().lower(), None)

    def move(self, name: str, room_id: str) -> None:
        """更新用户所在房间(旧版 " 事件 '2 换房)。"""
        rec = self._users.get(name.strip().lower())
        if rec is not None and len(rec) > 4:
            rec[4] = room_id

    def compute_stats(self) -> dict[str, int]:
        """按客户端公式计算五项指标。"""
        d = [0] * 13  # 只需 0..12
        for rec in self._users.values():
            status = rec[11] if len(rec) > 11 else ""
            bucket = _STATUS_BUCKET.get(status)
            if bucket is None and status and status in _DIGITS:
                bucket = int(status)
            if bucket is not None:
                d[bucket] += 1
        return {
            "online": len(self._users),
            "chatting": d[9] + d[8] + d[7] + d[6] + d[5],
            "active": d[4] + d[3] + d[2] + d[1] + d[0],
            "away": d[10],
            "entering": d[11],
        }
