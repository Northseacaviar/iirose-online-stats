"""全站用户列表维护 + 采样指标计算。

人数指标与网页终端 stats 指令同一套口径(messages_decoded.js tools(1)):
遍历 userJson 全部用户,按字段[11] 状态字符分桶:
  数字 5..9 → Chatting(近 10 分钟有动作,9 最新)
  数字 0..4 → Active(较久无动作)
  ""        → Away
  "*"       → Entering(刚进房,只参与「真人」与热度,不单列指标)
  "a"       → A.I.(认证机器人账户)

另两项是本项目自己聚合的派生指标:
  real = online − A.I. 人数(把机器人剔掉后的真人规模)
  heat = Σ 每个在线用户的状态分 = 站点口径的全站热度(见 heat.py)

早期版本单列过 entering 指标,已整块移除。
"""
from __future__ import annotations

from .heat import score_of

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

    def compute_stats(self) -> dict[str, int | float]:
        """按客户端公式计算人数分桶,另聚合「真人」与「全站热度」。"""
        d = [0] * 13  # 只需 0..12
        heat = 0.0
        for rec in self._users.values():
            status = rec[11] if len(rec) > 11 else ""
            bucket = _STATUS_BUCKET.get(status)
            if bucket is None and status and status in _DIGITS:
                bucket = int(status)
            if bucket is not None:
                d[bucket] += 1
            # 热度:机器人('a')不贡献,其余按状态分累加
            if status != "a":
                heat += score_of(status)
        online = len(self._users)
        return {
            "online": online,
            "real": online - d[12],          # 总人数 − A.I. 人数
            "chatting": d[9] + d[8] + d[7] + d[6] + d[5],
            "active": d[4] + d[3] + d[2] + d[1] + d[0],
            "away": d[10],
            "heat": round(heat, 1),          # 站点分数含 .5,保留一位小数
        }
