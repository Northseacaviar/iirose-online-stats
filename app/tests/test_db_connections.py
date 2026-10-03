"""回归:Database 的每个操作都必须关闭自己建立的连接。

背景:sqlite3.Connection 当上下文管理器用时只提交/回滚事务,**不关闭连接**
(Python 文档明确如此)。历史实现写的是 `with self._connect() as conn`,
于是每次 insert/query 都漏一个文件句柄,靠 GC 兜底;漏的速度追上回收时
进程 fd 爬到软限(1024),sqlite 开始报 "unable to open database file",
Koishi 上报全部 500 —— 实测约 54 小时打满,数据断 12 小时。

本测试用 mock 记录每个连接是否被 close(),防止这个坑再回来。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.db import Database


class _TrackedConnection:
    """包一层真连接,记录 close() 有没有被调用。"""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.closed = False

    # 其余方法/属性(execute、rowcount 等)原样转发
    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def close(self) -> None:
        self.closed = True
        self._real.close()


def test_every_operation_closes_its_connection(tmp_path, monkeypatch):
    created: list[_TrackedConnection] = []
    real_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        conn = _TrackedConnection(real_connect(*args, **kwargs))
        created.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    db = Database(tmp_path / "t.db")  # 建库 + 建表
    db.insert_sample("2026-01-01T00:00:00", 1, 1, 1, 1, 1, 1.0)
    db.insert_sample_ignore("2026-01-01T00:10:00", 2, 2, 2, 2, 2, 2.0)
    db.query()
    db.query(since_ts="2026-01-01T00:00:00", limit=5)
    db.latest()

    assert created, "应该建立过连接"
    leaked = [c for c in created if not c.closed]
    assert not leaked, f"{len(leaked)}/{len(created)} 个连接没有关闭"
