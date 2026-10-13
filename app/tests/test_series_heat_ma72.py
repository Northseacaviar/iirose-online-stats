"""热度 72 小时滑动平均(heat_ma72)测试。

口径:
- heat_ma72 = 该样本往前 72 小时窗口内 heat 的算术平均(闭区间,含当前点);
- 窗口只吃 >= 数据起点(配置 data_start)的样本 —— 早于起点的样本不显示、也不参与统计;
- 窗口只吃 heat 非空的样本(早期迁移过来的历史样本这一列是空的,拿 None 求和会 TypeError);
- 窗口不完整(窗口内样本数或覆盖时长不足满窗 96%)时给 None,前端据此把线断开。
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp.test_utils import TestClient, TestServer

from storage.db import Database
from web.server import create_app

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
STEP = 600                      # 线上采样节拍(秒)
HOURS72 = 72 * 3600
FMT = "%Y-%m-%dT%H:%M:%S"
DATA_START = "2026-10-01T00:00:00"
FULL_COUNT = HOURS72 // STEP + 1        # 433:满窗样本数(含当前点)


def _client(db: Database, data_start: str | None = None, interval: int | None = STEP) -> TestClient:
    # interval_seconds 必须与造数节拍一致:它是"满窗应有多少条"的基准
    return TestClient(TestServer(create_app(
        db, WEB_DIR, interval_seconds=interval, data_start=data_start)))


def _seed(db: Database, first: str, count: int, drop: tuple[int, ...] = (),
          heat=None, online=None, step: int = STEP) -> None:
    """从 first 起每 step 秒插一条;heat 缺省 400.0,可用函数给每条单独定值。"""
    t0 = datetime.strptime(first, FMT)
    for i in range(count):
        if i in drop:
            continue
        h = 400.0 if heat is None else heat(i)
        db.insert_sample(
            (t0 + timedelta(seconds=step * i)).strftime(FMT),
            (100 + i) if online is None else online(i), 98, 10, 20, 30, h,
        )


def _manual_heat_mean(db: Database, last_ts: str,
                      data_start: str = DATA_START) -> tuple[float, int]:
    """独立对照实现:库里全量样本中,落在 [last_ts-72h, last_ts] 的 heat 均值与条数。

    刻意不复用被测代码的滑窗逻辑 —— 走全量列表推导,能抓出取窗边界写错。
    造数只用 0.5 / 0.25 这类二进制精确值,求和顺序差异不会让两次结果差最后一位,
    因此断言可以用等号而不是近似。
    **必须带上数据起点过滤**(与读侧口径同源):漏了它,窗口刚好跨过起点的那些点会
    被算进「起点之前的历史」,而实现那边是拿不到的 —— 那种断言会靠巧合通过
    (2026-10-04T00:00 那点的 72h 窗口恰好与起点之后完全重合,10-03T00:00 就不重合)。
    """
    hi = datetime.strptime(last_ts, FMT)
    lo = hi - timedelta(seconds=HOURS72)
    vals = [
        r["heat"] for r in db.query(data_start)
        if r["heat"] is not None and lo <= datetime.strptime(r["ts"], FMT) <= hi
    ]
    return round(sum(vals) / len(vals), 1), len(vals)


def test_ma72_full_window_matches_manual_average(tmp_path):
    """满窗样本的 heat_ma72 等于手算的 72 小时均值。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)          # 83.3 小时
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?range=all")).json()
        rows = body["samples"]
        expect, n = _manual_heat_mean(db, rows[-1]["ts"])
        # 参照实现的窗口条数:证明这条样本确实满窗,否则下面的均值比对没有意义
        # (含当前点:72h / 10min = 432 个间隔 → 433 条)
        assert n == FULL_COUNT, n
        assert rows[-1]["heat_ma72"] == expect
        # 抽查中间一条(index 499 往前 20 条,仍满窗):值取自己做上界的均值
        mid = 500 - 1 - 20
        assert rows[mid]["heat_ma72"] == _manual_heat_mean(db, rows[mid]["ts"])[0]
    asyncio.run(run())


