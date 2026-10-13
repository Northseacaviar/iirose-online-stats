"""24 小时滑动平均(online_ma24)测试。

口径:
- online_ma24 = 该样本往前 24 小时窗口内 online 的算术平均(闭区间,含当前点);
- 窗口只吃 >= 数据起点(配置 data_start)的样本 —— 早于起点的样本不显示、也不参与统计;
- 窗口不完整(窗口内样本数或覆盖时长不足满窗 98%)时给 None,前端据此把线断开。
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
FMT = "%Y-%m-%dT%H:%M:%S"
DATA_START = "2026-10-01T00:00:00"


def _client(db: Database, data_start: str | None = None, interval: int = STEP) -> TestClient:
    # interval_seconds 必须与造数节拍一致:它是"满窗应有多少条"的基准
    return TestClient(TestServer(create_app(
        db, WEB_DIR, interval_seconds=interval, data_start=data_start)))


def _seed(db: Database, first: str, count: int, drop: tuple[int, ...] = (),
          online=None, step: int = STEP) -> None:
    """从 first 起每 step 秒插一条,online(i) 缺省 100+i(便于手算均值);drop 制造缺口。"""
    t0 = datetime.strptime(first, FMT)
    for i in range(count):
        if i in drop:
            continue
        v = (100 + i) if online is None else online(i)
        db.insert_sample(
            (t0 + timedelta(seconds=step * i)).strftime(FMT), v, v - 2, 10, 20, 30, 400.0
        )


def _manual_mean(db: Database, last_ts: str) -> tuple[float, int]:
    """独立对照实现:库里全量样本中,落在 [last_ts-24h, last_ts] 的 online 均值与条数。

    刻意不复用被测代码的滑窗逻辑 —— 走全量列表推导,能抓出取窗边界写错。
    """
    hi = datetime.strptime(last_ts, FMT)
    lo = hi - timedelta(seconds=86400)
    vals = [
        r["online"] for r in db.query(None)
        if lo <= datetime.strptime(r["ts"], FMT) <= hi
    ]
    return round(sum(vals) / len(vals), 1), len(vals)


def test_ma24_full_window_matches_manual_average(tmp_path):
    """满窗样本的 online_ma24 等于手算的 24 小时均值。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200)  # 33 小时
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?range=all")).json()
        rows = body["samples"]
        last = rows[-1]["ts"]
        expect, n = _manual_mean(db, last)
        assert n == 145, n                      # 含当前点:24h / 10min = 144 个间隔 → 145 条
        assert rows[-1]["online_ma24"] == expect
        # 抽查中间一条:窗口同样满,值取自己做上界的均值
        mid = 200 - 1 - 12                      # 从末尾往前 12 个样本(仍满窗)
        assert rows[mid]["online_ma24"] == _manual_mean(db, rows[mid]["ts"])[0]
    asyncio.run(run())


def test_ma24_none_until_window_is_full(tmp_path):
    """开头不满 24 小时的点给 None;满 24 小时那一刻起才有值。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        by_ts = {r["ts"]: r["online_ma24"] for r in rows}
        assert by_ts["2026-10-01T00:00:00"] is None      # 库里第一条
        assert by_ts["2026-10-01T12:00:00"] is None      # 窗口内 73 条,远不满
        assert by_ts["2026-10-02T00:00:00"] is not None  # 满 24 小时
    asyncio.run(run())


def test_ma24_window_excludes_samples_before_data_start(tmp_path):
    """数据起点之前的样本不参与窗口:起点把可用历史截断后,窗口就得重新等满 24 小时。"""
    async def run():
        db = Database(tmp_path / "t.db")
        # 库里 9/29 起就有数据,但数据起点设在 10/1 —— 9/29–9/30 那段是被隐藏的试跑数据
        _seed(db, "2026-09-29T12:00:00", 400)
        async with _client(db, "2026-10-01T00:00:00") as cli:
            body = await (await cli.get("/api/series?start=2026-10-01&end=2026-10-02")).json()
            rows = body["samples"]
        assert rows[0]["ts"] == "2026-10-01T00:00:00"
        # 只看起点之后的样本:10/1 0 点到 10/1 12 点 = 73 条 < 满窗 → None
        assert rows[0]["online_ma24"] is None
        assert next(r for r in rows if r["ts"] == "2026-10-01T12:00:00")["online_ma24"] is None
        # 10/2 0 点:窗口内 145 条(全在起点之后)→ 有值,且等于起点后那批的均值
        tail = next(r for r in rows if r["ts"] == "2026-10-02T00:00:00")
        expect, n = _manual_mean(db, "2026-10-02T00:00:00")
        assert n == 145
        assert tail["online_ma24"] == expect
    asyncio.run(run())


def test_ma24_none_when_historical_gap_inside_window(tmp_path):
    """窗口里挖掉 3 小时(18 条)后平均不可信 —— 给 None,不给"只用了 21 小时"的数。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200, drop=tuple(range(150, 168)))  # 第 150–167 条缺
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        gap_ts = [r["ts"] for r in rows]
        # 末尾点(第 199 条)的窗口含整段缺口
        assert rows[-1]["online_ma24"] is None
        # 缺口完全落在窗口之外后才恢复(第 168 条之后再走 24 小时 = 第 312 条以后)
        assert gap_ts[0] == "2026-10-01T00:00:00"
    asyncio.run(run())


