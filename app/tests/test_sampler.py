"""采样器数据缺失警告:距上次入库超过 2 个采样间隔时告警(停机/断连)。"""
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.sampler import Sampler


class _FakeDb:
    """只实现 Sampler._insert 用到的接口。"""

    def __init__(self, last_ts=None):
        self.last_ts = last_ts
        self.inserted = []

    def latest(self):
        if self.last_ts is None:
            return None
        return {"ts": self.last_ts, "online": 1, "chatting": 1, "active": 1,
                "away": 1, "entering": 1}

    def insert_sample(self, ts, online, chatting, active, away, entering):
        self.inserted.append((ts, online, chatting, active, away, entering))


def _sampler(db, log):
    return Sampler(None, db, interval_seconds=60, log=log)


def _insert(sampler):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stats = {"online": 100, "chatting": 20, "active": 30, "away": 40, "entering": 5}
    sampler._insert(now, stats)
    return now


def test_recent_sample_no_warning(caplog):
    last = (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S")
    db = _FakeDb(last_ts=last)
    with caplog.at_level(logging.WARNING):
        _insert(_sampler(db, logging.getLogger("t")))
    assert not caplog.text, f"正常间隔不应警告,却输出: {caplog.text}"
    assert len(db.inserted) == 1


def test_gap_after_shutdown_warns(caplog):
    last = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
    db = _FakeDb(last_ts=last)
    with caplog.at_level(logging.WARNING):
        _insert(_sampler(db, logging.getLogger("t")))
    assert "距上次采样已隔" in caplog.text and "数据缺失" in caplog.text
    assert len(db.inserted) == 1  # 警告后仍正常入库


def test_empty_db_no_warning(caplog):
    db = _FakeDb(last_ts=None)
    with caplog.at_level(logging.WARNING):
        _insert(_sampler(db, logging.getLogger("t")))
    assert not caplog.text
    assert len(db.inserted) == 1
