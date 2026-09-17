"""protocol 单元测试:帧解码、消息分割、快照解析、登录错误识别。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.protocol import (
    check_login_error,
    decode_frame,
    parse_join_record,
    parse_snapshot,
    split_frame,
)


def _snap(users: list[str]) -> str:
    """构造 % 快照帧:users 为 > 连接字段的条目串。"""
    return '%*"' + "<".join(users) + "'"


def test_decode_plain_and_gzip():
    assert decode_frame(b"hello") == "hello"
    import gzip
    compressed = b"\x01" + gzip.compress("你好".encode("utf-8"))
    assert decode_frame(compressed) == "你好"


def test_split_snapshot_whole():
    frame = _snap(["cartoon/1>0>alice>alice>room>0>>0>uid1>0>0>0>9>0>0>0"])
    msgs = split_frame(frame)
    assert len(msgs) == 1 and msgs[0].startswith('%*"')


def test_split_tilde_whole():
    assert split_frame("~abc<def") == ["~abc<def"]


def test_split_multi_messages_with_continuation():
    # 两条 " 消息,第二条是第一条的续接片段(无前导符号)
    frame = '"1699999999>pic>alice>\'1>0>0>0>0>uid1>0>0>0<continuation-part'
    msgs = split_frame(frame)
    assert msgs == ['"1699999999>pic>alice>\'1>0>0>0>0>uid1>0>0>0', '"continuation-part']


def test_split_umessage_kept_whole():
    # u 消息整帧:不分割
    rec = "cartoon/1>0>alice>alice>room>0>>0>uid1>0>0>0>9>0>0>0"
    assert split_frame("u11" + rec) == ["u11" + rec]


def test_parse_snapshot_users():
    users = [
        "cartoon/1>1>Alice>Alice>roomA>0>>0>uid111111111111>0>0>9>0>0>0",
        "http://x/y.png>0>Bob>Bob>roomB>0>>0>uid222222222222>0>0>4>0>0>0",
        "cartoon/2>2>Carol>Carol>roomC>0>>0>uid333333333333>0>0>>0>0>0",
    ]
    records = parse_snapshot(_snap(users))
    assert len(records) == 3
    assert records[0][2] == "Alice" and records[0][11] == "9"
    assert records[1][11] == "4"
    assert records[2][11] == ""  # away


def test_parse_snapshot_ignores_rooms_and_short_entries():
    frame = '%*"' + "<".join([
        "cartoon/1>0>alice>alice>room>0>>0>uid1>0>0>0>9>0>0>0",
        "5ce6a4b520a90_5ce6a4b520a91>Room>255,0,0>0>1>x,y",  # 房间条目
        "short-entry",
    ]) + "'"
    records = parse_snapshot(frame)
    assert len(records) == 1 and records[0][2] == "alice"


def test_parse_snapshot_accepts_any_avatar_prefix():
    # 联机普查发现头像前缀多样(anime/、system/ 等),只要不是房间条目都算用户
    users = [
        "anime/312>0>anime_user>anime_user>room>0>>0>uid11111111111>0>0>8>0>0>0",
        "system/900004>0>default_avatar>default_avatar>room>0>>0>uid22222222222>0>0>>0>0>0",
    ]
    records = parse_snapshot(_snap(users))
    assert len(records) == 2
    assert records[0][2] == "anime_user" and records[0][11] == "8"
    assert records[1][2] == "default_avatar" and records[1][11] == ""


def test_parse_snapshot_empty_users():
    assert parse_snapshot('%*"\'rooms...') == []


def test_login_error_codes():
    assert check_login_error('%*"2') == ("2", "密码错误")
    assert check_login_error('%*"x…') == ("x", "账号被封禁")
    assert check_login_error(_snap(["cartoon/1>0>a>a>r>0>>0>u>0>0>0>9>0>0>0"])) is None


def test_parse_join_record():
    rec = "cartoon/1>0>alice>alice>room>0>>0>uid1>0>0>9>0>0>0"
    fields = parse_join_record(rec)
    assert fields is not None and fields[2] == "alice" and fields[11] == "9"
    # 前导 < 残留
    fields = parse_join_record("<" + rec)
    assert fields is not None and fields[2] == "alice"
    # 非法载荷
    assert parse_join_record("garbage") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