def test_ma24_skips_leading_rows_for_partial_history(tmp_path):
    """库里历史不足 24 小时:所有点都是 None,而不是拿几个点算出一个假均值。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 50)  # 只有 8.3 小时
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows and all(r["online_ma24"] is None for r in rows)
    asyncio.run(run())


def test_ma24_for_custom_range_uses_out_of_range_history(tmp_path):
    """自定义区间:窗口要能吃区间之前的数据,但那些样本本身不返回。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 500)  # 83 小时
        async with _client(db, DATA_START) as cli:
            body = await (await cli.get("/api/series?start=2026-10-03&end=2026-10-03")).json()
        rows = body["samples"]
        assert rows[0]["ts"] == "2026-10-03T00:00:00"   # 10/3 之前的样本不返回
        assert len(rows) == 144                          # 10/3 整天
        assert rows[0]["online_ma24"] == _manual_mean(db, "2026-10-03T00:00:00")[0]
        assert all(r["online_ma24"] is not None for r in rows)
    asyncio.run(run())


def test_ma24_present_on_every_returned_row(tmp_path):
    """返回的每条样本都带 online_ma24 键(前端按它画线,缺键会画成断线)。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200)
        # 另接一段贴到"现在"的数据:预设 range=24h 是相对当前时间算的,只有过去日期会返回空表
        now = datetime.now().replace(second=0, microsecond=0)
        _seed(db, (now - timedelta(hours=60)).strftime(FMT), 361)
        async with _client(db, DATA_START) as cli:
            for q in ("range=all", "range=24h", "start=2026-10-01&end=2026-10-02"):
                rows = (await (await cli.get(f"/api/series?{q}")).json())["samples"]
                assert rows, q
                assert all("online_ma24" in r for r in rows), q
            rows = (await (await cli.get("/api/series?range=24h")).json())["samples"]
            assert any(r["online_ma24"] is not None for r in rows)  # 近 24h 的窗口是满的
    asyncio.run(run())


def test_ma24_tolerates_a_few_missing_samples(tmp_path):
    """容差口径:窗口里少几条(采样抖动造成的)仍算满窗,不该凭空断线。

    线上满窗实测是 143~145 条(9~12 分钟抖动),判据不能卡"一条不少"——
    卡死会把正常的 143 条窗口判成不完整,实测凭空多出 148 个断口。
    这里缺 5 条(145→140)仍须有值;缺到容差之外(缺 20 条→125)才给 None。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        # 缺口必须落在被测点(末尾 index 399)的窗口 [255, 399] 内,否则测的不是容差
        _seed(db, "2026-10-01T00:00:00", 400, drop=(300, 301, 302, 303, 304))
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows[-1]["online_ma24"] is not None     # 140 条,仍在容差内

        db2 = Database(tmp_path / "t2.db")
        _seed(db2, "2026-10-01T00:00:00", 400, drop=tuple(range(300, 320)))
        async with _client(db2, DATA_START) as cli:
            rows2 = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert rows2[-1]["online_ma24"] is None        # 125 条,超出容差
    asyncio.run(run())


def test_ma24_none_when_span_too_short(tmp_path):
    """条数够但时间跨度不够(实际节拍比配置密)时,同样判为窗口不完整。

    造 20 小时、每 60 秒一条(1201 条),配置节拍按 600 秒 —— 条数判据会通过
    (1201 远超满窗 142 条),但跨度只有 20 小时 < 23 小时,必须为 None。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 1201, step=60)
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        assert len(rows) == 1201
        assert all(r["online_ma24"] is None for r in rows)
    asyncio.run(run())


def test_ma24_keeps_one_decimal(tmp_path):
    """均值保留一位小数。

    online 是整数,但奇数条窗口的平均必然带小数。全用 100+i 造数时任何窗口均值都是
    整数,"保留一位小数"这条口径就钉不住(线上实际是 188.4 / 177.8)。
    """
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200, online=lambda i: 100 + (i % 2))
        async with _client(db, DATA_START) as cli:
            rows = (await (await cli.get("/api/series?range=all")).json())["samples"]
        v = rows[-1]["online_ma24"]
        assert v == 100.5      # 145 条(奇数):和 = 14500 + 73
        assert v != round(v)   # 真带小数,不是被 round 成整数
    asyncio.run(run())


def test_ma24_tolerates_unparsable_timestamp(tmp_path):
    """库里混进格式不对的时间戳:不能整个请求 500,跳过那条、其余照常返回。"""
    async def run():
        db = Database(tmp_path / "t.db")
        _seed(db, "2026-10-01T00:00:00", 200)
        # 空格分隔(不是 T 分隔)—— 排在数据起点之后、会被查出来,但 strptime 解析不了
        db.insert_sample("2026-10-05 12:00:00", 111, 109, 10, 20, 30, 400.0)
        async with _client(db, DATA_START) as cli:
            resp = await cli.get("/api/series?range=all")
            assert resp.status == 200, resp.status
            rows = (await resp.json())["samples"]
        ts_list = [r["ts"] for r in rows]
        assert "2026-10-05 12:00:00" not in ts_list
        assert len(rows) == 200
        assert rows[-1]["online_ma24"] is not None
    asyncio.run(run())