def test_ma72_none_until_window_is_full(tmp_path):
    """开头不满窗口的点给 None;跨过 96% 容差线那一条起才有值。

    容差把"满窗"从 433 条放宽到 414.72 条,所以判据落在第 415 条(10/3 21:00),
    不是第 433 条 —— 这一点和 24h 均线同源(线上采样抖动 9~12 分钟)。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        by_ts = {r["ts"]: r["heat_ma72"] for r in rows}
        assert by_ts["2026-10-01T00:00:00"] is None      # 库里第一条
        assert by_ts["2026-10-02T00:00:00"] is None      # 24 小时,远不满
        assert by_ts["2026-10-03T12:00:00"] is None      # 60 小时,仍不满
        assert by_ts["2026-10-03T20:50:00"] is None      # 414 条,压在容差线(414.72)之下
        assert by_ts["2026-10-03T21:00:00"] is not None  # 415 条,过线
        assert by_ts["2026-10-04T00:00:00"] is not None  # 真满窗(433 条)
    asyncio.run(run())


def test_ma72_window_excludes_samples_before_data_start(tmp_path):
    """数据起点之前的样本不参与窗口:起点把可用历史截断后,窗口就得重新等满 72 小时。"""
    async def run():
        db = Database(tmp_path / "t.db")
        # 库里 9/28 起就有数据,但数据起点设在 10/1 —— 9/28–9/30 那段是被隐藏的试跑数据
        _seed(db, "2026-09-28T00:00:00", 900)
        async with _client(db, "2026-10-01T00:00:00") as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows[0]["ts"] == "2026-10-01T00:00:00"
        assert rows[0]["heat_ma72"] is None              # 起点处窗口里一条样本都没有
        # 这个点最关键:72h 窗口整个跨过起点(10-03T00:00 往前到 09-30T00:00),
        # 若把起点之前的样本也算成窗口材料,它会算出一个均值 —— 必须是 None
        cross = next(r for r in rows if r["ts"] == "2026-10-03T00:00:00")
        expect, n = _manual_heat_mean(db, "2026-10-03T00:00:00")
        assert n == 289, n            # 起点之后只有 289 条(把起点前那 144 条算进来才是 433)
        assert n < 415                # 远不满 72h 判据线 → 实现给 None 是对的
        assert cross["heat_ma72"] is None
        assert next(r for r in rows if r["ts"] == "2026-10-03T20:50:00")["heat_ma72"] is None
        tail = next(r for r in rows if r["ts"] == "2026-10-04T00:00:00")
        # 10/4 0 点:窗口内 433 条(全在起点之后)→ 有值
        assert tail["heat_ma72"] == _manual_heat_mean(db, "2026-10-04T00:00:00")[0]
    asyncio.run(run())


def test_ma72_none_when_historical_gap_inside_window(tmp_path):
    """窗口里挖掉 8 小时(48 条,>4% 容差)后平均不可信 —— 给 None。"""
    async def run():
        db = Database(tmp_path / "t.db")
        # 缺口必须落在末尾点的窗口 [67, 499] 内,否则测的不是窗口完整性
        _seed(db, "2026-10-01T00:00:00", 500, drop=tuple(range(400, 448)))
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows[-1]["heat_ma72"] is None
        # 缺口之前的点本来就不满窗(历史不足 72 小时),不重复断言;恢复点要等窗口
        # 整体走过缺口(第 880 条之后),库里没这么多样本 —— 见容差用例的正面判据
    asyncio.run(run())


def test_ma72_tolerates_a_few_missing_samples(tmp_path):
    """容差口径:窗口里少几条(采样抖动造成的)仍算满窗,不该凭空断线。

    线下 72 小时窗口满窗 433 条,判据线 415 条(96%)。缺 10 条仍须有值;
    缺 30 条(→403)超出容差才给 None。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500, drop=tuple(range(410, 420)))
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows[-1]["heat_ma72"] is not None         # 423 条,仍在容差内

        db2 = Database(tmp_path / "t2.db")
        _seed(db2, "2026-10-01T00:00:00", 500, drop=tuple(range(410, 440)))
        async with _client(db2, DATA_START) as cli:
            rows2 = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows2[-1]["heat_ma72"] is None            # 403 条,超出容差
    asyncio.run(run())


