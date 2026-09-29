"""userlist 单元测试:人数分桶(对照网页客户端公式)+ real/heat 聚合。"""
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
        "online": 0, "real": 0, "chatting": 0, "active": 0, "away": 0, "heat": 0.0,
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
        "real": 15,      # 16 − 1 个 A.I.
        "chatting": 5,   # 状态 9,8,7,6,5
        "active": 5,     # 状态 4,3,2,1,0
        "away": 2,       # ""
        # 20+18+16+14+12 + 4+3.5+3+2.5+2 + 0+0 + 1+1+1 + 0(a 不计) = 98
        "heat": 98.0,
    }


def test_upsert_remove():
    ul = UserList()
    ul.rebuild([_rec("Alice", "9"), _rec("Bob", "")])
    assert ul.compute_stats()["online"] == 2
    # u11 更新 Alice 状态为 ""
    ul.upsert(_rec("alice", ""))  # 键为小写
    stats = ul.compute_stats()
    assert stats["chatting"] == 0 and stats["away"] == 2 and stats["heat"] == 0.0
    # u10 删除 Bob
    ul.remove("bob")
    assert ul.compute_stats()["online"] == 1


def test_rebuild_replaces():
    ul = UserList()
    ul.rebuild([_rec("A", "9"), _rec("B", "9")])
    ul.rebuild([_rec("C", "")])
    stats = ul.compute_stats()
    assert stats == {
        "online": 1, "real": 1, "chatting": 0, "active": 0, "away": 1, "heat": 0.0,
    }


def test_unknown_status_ignored_in_buckets_but_counted_online():
    ul = UserList()
    ul.rebuild([_rec("A", "9"), _rec("B", "z")])
    stats = ul.compute_stats()
    assert stats["online"] == 2 and stats["chatting"] == 1
    assert stats["real"] == 2          # 未知状态不算 A.I.
    assert stats["heat"] == 20.0       # 未知状态记 0 分


def test_real_excludes_only_ai():
    ul = UserList()
    ul.rebuild([_rec("人1", "9"), _rec("人2", "*"), _rec("bot1", "a"), _rec("bot2", "a")])
    stats = ul.compute_stats()
    assert stats["online"] == 4 and stats["real"] == 2


def test_heat_ignores_ai_and_counts_entering():
    ul = UserList()
    ul.rebuild([_rec("bot", "a"), _rec("刚进房", "*"), _rec("挂机", "")])
    stats = ul.compute_stats()
    assert stats["heat"] == 1.0        # 机器人 0 + 刚进房 1 + 挂机 0


def test_name_key_is_lowercase():
    ul = UserList()
    ul.rebuild([_rec("Alice", "9")])
    ul.remove("ALICE")
    assert len(ul) == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
