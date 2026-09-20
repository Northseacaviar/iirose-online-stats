"""登录包构建。

联机实测(2026-09-17):
- wss://m.iirose.com:443 接受注册账号登录,格式与 iirosebot 一致;
- 游客账号需经网页"新的开始"流程创建(纯 WS 无法注册,返回 %*"1);
- 空间站房间 ID 5ce6a4b520a90 恒可用(统计是全站的,房间不影响数据)。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import string

DEFAULT_ROOM = "5ce6a4b520a90"  # 空间站


def _fingerprint() -> str:
    return "@" + "".join(
        secrets.choice(string.ascii_lowercase + string.digits) for _ in range(32)
    )


def build_login(username: str, password: str, room: str = DEFAULT_ROOM) -> str:
    """注册账号登录包:`*` + JSON(md5 密码)。

    字段对照(网页客户端实测,与站点登录流程一致):
    - r  房间 ID(全站统计,任意可用房间均可)
    - n  用户名
    - p  密码的 md5 十六进制(站点协议要求,网页登录同款)
    - st "n" 表示账号登录
    - mo / mb 空串(未知用途,网页客户端同样为空)
    - mu "01"(未知用途,网页客户端同样为 01)
    - fp "@"+32 位随机小写字母数字(实测站点接受任意随机值)
    """
    packet = {
        "r": room,
        "n": username,
        "p": hashlib.md5(password.encode("utf-8")).hexdigest(),
        "st": "n",
        "mo": "",
        "mb": "",
        "mu": "01",
        "fp": _fingerprint(),
    }
    return "*" + json.dumps(packet, separators=(",", ":"), ensure_ascii=False)
