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
        return {"ts": self.last_ts, "online": 1, "real": 1, "chatting": 1,
                "active": 1, "away": 1, "heat": 2.0}

    def insert_sample(self, ts, online, real, chatting, active, away, heat):
        self.inserted.append((ts, online, real, chatting, active, away, heat))


def _sampler(db, log):
    return Sampler(None, db, interval_seconds=600, log=log)


def _insert(sampler):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stats = {"online": 100, "real": 98, "chatting": 20, "active": 30,
             "away": 40, "heat": 512.5}
    sampler._insert(now, stats)
    return now


def test_recent_sample_no_warning(caplog):
    last = (datetime.now() - timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%S")
    db = _FakeDb(last_ts=last)
    with caplog.at_level(logging.WARNING):
        _insert(_sampler(db, logging.getLogger("t")))
    assert not caplog.text, f"正常间隔不应警告,却输出: {caplog.text}"
    assert len(db.inserted) == 1


def test_inserted_row_carries_all_metrics():
    db = _FakeDb()
    _insert(_sampler(db, logging.getLogger("t")))
    assert db.inserted[0][1:] == (100, 98, 20, 30, 40, 512.5)


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
