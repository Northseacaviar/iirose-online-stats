"""SQLite 存储。每次操作独立连接(经 asyncio.to_thread 调用,线程安全)。

指标列:online(总人数) / real(总人数−A.I.) / chatting / active / away / heat(全站热度)。
早期版本有过 entering 列,已整块移除 —— 打开旧库时自动迁移(历史样本的 real/heat 留空,
不伪造数据;迁移前会先复制一份库文件)。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts       TEXT PRIMARY KEY,  -- 本地时间 ISO,精确到秒
    online   INTEGER NOT NULL,
    real     INTEGER,           -- 历史样本无此列数据,故允许空
    chatting INTEGER NOT NULL,
    active   INTEGER NOT NULL,
    away     INTEGER NOT NULL,
    heat     REAL               -- 站点热度分含 .5,用实数
);
"""

# samples 表列名(查询结果 → dict 的键)
_KEYS = ("ts", "online", "real", "chatting", "active", "away", "heat")

_SELECT = "SELECT ts, online, real, chatting, active, away, heat FROM samples"


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # closing():sqlite3 连接的 with 只管事务提交,**不关闭连接** ——
        # 只用 with 会每次操作漏一个文件句柄,靠 GC 兜底,漏到 fd 上限后
        # 读写全部报 "unable to open database file"(实测:1024 软限,约 54 小时打满)。
        with closing(self._connect()) as conn, conn:
            self._migrate(conn)
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧库(带 entering 列)迁移到新 schema。

        只在检测到 entering 列时动手;迁移前复制一份库文件,失败也不丢原始数据。
        """
        cols = [row[1] for row in conn.execute("PRAGMA table_info(samples)")]
        if not cols or "entering" not in cols:
            return  # 新库或已迁移
        bak = self.path.with_name(self.path.name + ".bak-entering")
        if not bak.exists():
            shutil.copy2(self.path, bak)
        conn.executescript(
            """
            ALTER TABLE samples RENAME TO samples_old;
            CREATE TABLE samples (
                ts       TEXT PRIMARY KEY,
                online   INTEGER NOT NULL,
                real     INTEGER,
                chatting INTEGER NOT NULL,
                active   INTEGER NOT NULL,
                away     INTEGER NOT NULL,
                heat     REAL
            );
            INSERT INTO samples (ts, online, chatting, active, away)
                SELECT ts, online, chatting, active, away FROM samples_old;
            DROP TABLE samples_old;
            """
        )
        logging.getLogger("iirose.db").info(
            "samples 表已迁移:移除 entering,新增 real/heat(历史样本这两列留空);"
            "迁移前副本:%s", bak.name,
        )

    def insert_sample(
        self, ts: str, online: int, real: int, chatting: int, active: int,
        away: int, heat: float,
    ) -> None:
        # closing():sqlite3 连接的 with 只管事务提交,**不关闭连接** ——
        # 只用 with 会每次操作漏一个文件句柄,靠 GC 兜底,漏到 fd 上限后
        # 读写全部报 "unable to open database file"(实测:1024 软限,约 54 小时打满)。
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, online, real, chatting, active, away, heat),
            )

    def insert_sample_ignore(
        self, ts: str, online: int, real: int, chatting: int, active: int,
        away: int, heat: float,
    ) -> bool:
        """浏览器侧上报:同秒已有 WS 采样时不覆盖,返回是否写入。"""
        # closing():sqlite3 连接的 with 只管事务提交,**不关闭连接** ——
        # 只用 with 会每次操作漏一个文件句柄,靠 GC 兜底,漏到 fd 上限后
        # 读写全部报 "unable to open database file"(实测:1024 软限,约 54 小时打满)。
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, online, real, chatting, active, away, heat),
            )
            return cur.rowcount > 0

    def query(self, since_ts: str | None = None, limit: int | None = None) -> list[dict]:
        """查询样本(时间升序)。

        since_ts: 只取时间戳 >= 该值的样本;limit: 最多返回条数。
        """
        sql = _SELECT
        params: tuple = ()
        if since_ts is not None:
            sql += " WHERE ts >= ?"
            params = (since_ts,)
        sql += " ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        # closing():sqlite3 连接的 with 只管事务提交,**不关闭连接** ——
        # 只用 with 会每次操作漏一个文件句柄,靠 GC 兜底,漏到 fd 上限后
        # 读写全部报 "unable to open database file"(实测:1024 软限,约 54 小时打满)。
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(zip(_KEYS, row)) for row in rows]

    def latest(self) -> dict | None:
        """最新一条样本;库为空时返回 None。"""
        # closing():sqlite3 连接的 with 只管事务提交,**不关闭连接** ——
        # 只用 with 会每次操作漏一个文件句柄,靠 GC 兜底,漏到 fd 上限后
        # 读写全部报 "unable to open database file"(实测:1024 软限,约 54 小时打满)。
        with closing(self._connect()) as conn, conn:
            row = conn.execute(_SELECT + " ORDER BY ts DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return dict(zip(_KEYS, row))
