"""iirose WS 协议:帧解码、消息分割、快照解析。

参考实现:
- iirosebot ws_iirose/transfer_plugin.py(裸 ws 端点消息分割/续接规则)
- 网页客户端 messages_decoded.js(% 快照结构、u 频道)
"""
from __future__ import annotations

import gzip
import html
import re

# iirosebot check_start_symbols:消息以"非单词字符"开头视为新消息的起始符号
_START_SYMBOL_RE = re.compile(r"^(\W+)")
# u 频道消息头(u10/u11/u2…):u 是单词字符,单独识别,防止在批量帧中被误当续接
_U_MSG_RE = re.compile(r"^u(?:1[01]|2)")

# 房间条目特征:字段[0] 形如 13hex_13hex(用户条目的头像前缀多样,不可靠,
# 联机普查:http/https/cartoon/scenery/anime/system 等 → 用排除房间条目的方式分类)
_ROOM_ENTRY_RE = re.compile(r"^[\da-f]{13}_[\da-f]{13}$")

# 快照条目最小字段数(需要访问字段[11] 状态字符)
MIN_RECORD_FIELDS = 12


def decode_frame(data: bytes | str) -> str:
    """解一帧为文本:首字节 0x01 = gzip 压缩。

    websockets >= 14 对文本帧返回 str,旧版/二进制帧返回 bytes,两者都兼容。
    """
    if isinstance(data, str):
        return data
    if data[:1] == b"\x01":
        return gzip.decompress(data[1:]).decode("utf-8")
    return data.decode("utf-8")


def split_frame(text: str) -> list[str]:
    """一帧 → 若干逻辑消息。

    规则(对照 iirosebot process_message):
    - `%` 开头 = 快照大包,整帧一条,不参与分割;
    - `~` 开头 = 整帧一条;
    - 否则按 `<` 分割;片段以非单词字符开头 = 新消息(其前导符号即起始符号),
      否则为续接片段,拼接上一消息的起始符号;
    - u 频道消息头(u1*/u2)同样视为新消息起点,避免批量帧中被打散。
    """
    if text.startswith("%") or text.startswith("~"):
        return [text]
    pieces = text.split("<")
    if len(pieces) <= 1:
        return pieces
    messages: list[str] = []
    start_symbol = ""
    for piece in pieces:
        piece = html.unescape(piece)
        sym_match = _START_SYMBOL_RE.match(piece)
        u_match = _U_MSG_RE.match(piece)
        if sym_match or u_match:
            start_symbol = u_match.group(0) if u_match else sym_match.group(1)
            messages.append(piece)
        elif start_symbol and messages:
            messages.append(start_symbol + piece)
        else:
            messages.append(piece)
    return messages


def parse_snapshot(frame_text: str) -> list[list[str]]:
    """解析 `%` 快照帧 → 用户记录列表(每条为 `>` 分字段的 list)。

    帧结构(对照网页客户端 freshRoom):`%*"` + payload;
    payload 按 `'` 分两段:g[0] = 全站用户(`<` 分隔记录),g[1] = 房间树(忽略)。
    无用户段(空快照)返回 []。
    """
    idx = frame_text.find('"')
    if idx == -1:
        return []
    users_blob = frame_text[idx + 1 :].split("'", 1)[0]
    records: list[list[str]] = []
    for piece in users_blob.split("<"):
        if not piece:
            continue
        fields = piece.split(">")
        if len(fields) < MIN_RECORD_FIELDS:
            continue
        if _ROOM_ENTRY_RE.match(fields[0]):
            continue  # 房间条目(正常情况下出现在 g[1],这里只是兜底)
        # 字段级反转义(分割在反转义之前,避免内容中的 &gt; 破坏字段边界)
        records.append([html.unescape(f) for f in fields])
    return records


def parse_join_record(payload: str) -> list[str] | None:
    """解析 u11 加入消息的载荷(`>` 分字段记录,同快照条目)。

    payload 可能带前导 `<`(帧分割残留),先剥掉。
    """
    payload = payload.lstrip("<")
    fields = payload.split(">")
    if len(fields) < MIN_RECORD_FIELDS:
        return None
    return [html.unescape(f) for f in fields]


def check_login_error(frame_text: str) -> tuple[str, str] | None:
    """检测 `%*"` 前缀的登录错误码。

    成功快照 = `%*"` 后跟非数字非 x 的载荷(首位是用户条目头像的 h/c);
    错误 = 后跟数字或 x。返回 (code, 说明),正常返回 None。
    """
    if not frame_text.startswith('%*"'):
        return None
    code = frame_text[3:4]
    if code.isdigit() or code == "x":
        messages = {
            "0": "名字已被占用",
            "1": "用户不存在",
            "2": "密码错误",
            "4": "当日登录尝试次数超限",
            "5": "房间密码错误",
            "x": "账号被封禁",
        }
        return code, messages.get(code, f"未知错误码 {code}")
    return None
