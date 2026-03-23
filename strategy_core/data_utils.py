"""
strategy_core/data_utils.py
==================================
历史 K 线数据获取工具（供回测脚本和策略复用）。

提供两种数据来源：
  - fetch_real_bars : 从 OKX 拉取真实历史 K 线
  - generate_mock_bars : 本地生成带均值回归特征的模拟 K 线
  - load_bars : 统一入口，根据 source 参数自动分发
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 真实数据：从 OKX 拉取
# ──────────────────────────────────────────────────────────────

def fetch_real_bars(
    inst_id: str,
    interval: str = "1H",
    limit: int = 1000,
    start: datetime | None = None,
    end: datetime | None = None,
):
    """
    从 OKX REST API 拉取真实历史 K 线。

    优先使用时间范围模式：若同时指定 start 和 end，则调用
    get_history_klines 按时间段分批拉取（数量不受 limit 限制）；
    否则调用 get_klines 拉取最近 limit 根。

    Args:
        inst_id:  交易品种，如 "BTC-USDT"
        interval: K 线周期，如 "1H"、"4H"、"1D"
        limit:    拉取条数，仅在未指定 start/end 时生效
        start:    回测起始时间（UTC），与 end 同时指定时启用时间范围模式
        end:      回测结束时间（UTC），默认为当前时间

    Returns:
        list[BarData]，按时间升序排列

    Raises:
        RuntimeError: OKX 网关连接失败时抛出（调用方负责 fallback）
    """
    from core.event_bus import EventBus
    from gateway.okx.okx_gateway import OKXGateway
    from utils.config_loader import load_okx_config

    config = load_okx_config()
    event_bus = EventBus()
    gw = OKXGateway(event_bus, config["okx"])
    try:
        gw.connect()
        if start is not None:
            end_ = end if end is not None else datetime.now(timezone.utc)
            bars = gw.get_history_klines(inst_id, interval, start=start, end=end_)
        else:
            bars = gw.get_klines(inst_id, interval, limit=limit)
        if bars:
            logger.info(
                "从 OKX 拉取 %d 根 %s K 线  [%s → %s]",
                len(bars), interval,
                bars[0].timestamp.strftime("%Y-%m-%d"),
                bars[-1].timestamp.strftime("%Y-%m-%d"),
            )
        else:
            logger.warning("从 OKX 拉取到 0 根 K 线，请检查参数")
        return bars
    finally:
        gw.disconnect()


# ──────────────────────────────────────────────────────────────
# 模拟数据：本地 GBM + 均值回归
# ──────────────────────────────────────────────────────────────

def generate_mock_bars(
    inst_id: str,
    interval: str = "1H",
    n_days: int = 180,
    start_price: float = 45_000.0,
    seed: int = 42,
    annual_vol: float = 0.012,   # 每 bar 波动率（约等于 1H 隐含年化 ~42%）
    start: datetime | None = None,
    end: datetime | None = None,
):
    """
    生成带均值回归特征的模拟 K 线（GBM + Ornstein–Uhlenbeck drift）。

    Args:
        inst_id:     交易品种名称（仅用于填充 BarData.inst_id）
        interval:    K 线周期字符串（填充 BarData.interval，不影响生成逻辑）
        n_days:      生成天数（当 start/end 均未指定时生效）
        start_price: 起始价格
        seed:        随机种子，固定保证可复现
        annual_vol:  每 bar 的对数收益率标准差
        start:       若指定，则从此时间开始生成（优先于 n_days）
        end:         若指定，则生成到此时间截止（与 start 同时指定时，按日期范围计算 n_days）

    Returns:
        list[BarData]，按时间升序排列
    """
    from core.enums import Exchange
    from core.models import BarData

    _INTERVAL_HOURS = {
        "1m": 1 / 60,  "2m": 2 / 60,  "3m": 3 / 60,
        "5m": 5 / 60,  "10m": 10 / 60, "15m": 15 / 60, "30m": 30 / 60,
        "1H": 1, "2H": 2, "4H": 4, "6H": 6, "12H": 12,
        "1D": 24, "1W": 168, "1M": 720,
    }
    bar_hours = _INTERVAL_HOURS.get(interval, 1)

    # 确定起始时间和天数
    if start is not None:
        ts = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        if end is not None:
            end_ = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
            n_days = max(1, int((end_ - ts).total_seconds() / 86400))
    else:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)

    n_bars = int(n_days * 24 / bar_hours)

    random.seed(seed)
    bars, price = [], start_price

    for _ in range(n_bars):
        mean_reversion = (start_price - price) / start_price * 0.005
        eps = random.gauss(0, 1)
        ret = math.exp((mean_reversion - 0.5 * annual_vol ** 2) + annual_vol * eps)
        next_price = price * ret
        open_, close_ = price, next_price
        high = max(open_, close_) * (1 + abs(random.gauss(0, 0.004)))
        low  = min(open_, close_) * (1 - abs(random.gauss(0, 0.004)))

        bars.append(BarData(
            inst_id=inst_id,
            exchange=Exchange.OKX,
            interval=interval,
            open=Decimal(str(round(open_, 2))),
            high=Decimal(str(round(high, 2))),
            low=Decimal(str(round(low, 2))),
            close=Decimal(str(round(close_, 2))),
            volume=Decimal(str(round(random.uniform(50, 300), 2))),
            volume_ccy=Decimal("0"),
            timestamp=ts,
        ))
        price = next_price
        ts += timedelta(hours=bar_hours)

    if not bars:
        logger.warning(
            "generate_mock_bars 生成 0 根 K 线 | interval=%s n_days=%d bar_hours=%.4f",
            interval, n_days, bar_hours,
        )
        return bars

    logger.info(
        "生成模拟 K 线 %d 根 [%s → %s]  seed=%d",
        len(bars),
        bars[0].timestamp.strftime("%Y-%m-%d"),
        bars[-1].timestamp.strftime("%Y-%m-%d"),
        seed,
    )
    return bars


# ──────────────────────────────────────────────────────────────
# 统一入口
# ──────────────────────────────────────────────────────────────

def load_bars(
    source: str,
    inst_id: str,
    interval: str = "1H",
    limit: int = 1000,
    start: datetime | None = None,
    end: datetime | None = None,
    mock_days: int = 180,
    mock_seed: int = 42,
    fallback_to_mock: bool = True,
):
    """
    统一数据加载入口。

    Args:
        source:          "real" 从 OKX 拉取，"mock" 本地生成
        inst_id:         交易品种
        interval:        K 线周期
        limit:           real 模式且未指定时间范围时拉取的条数
        start:           回测起始时间（UTC）；与 end 同时指定时按时间段拉取
        end:             回测结束时间（UTC）；未指定则为当前时间
        mock_days:       mock 模式天数
        mock_seed:       mock 模式随机种子
        fallback_to_mock: real 失败时是否自动降级为 mock（默认 True）

    Returns:
        list[BarData]
    """
    if source == "mock":
        return generate_mock_bars(inst_id, interval=interval,
                                  n_days=mock_days, seed=mock_seed,
                                  start=start, end=end)

    # source == "real"
    try:
        return fetch_real_bars(inst_id, interval=interval,
                               limit=limit, start=start, end=end)
    except Exception as exc:
        if fallback_to_mock:
            logger.warning("OKX 数据拉取失败（%s），自动切换为模拟数据", exc)
            return generate_mock_bars(inst_id, interval=interval,
                                      n_days=mock_days, seed=mock_seed,
                                      start=start, end=end)
        raise
