"""历史数据加载器 - 从数据库读取或从交易所 API 下载历史数据"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from data.database import Database

if TYPE_CHECKING:
    from gateway.base_gateway import BaseGateway

logger = logging.getLogger("data.feed")

# OKX 单次历史 K 线最大返回条数
_OKX_PAGE_LIMIT = 300

# 各 interval 对应的 timedelta（用于间隙检测和分页步长计算）
_INTERVAL_DELTA: dict[str, timedelta] = {
    "1m":  timedelta(minutes=1),
    "3m":  timedelta(minutes=3),
    "5m":  timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1H":  timedelta(hours=1),
    "2H":  timedelta(hours=2),
    "4H":  timedelta(hours=4),
    "6H":  timedelta(hours=6),
    "12H": timedelta(hours=12),
    "1D":  timedelta(days=1),
    "1W":  timedelta(weeks=1),
}


class DataFeed:
    """
    历史数据加载器。

    三种数据来源：
    1. 录制数据（优先）：实盘 WebSocket 录制的 K 线，与实盘完全一致
    2. 数据库（次优）：本地已录制的 K 线
    3. REST API 下载（兜底）：调用 Gateway REST API 下载并入库

    使用示例：
        # 方式1：从录制数据加载（推荐，保证回测-实盘数据一致）
        feed = DataFeed.from_recorded_data("data/recorded.db", "BTC-USDT-SWAP", "1H")
        bars = feed.load_history(...)

        # 方式2：从 REST API 加载（可能有微小差异）
        feed = DataFeed.from_okx_rest("BTC-USDT-SWAP", "1H", start, end, gateway)

        # 方式3：混用（默认）
        feed = DataFeed(db, gateway)
        bars = feed.load_history("BTC-USDT-SWAP", "1H", start, end)
    """

    def __init__(self, database: Database, gateway: "BaseGateway | None" = None, source: str = "mixed"):
        """
        Args:
            database: 数据库实例
            gateway:  可选的 Gateway 实例（用于 API 下载功能）
            source:   数据来源标记 "recorded" | "rest" | "mixed"
        """
        self.db = database
        self.gateway = gateway
        self.source = source

    # ─────────────────────── 主接口 ──────────────────────────────

    def load_history(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime | None = None,
    ) -> list:
        """
        加载历史 K 线，自动补齐本地缺失数据。

        五步流程：
        a. 查询本地已有数据范围（get_bar_range）
        b. 计算缺失的时间区间
        c. 仅对缺失部分调用 Gateway REST API 分页拉取
        d. 拉取结果写入本地 SQLite
        e. 从本地 SQLite 一次性读取完整数据，清洗后返回

        首次调用会从 API 下载数据（较慢），之后直接本地读取（秒级）。

        Args:
            symbol:   产品 ID（如 "BTC-USDT-SWAP"）
            interval: K 线周期（如 "1H"）
            start:    开始时间（UTC）
            end:      结束时间（UTC），None 时使用当前时间

        Returns:
            清洗后的 BarData 列表，按时间戳升序排列
        """
        # 统一时区
        start = _ensure_utc(start)
        end = _ensure_utc(end or datetime.now(timezone.utc))

        exchange = self.gateway.exchange.value if self.gateway else ""

        # ── a. 查询本地已有范围 ───────────────────────────────────
        local_range = self.db.get_bar_range(symbol, exchange, interval)

        # ── b. 计算缺失区间 ───────────────────────────────────────
        gaps = _calc_gaps(start, end, local_range)

        # ── c/d. 对每段缺口分页拉取并入库 ─────────────────────────
        if gaps:
            if not self.gateway:
                logger.warning(
                    "本地数据不完整（缺 %d 段），但未配置 Gateway，无法补拉 | "
                    "symbol=%s interval=%s",
                    len(gaps), symbol, interval,
                )
            else:
                for gap_start, gap_end in gaps:
                    self._download_and_save(symbol, interval, gap_start, gap_end)

        # ── e. 从本地读取完整数据 ─────────────────────────────────
        bars = self.db.load_bars(symbol, exchange, interval, start, end)
        logger.info(
            "load_history 完成 | symbol=%s interval=%s count=%d [%s ~ %s]",
            symbol, interval, len(bars),
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
        )

        # ── 数据清洗 ──────────────────────────────────────────────
        bars = self._clean_bars(bars, symbol, interval)
        return bars

    def load_bars(
        self,
        inst_id: str,
        exchange: str,
        interval: str,
        start: datetime,
        end: datetime | None = None,
        auto_download: bool = True,
    ) -> list:
        """
        加载 K 线数据（旧接口，保持兼容）。

        策略：
        1. 先查数据库
        2. 若数据缺口较大且 auto_download=True，则从 API 补充下载
        3. 返回合并后的完整数据

        推荐新代码使用 load_history()。
        """
        if end is None:
            end = datetime.now(timezone.utc)
        start = _ensure_utc(start)
        end = _ensure_utc(end)

        # 从数据库加载
        bars = self.db.load_bars(inst_id, exchange, interval, start, end)

        if bars:
            logger.info(
                "从数据库加载 %d 根 %s %s K线 [%s ~ %s]",
                len(bars), inst_id, interval,
                bars[0].timestamp.strftime("%Y-%m-%d"),
                bars[-1].timestamp.strftime("%Y-%m-%d"),
            )
            return bars

        # 数据库无数据，通过 API 下载
        if auto_download and self.gateway:
            logger.info("数据库无数据，从 API 下载 %s %s K线...", inst_id, interval)
            return self.download_bars(inst_id, interval, start, end)

        logger.warning("无法获取 %s %s K线：数据库为空且未配置 Gateway", inst_id, interval)
        return []

    def download_bars(
        self,
        inst_id: str,
        interval: str,
        start: datetime,
        end: datetime | None = None,
    ) -> list:
        """
        从 Gateway API 下载历史 K 线并入库。

        Returns:
            下载的 BarData 列表
        """
        if not self.gateway:
            raise RuntimeError("未配置 Gateway，无法下载历史数据")

        if end is None:
            end = datetime.now(timezone.utc)

        logger.info(
            "开始下载历史 K线 %s %s [%s ~ %s]",
            inst_id, interval,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

        bars = self.gateway.get_history_klines(inst_id, interval, start, end)

        if bars:
            count = self.db.save_bars(bars)
            logger.info("下载完成，共 %d 根K线，入库 %d 根（重复忽略）", len(bars), count)

        return bars

    def get_latest_bars(self, inst_id: str, interval: str, limit: int = 100) -> list:
        """
        获取最新 K 线（直接从 API，不入库，适合实时策略初始化）。
        """
        if not self.gateway:
            raise RuntimeError("未配置 Gateway")
        return self.gateway.get_klines(inst_id, interval, limit)

    # ─────────────────────── 内部方法 ────────────────────────────

    def _download_and_save(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> None:
        """
        分页拉取指定时间段的 K 线并写入数据库。

        OKX 单次最多返回 300 根，按 interval 步长循环翻页直到覆盖 end。
        """
        delta = _INTERVAL_DELTA.get(interval)
        total_saved = 0
        cursor = start

        logger.info(
            "开始补拉历史K线 | symbol=%s interval=%s [%s ~ %s]",
            symbol, interval,
            start.strftime("%Y-%m-%d %H:%M"),
            end.strftime("%Y-%m-%d %H:%M"),
        )

        while cursor < end:
            # 每页覆盖的时间窗口
            if delta:
                page_end = min(cursor + delta * _OKX_PAGE_LIMIT, end)
            else:
                page_end = end

            try:
                bars = self.gateway.get_history_klines(symbol, interval, cursor, page_end)
            except Exception:
                logger.exception(
                    "API 拉取K线失败 | symbol=%s interval=%s cursor=%s",
                    symbol, interval, cursor,
                )
                break

            if not bars:
                break

            saved = self.db.save_bars(bars)
            total_saved += saved
            logger.debug(
                "已拉取 %d 根K线，入库 %d 根 | cursor=%s",
                len(bars), saved, cursor.strftime("%Y-%m-%d %H:%M"),
            )

            # 推进游标到已拉取数据的最后一根之后
            last_ts = bars[-1].timestamp
            last_ts = _ensure_utc(last_ts)
            if delta:
                cursor = last_ts + delta
            else:
                break  # 无法推进，退出防止死循环

            if len(bars) < _OKX_PAGE_LIMIT:
                break  # API 已返回不足一页，说明数据到头

        logger.info(
            "补拉完成 | symbol=%s interval=%s 入库=%d 根",
            symbol, interval, total_saved,
        )

    def _clean_bars(self, bars: list, symbol: str, interval: str) -> list:
        """
        数据清洗（三步）：
        a. 按时间戳升序排序
        b. 过滤价格为 0 或 volume 为 0 的异常条目，记 warning 日志
        c. 检测时间间隙，记 warning 日志但不阻断

        Returns:
            清洗后的 BarData 列表
        """
        if not bars:
            return bars

        # a. 排序
        bars = sorted(bars, key=lambda b: b.timestamp)

        # b. 过滤零价 / 零量
        clean: list = []
        for b in bars:
            if b.close <= Decimal("0") or b.volume <= Decimal("0"):
                logger.warning(
                    "过滤异常K线 | symbol=%s interval=%s ts=%s "
                    "close=%s volume=%s",
                    symbol, interval, b.timestamp, b.close, b.volume,
                )
                continue
            clean.append(b)

        # c. 检测时间间隙
        delta = _INTERVAL_DELTA.get(interval)
        if delta and len(clean) >= 2:
            _gap_threshold = delta * 2
            for i in range(1, len(clean)):
                prev_ts = _ensure_utc(clean[i - 1].timestamp)
                curr_ts = _ensure_utc(clean[i].timestamp)
                gap = curr_ts - prev_ts
                if gap > _gap_threshold:
                    logger.warning(
                        "检测到时间间隙 | symbol=%s interval=%s "
                        "gap=%.0fs [%s ~ %s]",
                        symbol, interval, gap.total_seconds(),
                        prev_ts.strftime("%Y-%m-%d %H:%M"),
                        curr_ts.strftime("%Y-%m-%d %H:%M"),
                    )

        return clean

    # ─────────────────────── 工厂方法 ────────────────────────────

    @classmethod
    def from_recorded_data(
        cls,
        db_path: str,
        symbol: str,
        interval: str,
        exchange: str = "OKX",
    ) -> "DataFeed":
        """
        从实盘录制的 SQLite 数据加载，保证回测-实盘数据完全一致。

        推荐工作流：
            实盘运行：DataRecorder 录制 WebSocket K 线 → SQLite
                          ↓
            回测回放：DataFeed.from_recorded_data() → BacktestEngine

        Args:
            db_path:   SQLite 数据库路径
            symbol:    产品 ID（如 "BTC-USDT-SWAP"）
            interval:  K 线周期
            exchange:  交易所（默认 OKX）

        Returns:
            DataFeed 实例，仅从本地数据库读取

        示例：
            feed = DataFeed.from_recorded_data(
                "data/recorded.db", "BTC-USDT-SWAP", "1H"
            )
            bars = feed.load_history(...)
        """
        database = Database(db_path)
        feed = cls(database=database, gateway=None, source="recorded")
        logger.info("DataFeed 从录制数据加载 | db=%s symbol=%s interval=%s", db_path, symbol, interval)
        return feed

    @classmethod
    def from_okx_rest(
        cls,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime | None,
        gateway: "BaseGateway",
    ) -> "DataFeed":
        """
        从 OKX REST API 加载历史 K 线（作为 fallback）。

        注意：REST API 数据与 WebSocket 数据可能有微小差异，
        建议优先使用 from_recorded_data()。

        Args:
            symbol:   产品 ID
            interval: K 线周期
            start:    开始时间（UTC）
            end:      结束时间（UTC），None 表示当前时间
            gateway:  Gateway 实例（需支持 get_history_klines）

        Returns:
            DataFeed 实例

        示例：
            feed = DataFeed.from_okx_rest(
                "BTC-USDT-SWAP", "1H",
                start=datetime(2025, 1, 1),
                end=datetime(2025, 3, 1),
                gateway=gateway,
            )
            bars = feed.load_history(...)
        """
        feed = cls(database=gateway.database if hasattr(gateway, "database") else Database(), gateway=gateway, source="rest")
        logger.info("DataFeed 从 REST API 加载 | symbol=%s interval=%s", symbol, interval)
        return feed


# ─────────────────────── 工具函数 ────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    """确保 datetime 带 UTC tzinfo。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _calc_gaps(
    start: datetime,
    end: datetime,
    local_range: tuple[datetime, datetime] | None,
) -> list[tuple[datetime, datetime]]:
    """
    计算 [start, end] 内本地数据缺失的时间区间。

    本地范围 (local_min, local_max) 将 [start, end] 分为最多 2 段缺口：
    - 前缺口：[start, local_min) （若 start < local_min）
    - 后缺口：(local_max, end]  （若 local_max < end）
    - 无本地数据：整段 [start, end] 均为缺口

    Returns:
        [(gap_start, gap_end), ...] 空列表表示无需补拉
    """
    if local_range is None:
        # 本地无任何数据，整段都需要拉
        return [(start, end)]

    local_min, local_max = _ensure_utc(local_range[0]), _ensure_utc(local_range[1])
    gaps: list[tuple[datetime, datetime]] = []

    if start < local_min:
        gaps.append((start, local_min))

    if local_max < end:
        gaps.append((local_max, end))

    return gaps
