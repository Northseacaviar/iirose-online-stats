"""SQLite 存储。每次操作独立连接(经 asyncio.to_thread 调用,线程安全)。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts       TEXT PRIMARY KEY,  -- 本地时间 ISO,精确到秒
    online   INTEGER NOT NULL,
    chatting INTEGER NOT NULL,
    active   INTEGER NOT NULL,
    away     INTEGER NOT NULL,
    entering INTEGER NOT NULL
);
"""

# samples 表列名(查询结果 → dict 的键)
_KEYS = ("ts", "online", "chatting", "active", "away", "entering")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def insert_sample(
        self, ts: str, online: int, chatting: int, active: int, away: int, entering: int
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?)",
                (ts, online, chatting, active, away, entering),
            )

    def insert_sample_ignore(
        self, ts: str, online: int, chatting: int, active: int, away: int, entering: int
    ) -> bool:
        """浏览器侧上报:同秒已有 WS 采样时不覆盖,返回是否写入。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?)",
                (ts, online, chatting, active, away, entering),
            )
            return cur.rowcount > 0

    def query(self, since_ts: str | None = None, limit: int | None = None) -> list[dict]:
        """查询样本(时间升序)。

        since_ts: 只取时间戳 >= 该值的样本;limit: 最多返回条数。
        """
        sql = "SELECT ts, online, chatting, active, away, entering FROM samples"
        params: tuple = ()
        if since_ts is not None:
            sql += " WHERE ts >= ?"
            params = (since_ts,)
        sql += " ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(zip(_KEYS, row)) for row in rows]

    def latest(self) -> dict | None:
        """最新一条样本;库为空时返回 None。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts, online, chatting, active, away, entering FROM samples "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(zip(_KEYS, row))
