"""anomaly 单元测试:基线不足放行、正常通过、系统性崩塌剔除、单指标尖峰放行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.anomaly import reject_reason

# 近 5 分钟基线:online≈150 chatting≈20 active≈30 away≈60 entering≈3
BASELINE = [
    {"online": 152, "chatting": 19, "active": 31, "away": 61, "entering": 2},
    {"online": 149, "chatting": 21, "active": 29, "away": 59, "entering": 4},
    {"online": 150, "chatting": 20, "active": 30, "away": 60, "entering": 3},
]

NORMAL = {"online": 151, "chatting": 22, "active": 28, "away": 58, "entering": 3}
COLLAPSE = {"online": 40, "chatting": 5, "active": 8, "away": 15, "entering": 0}


def test_insufficient_baseline_accepts():
    assert reject_reason(COLLAPSE, BASELINE[:2]) is None
    assert reject_reason(COLLAPSE, []) is None


def test_normal_sample_accepted():
    assert reject_reason(NORMAL, BASELINE) is None


def test_systemic_collapse_rejected():
    reason = reject_reason(COLLAPSE, BASELINE)
    assert reason is not None
    assert "online" in reason and "away" in reason  # ≥2 项违规


def test_single_metric_spike_accepted():
    # 只有 entering 尖峰(均值 3 → 15,偏差 12 > max(6, 3)):单项违规,放行
    spike = {**NORMAL, "entering": 15}
    assert reject_reason(spike, BASELINE) is None


def test_exactly_two_violations_rejected():
    two = {**NORMAL, "online": 45, "away": 20}  # online/away 双双崩塌
    reason = reject_reason(two, BASELINE)
    assert reason is not None and reason.count("=") >= 2


def test_custom_thresholds_override():
    # 收紧 online/chatting 阈值后,默认阈值下正常的候选变为异常
    cfg = {"thresholds": {"online": {"abs": 3, "rel": 0.01}, "chatting": {"abs": 3, "rel": 0.01}}}
    drifted = {"online": 145, "chatting": 24, "active": 30, "away": 60, "entering": 3}
    assert reject_reason(drifted, BASELINE, cfg) is not None
    assert reject_reason(drifted, BASELINE) is None  # 默认阈值下仍正常


def test_custom_min_samples():
    cfg = {"min_samples": 5}
    assert reject_reason(COLLAPSE, BASELINE, cfg) is None  # 基线不足,放行
