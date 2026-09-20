"""run.py 配置加载兜底:深合并与数值校验。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run import _deep_merge, _num, load_config


def test_deep_merge_keeps_default_nested_keys():
    merged = _deep_merge(
        {"ws": {"enabled": False, "hosts": ["a"], "port": 443}},
        {"ws": {"port": 8443}},
    )
    assert merged["ws"]["port"] == 8443
    assert merged["ws"]["hosts"] == ["a"]
    assert merged["ws"]["enabled"] is False


def test_load_config_partial_yaml(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("ws:\n  port: 8443\n", encoding="utf-8")
    config = load_config(cfg_path)
    assert config["ws"]["port"] == 8443
    assert config["ws"]["hosts"]  # 默认主机保留
    assert config["ws"]["enabled"] is False  # 默认网页脚本模式


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config["interval_seconds"] == 60
    assert config["ws"]["enabled"] is False


def test_num_validation():
    assert _num("60", 60.0, 1.0) == 60.0
    assert _num(0, 60.0, 1.0) == 60.0       # ≤0 退回默认
    assert _num(-5, 60.0, 1.0) == 60.0
    assert _num("garbage", 60.0, 1.0) == 60.0
    assert _num(0, 10.0, 0.0) == 0.0        # 允许 0 的场景(常驻模式)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
