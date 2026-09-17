"""userlist 单元测试:stats 五项指标计算(对照网页客户端公式)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.userlist import UserList


def _rec(name: str, status: str) -> list[str]:
    """构造最小用户记录:字段布局与快照一致,只用 [2] 名字与 [11] 状态。"""
    rec = [""] * 16
    rec[2] = name
    rec[11] = status
    return rec


def test_empty():
    ul = UserList()
    assert ul.compute_stats() == {
        "online": 0, "chatting": 0, "active": 0, "away": 0, "entering": 0,
    }


def test_buckets():
    ul = UserList()
    records = [
        _rec("u1", "9"), _rec("u2", "8"), _rec("u3", "7"), _rec("u4", "6"), _rec("u5", "5"),
        _rec("u6", "4"), _rec("u7", "3"), _rec("u8", "2"), _rec("u9", "1"), _rec("u10", "0"),
        _rec("u11", ""), _rec("u12", ""),
        _rec("u13", "*"), _rec("u14", "*"), _rec("u15", "*"),
        _rec("u16", "a"),
    ]
    ul.rebuild(records)
    stats = ul.compute_stats()
    assert stats == {
        "online": 16,
        "chatting": 5,   # 状态 9,8,7,6,5
        "active": 5,     # 状态 4,3,2,1,0
        "away": 2,       # ""
        "entering": 3,   # "*"
    }


def test_upsert_remove():
    ul = UserList()
    ul.rebuild([_rec("Alice", "9"), _rec("Bob", "")])
    assert ul.compute_stats()["online"] == 2
    # u11 更新 Alice 状态为 ""
    ul.upsert(_rec("alice", ""))  # 键为小写
    stats = ul.compute_stats()
    assert stats["chatting"] == 0 and stats["away"] == 2
    # u10 删除 Bob
    ul.remove("bob")
    assert ul.compute_stats()["online"] == 1


def test_rebuild_replaces():
    ul = UserList()
    ul.rebuild([_rec("A", "9"), _rec("B", "9")])
    ul.rebuild([_rec("C", "")])
    stats = ul.compute_stats()
    assert stats == {"online": 1, "chatting": 0, "active": 0, "away": 1, "entering": 0}


def test_unknown_status_ignored_in_buckets_but_counted_online():
    ul = UserList()
    ul.rebuild([_rec("A", "9"), _rec("B", "z")])
    stats = ul.compute_stats()
    assert stats["online"] == 2 and stats["chatting"] == 1


def test_name_key_is_lowercase():
    ul = UserList()
    ul.rebuild([_rec("Alice", "9")])
    ul.remove("ALICE")
    assert len(ul) == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
