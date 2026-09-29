"""anomaly 单元测试:基线不足放行、正常通过、系统性崩塌剔除、单指标尖峰放行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.anomaly import DEFAULT_CONFIG, reject_reason

# 近 1 小时基线(10 分钟一条):online≈150 chatting≈20 active≈30 away≈60
BASELINE = [
    {"online": 152, "chatting": 19, "active": 31, "away": 61},
    {"online": 149, "chatting": 21, "active": 29, "away": 59},
    {"online": 150, "chatting": 20, "active": 30, "away": 60},
]

NORMAL = {"online": 151, "chatting": 22, "active": 28, "away": 58}
COLLAPSE = {"online": 40, "chatting": 5, "active": 8, "away": 15}


def test_window_covers_several_samples_at_600s():
    """采样间隔 600 秒时,窗口必须 ≥ min_samples 个间隔,否则判定永不触发。"""
    assert DEFAULT_CONFIG["window_seconds"] >= 600 * DEFAULT_CONFIG["min_samples"]


def test_derived_metrics_not_judged():
    """real / heat 不在判定项里:与 online / chatting 共线,加进去只会搅浑门槛。"""
    assert "real" not in DEFAULT_CONFIG["thresholds"]
    assert "heat" not in DEFAULT_CONFIG["thresholds"]


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
    # 只有 chatting 尖峰(均值 20 → 35,偏差 15 > max(12, 12)):单项违规,放行
    spike = {**NORMAL, "chatting": 35}
    assert reject_reason(spike, BASELINE) is None


def test_extra_metrics_do_not_affect_judgement():
    """候选样本多带 real/heat 字段(实际入库形状)时结论不变。"""
    weird = {**NORMAL, "real": 0, "heat": 99999.0}
    assert reject_reason(weird, BASELINE) is None


def test_exactly_two_violations_rejected():
    two = {**NORMAL, "online": 45, "away": 20}  # online/away 双双崩塌
    reason = reject_reason(two, BASELINE)
    assert reason is not None and reason.count("=") >= 2


def test_custom_thresholds_override():
    # 收紧 online/chatting 阈值后,默认阈值下正常的候选变为异常
    cfg = {"thresholds": {"online": {"abs": 3, "rel": 0.01}, "chatting": {"abs": 3, "rel": 0.01}}}
    drifted = {"online": 145, "chatting": 24, "active": 30, "away": 60}
    assert reject_reason(drifted, BASELINE, cfg) is not None
    assert reject_reason(drifted, BASELINE) is None  # 默认阈值下仍正常


def test_custom_min_samples():
    cfg = {"min_samples": 5}
    assert reject_reason(COLLAPSE, BASELINE, cfg) is None  # 基线不足,放行


def test_incomplete_threshold_skipped():
    """阈值配置残缺(缺 rel)时跳过该指标,不因配置写坏而崩溃。"""
    cfg = {"thresholds": {"online": {"abs": 3}, "chatting": {"abs": 3}}}
    # online 偏差约 5、chatting 偏差 4:两项本会各自违规,但阈值残缺 → 跳过 → 放行
    drifted = {"online": 145, "chatting": 24, "active": 30, "away": 60}
    assert reject_reason(drifted, BASELINE, cfg) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