def test_ma72_none_when_span_too_short(tmp_path):
    """条数够但时间跨度不够(实际节拍比配置密)时,同样判为窗口不完整。

    造 60 小时、每 60 秒一条(3601 条),配置节拍按 600 秒 —— 条数判据会通过
    (3601 远超满窗 415 条),但跨度只有 60 小时 < 72×0.96,必须为 None。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 3601, step=60)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert len(rows) == 3601
        assert all(r["heat_ma72"] is None for r in rows)
    asyncio.run(run())


def test_ma72_keeps_one_decimal(tmp_path):
    """均值保留一位小数(热度本身已是实数,均线仍要统一到一位)。

    全用 400.0 造数时任何窗口均值都是整数,这条口径就钉不住。这里让 heat 在
    400 / 400.5 之间交替:433 条的窗口里两种值各占一半,均值必带小数。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500, heat=lambda i: 400.0 + (i % 2) * 0.5)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        v = rows[-1]["heat_ma72"]
        assert v == _manual_heat_mean(db, rows[-1]["ts"])[0]
        assert v != round(v)          # 真带小数,不是被 round 成整数
        assert round(v, 1) == v       # 且只留一位
    asyncio.run(run())


def test_ma72_ignores_rows_with_null_heat(tmp_path):
    """heat 为空的历史样本:不参与求和,也不占用窗口的条数与跨度。

    真实场景:samples 表在早期版本没有 heat 列,迁移时不伪造数据、留空。这些行
    拿 None 求和会直接 TypeError,只能当它不存在(与坏时间戳同一处理)——
    但"不存在"不等于"缺口":中间插一条空值不该把 72 小时窗口判成不完整。
    空值那条自己没有热度,它的 heat_ma72 也给 None(与 heat 线一起断开)。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)            # 末尾样本 10/4 11:10
        # ① 系列中间插一条:它落在末尾点的窗口内,是"空值不等于缺口"的判据
        db.insert_sample("2026-10-03T12:00:03", 150, 148, 10, 20, 30, None)
        # ② 末尾之后插一条:此时窗口已满,它的 None 只可能来自 heat 为空本身
        db.insert_sample("2026-10-04T12:00:03", 151, 149, 10, 20, 30, None)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        tail = rows[-1]
        assert tail["ts"] == "2026-10-04T12:00:03"
        assert tail["heat"] is None
        assert tail["heat_ma72"] is None                 # 自己没有热度 → 均线同样为空
        mid_blank = next(r for r in rows if r["ts"] == "2026-10-03T12:00:03")
        assert mid_blank["heat_ma72"] is None
        # 窗口内少这 1 条不影响判据:末条样本(10/4 11:10)的窗口仍算满窗
        last_seeded = next(r for r in rows if r["ts"] == "2026-10-04T11:10:00")
        assert last_seeded["heat_ma72"] == 400.0         # 432 条,均值仍是 400
    asyncio.run(run())


def test_ma72_all_none_when_every_heat_is_null(tmp_path):
    """整库 heat 都为空(全是迁移前的历史):不做除法,一律 None,不许 500。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500, heat=lambda i: None)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?range=all")
            assert resp.status == 200, resp.status
            rows = (await resp.json())["samples"]
        assert len(rows) == 500
        assert all(r["heat_ma72"] is None for r in rows)
        assert rows[-1]["online_ma24"] is not None       # 人数线不受 heat 为空影响
    asyncio.run(run())


