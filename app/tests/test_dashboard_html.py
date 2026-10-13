"""仪表盘 dashboard.html 的结构不变量(文本级)。

为什么用文本断言:仓库里没有任何加载 dashboard.html 的测试环境(只有 collector.js
的 node 测试),而它有一处踩过的坑 —— `SERIES` 是扇出源(图表/瓦片/明细表共用),
但几处键清单是**手写**的,靠人工同步:漏一处页面就只显示「刷新失败」,而数据其实全对
(派生指标取不到 `v-heat_ma72` 元素 → TypeError 被 refresh 最外层 catch 吞掉)。

这些断言抓的就是「加了系列却忘了改消费方」这类回归。改动 dashboard.html 时它们一起跑,
不必开浏览器。
"""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "web" / "dashboard.html").read_text(
    encoding="utf-8"
)


def _series_block() -> str:
    m = re.search(r"const SERIES = \[(.*?)\n\];", HTML, re.S)
    assert m, "dashboard.html 里找不到 SERIES 数组"
    return m.group(1)


def _series_entries() -> list[tuple[str, bool]]:
    """[(key, 是否派生指标)] —— 按条目花括号切,注释里的 key 不算。"""
    entries: list[tuple[str, bool]] = []
    for m in re.finditer(r"\{([^{}]*)\}", _series_block(), re.S):
        body = m.group(1)
        key = re.search(r'key:\s*"([A-Za-z0-9_]+)"', body)
        if key:
            entries.append((key.group(1), bool(re.search(r"derived:\s*true", body))))
    return entries


def _series_keys() -> set[str]:
    return {k for k, _ in _series_entries()}


def test_series_entries_parsed():
    """自检:解析出来的系列数不少于 8(否则下面的断言会因解析失败而空转)。"""
    assert len(_series_entries()) >= 8
    assert "online_ma24" in _series_keys() and "heat_ma72" in _series_keys()


def test_pad_gaps_null_point_carries_every_series_key():
    """padGaps 补出来的空点必须带全部系列键 —— 缺一个该线就在缺口处丢点。"""
    m = re.search(r"out\.push\(\{\s*ts:[^}]*\}\)", HTML)
    assert m, "dashboard.html 里找不到 padGaps 补空点那行"
    keys = set(re.findall(r"([A-Za-z0-9_]+):", m.group(0))) - {"ts"}
    assert keys == _series_keys(), keys ^ _series_keys()


def test_every_non_derived_series_has_a_value_tile():
    """每个非派生系列都要有对应的 v-<key> 瓦片元素,否则瓦片循环取 DOM 时抛异常。"""
    tile_ids = set(re.findall(r'id="v-([A-Za-z0-9_]+)"', HTML))
    assert tile_ids == {k for k, derived in _series_entries() if not derived}


def test_tiles_and_table_skip_derived_series():
    """瓦片与明细表两处 DOM 循环都要过滤派生指标(只有图表画均线)。"""
    assert HTML.count("SERIES.filter((x) => !x.derived)") == 2


def test_right_axis_visibility_considers_all_axis1_series():
    """右轴显隐要判「右轴上还有可见线」,不能只看热度一条线。"""
    assert "show: SERIES.some((s) => (s.axis || 0) === 1 && !hiddenKeys.has(s.key))" in HTML


def test_every_series_slot_is_defined_in_all_three_palettes():
    """SERIES 用到的每个颜色变量都要在浅色/跟随系统深色/手动深色三套里定义齐。"""
    slots = {s for s in re.findall(r'slot:\s*"(--series-[0-9]+)"', _series_block())}
    assert slots, "SERIES 里没解析到 slot"
    for slot in sorted(slots):
        assert HTML.count(f"{slot}:") == 3, (slot, HTML.count(f"{slot}:"))
