"""采样循环:每 interval_seconds 从用户列表计算五项指标并入库。

仅在收到过快照(has_data)时采样;断连期间留空(图表留缺口,不补零)。
入库前做异常检测:偏离自身近窗口内均值过大的样本剔除(见 anomaly.py)。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from storage.db import Database

from .anomaly import DEFAULT_CONFIG, reject_reason
from .ws_client import IIRoseClient


class Sampler:
    def __init__(
        self,
        client: IIRoseClient,
        db: Database,
        interval_seconds: float,
        anomaly_config: dict | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.db = db
        self.interval = interval_seconds
        self.anomaly_config = anomaly_config or {}
        self.log = log or logging.getLogger("iirose.sampler")

    def _recent_samples(self, ts: str) -> list[dict]:
        """近 window_seconds 内已入库样本(不含候选本身,时间升序)。"""
        window = self.anomaly_config.get(
            "window_seconds", DEFAULT_CONFIG["window_seconds"]
        )
        since = (
            datetime.fromisoformat(ts) - timedelta(seconds=window)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        return self.db.query(since)

    def _insert(self, ts: str, stats: dict) -> None:
        """入库,并在距上次入库超过 2 个采样间隔时警告数据缺失。

        每次写库前对比库中最新样本:开机(程序未运行)与断连恢复的缺失
        都会被同一条规则覆盖——警告后即写入新样本,下一轮对比自然刷新。
        """
        last = self.db.latest()
        if last is not None:
            gap = datetime.fromisoformat(ts) - datetime.fromisoformat(last["ts"])
            if gap > timedelta(seconds=self.interval * 2):
                self.log.warning(
                    "距上次采样已隔 %.0f 分钟(%s → %s),期间数据缺失(停机或断连)",
                    gap.total_seconds() / 60, last["ts"], ts,
                )
        self.db.insert_sample(
            ts,
            stats["online"],
            stats["chatting"],
            stats["active"],
            stats["away"],
            stats["entering"],
        )

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            if not self.client.has_data:
                continue
            stats = self.client.userlist.compute_stats()
            ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            recent = await asyncio.to_thread(self._recent_samples, ts)
            reason = reject_reason(stats, recent, self.anomaly_config)
            if reason:
                self.log.warning(
                    "采样异常,已剔除 online=%d chatting=%d active=%d away=%d entering=%d(%s)",
                    stats["online"], stats["chatting"], stats["active"],
                    stats["away"], stats["entering"], reason,
                )
                continue
            try:
                await asyncio.to_thread(self._insert, ts, stats)
                self.log.info(
                    "采样 %s online=%d chatting=%d active=%d away=%d entering=%d",
                    ts, stats["online"], stats["chatting"], stats["active"],
                    stats["away"], stats["entering"],
                )
            except Exception:
                self.log.exception("采样入库失败")
