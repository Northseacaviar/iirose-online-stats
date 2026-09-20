"""handshake 单元测试:登录包构建、指纹生成、密码加密。"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.handshake import DEFAULT_ROOM, _fingerprint, build_login


def _parse(packet: str) -> dict:
    """剥离登录包前导 * 并解析 JSON。"""
    assert packet.startswith("*")
    return json.loads(packet[1:])


def test_login_packet_fields():
    packet = build_login("alice", "secret")
    data = _parse(packet)
    assert data["r"] == DEFAULT_ROOM
    assert data["n"] == "alice"
    assert data["p"] == hashlib.md5(b"secret").hexdigest()
    assert data["st"] == "n"
    assert data["mo"] == ""
    assert data["mb"] == ""
    assert data["mu"] == "01"
    assert data["fp"].startswith("@") and len(data["fp"]) == 33


def test_custom_room():
    data = _parse(build_login("alice", "secret", room="custom-room"))
    assert data["r"] == "custom-room"


def test_password_never_plaintext():
    data = _parse(build_login("alice", "my-password"))
    assert data["p"] != "my-password"


def test_empty_credentials():
    data = _parse(build_login("", ""))
    assert data["n"] == ""
    assert data["p"] == hashlib.md5(b"").hexdigest()


def test_unicode_credentials():
    # ensure_ascii=False:非 ASCII 字符原样保留
    packet = build_login("小明", "密码123")
    assert "小明" in packet and "密码123" not in packet  # 密码只以 md5 出现
    data = _parse(packet)
    assert data["n"] == "小明"
    assert data["p"] == hashlib.md5("密码123".encode("utf-8")).hexdigest()


def test_special_chars_still_valid_json():
    data = _parse(build_login("a>b<c", 'q"uote\\'))
    assert data["n"] == "a>b<c"


def test_fingerprint_format():
    fp = _fingerprint()
    assert fp.startswith("@")
    body = fp[1:]
    assert len(body) == 32
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789" for c in body)


def test_fingerprint_random():
    assert _fingerprint() != _fingerprint()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