def test_ma72_present_on_every_returned_row(tmp_path):
    """返回的每条样本都带 heat_ma72 键(前端按它画线,缺键会画成断线)。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)            # 83 小时
        # 另接一段贴到"现在"的数据:预设 range=24h 是相对当前时间算的,只有过去日期会返回空表
        now = datetime.now().replace(second=0, microsecond=0)
        _seed(db, (now - timedelta(hours=120)).strftime(FMT), 721)
        async with _client(db, DATA_START) as cli:
            for q in ("range=all", "range=7d", "range=24h", "start=2026-10-01&end=2026-10-02"):
                rows = (await (await cli.get(f"/api/series?{q}")).json())["samples"]
                assert rows, q
                assert all("heat_ma72" in r for r in rows), q
                assert all("online_ma24" in r for r in rows), q
            # range=24h:取窗前移 72 小时,所以窗口内每一条的 72h 窗口都是满的
            rows = (await (await cli.get("/api/series?range=24h")).json())["samples"]
            assert all(r["heat_ma72"] is not None for r in rows)
    asyncio.run(run())


def test_ma72_for_custom_range_uses_out_of_range_history(tmp_path):
    """自定义区间:窗口要能吃区间之前 72 小时的数据,但那些样本本身不返回。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 900)            # 150 小时
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?start=2026-10-06&end=2026-10-06")).json()
        rows = body["samples"]
        assert rows[0]["ts"] == "2026-10-06T00:00:00"    # 10/6 之前的样本不返回
        assert len(rows) == 144                          # 10/6 整天
        # 区间之外确实攒够 72 小时的历史:否则「窗口能吃区间外数据」这条根本没被验到
        pre = [r for r in db.query(DATA_START) if r["ts"] < "2026-10-06T00:00:00"]
        assert len(pre) >= FULL_COUNT
        assert rows[0]["heat_ma72"] == _manual_heat_mean(db, "2026-10-06T00:00:00")[0]
        assert all(r["heat_ma72"] is not None for r in rows)
    asyncio.run(run())


def test_ma72_tolerates_unparsable_timestamp(tmp_path):
    """库里混进格式不对的时间戳:不能整个请求 500,跳过那条、其余照常返回。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)
        # 空格分隔(不是 T 分隔)—— 排在数据起点之后、会被查出来,但 strptime 解析不了
        db.insert_sample("2026-10-05 12:00:00", 111, 109, 10, 20, 30, 400.0)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?range=all")
            assert resp.status == 200, resp.status
            rows = (await resp.json())["samples"]
        assert "2026-10-05 12:00:00" not in [r["ts"] for r in rows]
        assert len(rows) == 500
        assert rows[-1]["heat_ma72"] is not None
    asyncio.run(run())


def test_ma72_on_empty_table(tmp_path):
    """空库:200 + 空数组(不是 500)。"""
    async def run():
        db = Database(tmp_path / "t.db")
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?range=all")
            assert resp.status == 200, resp.status
            assert (await resp.json())["samples"] == []
    asyncio.run(run())


def test_ma72_single_point_key_present_but_null(tmp_path):
    """库里只有一条:键必须在(前端按它建线),值必然是 None(窗口不满)。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 1)
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?range=all")).json()
        assert len(body["samples"]) == 1
        assert body["interval_seconds"] == STEP
        row = body["samples"][0]
        assert row["heat_ma72"] is None and row["online_ma24"] is None
    asyncio.run(run())


def test_ma72_survives_bad_interval_seconds(tmp_path):
    """interval_seconds 配成 0 / 负数 / None:不许抛异常。

    step 兜底 60 秒 → 满窗要 2592 条,库里 500 条远不够,所以一律 None
    (这是已知取舍:配置写小了均线会静默整条消失,与 24h 均线同源)。
    """
    async def run():
        for bad in (0, -5, None):
            db = Database(tmp_path / f"t{bad}.db")
            _seed(db, "2026-10-01T00:00:00", 500)
            async with _client(db, DATA_START, interval=bad) as cli:
                resp = await cli.get("/api/series?range=all")
                assert resp.status == 200, (bad, resp.status)
                rows = (await resp.json())["samples"]
            assert len(rows) == 500
            assert all(r["heat_ma72"] is None for r in rows), bad
    asyncio.run(run())


def test_ma72_without_data_start_uses_whole_table(tmp_path):
    """不设数据起点(data_start=None):窗口材料就是全表,从库里第 415 条起才有值。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)
        async with _client(db, None) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        by_ts = {r["ts"]: r["heat_ma72"] for r in rows}
        assert by_ts["2026-10-03T20:50:00"] is None      # 414 条,压在判据线下
        assert by_ts["2026-10-03T21:00:00"] == 400.0     # 415 条,过线
    asyncio.run(run())
