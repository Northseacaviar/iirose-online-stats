"""入库样本异常检测:偏离自身近 window_seconds 内均值的样本剔除。

动机:浏览器脚本更新(页面重载瞬间用户列表只加载了一部分)、站点断连恢复等
会造成瞬时异常数据。与自身近 5 分钟均值偏差过大的样本不进库。

规则:
- 基线 = 近 window_seconds 内已入库样本(≥ min_samples 条才判断,否则放行);
- 每项指标 |候选值 - 均值| > max(abs, rel × 均值) 记一次违规;
- ≥ 2 项指标同时违规才剔除 —— 单指标偶发抖动(如 entering 尖峰)属正常,
  脚本更新/断连类故障通常是多项同时崩塌。
"""
from __future__ import annotations

METRICS = ("online", "chatting", "active", "away", "entering")

DEFAULT_CONFIG: dict = {
    "window_seconds": 300,
    "min_samples": 3,
    "thresholds": {
        "online": {"abs": 15, "rel": 0.35},
        "chatting": {"abs": 12, "rel": 0.6},
        "active": {"abs": 12, "rel": 0.6},
        "away": {"abs": 15, "rel": 0.35},
        "entering": {"abs": 6, "rel": 1.0},
    },
}


def reject_reason(
    candidate: dict, recent: list[dict], config: dict | None = None
) -> str | None:
    """candidate 相对 recent(近窗口内已入库样本,时间升序)是否异常。

    异常返回违规说明(写入日志/接口响应),正常返回 None。
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    thresholds = {**DEFAULT_CONFIG["thresholds"], **(cfg.get("thresholds") or {})}
    if len(recent) < cfg["min_samples"]:
        return None  # 基线不足(冷启动/长时间断连后首条),不判断
    violations = []
    for key in METRICS:
        th = thresholds.get(key)
        if th is None:
            continue
        mean = sum(r[key] for r in recent) / len(recent)
        dev = abs(candidate[key] - mean)
        if dev > max(th["abs"], th["rel"] * mean):
            violations.append(f"{key}={candidate[key]}(均值{mean:.0f},偏差{dev:.0f})")
    if len(violations) >= 2:
        return "; ".join(violations)
    return None
