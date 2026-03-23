"""
未来函数检测测试（Lookahead Bias Tests）。

这是最重要的测试文件——确保回测引擎不存在 lookahead bias。

测试原则：
- 策略在 bar[t] 的 on_bar 中下单 → 最早在 bar[t+1] 的 open 成交
- get_klines 返回的数据不能包含当前 bar 之后的数据
- 截断一致性：bars[:100] 与 bars[:200] 的前 100 根 bar 结果完全相同
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.broker import SimulatedBroker
from backtest.engine import BacktestEngine
from core.enums import Exchange, MarginMode, OrderSide, OrderType, PositionSide
from core.models import BarData, OrderData, OrderRequest
from strategy_core.base_strategy import BaseStrategy


# ─────────────────────── 辅助工具 ────────────────────────────


def make_bar(
    price: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    ts: datetime | None = None,
    inst_id: str = "BTC-USDT",
) -> BarData:
    if open_ is None:
        open_ = price
    if high is None:
        high = max(open_, price) * 1.005
    if low is None:
        low = min(open_, price) * 0.995
    return BarData(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        interval="1H",
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(price)),
        volume=Decimal("100"),
        volume_ccy=Decimal(str(price * 100)),
        timestamp=ts or datetime.now(timezone.utc),
    )


def make_bars(n: int, start_price: float = 50000.0, step: float = 100.0) -> list[BarData]:
    """生成 n 根依次递增的 K 线，时间步长 1H"""
    base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        price = start_price + i * step
        bars.append(BarData(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            interval="1H",
            open=Decimal(str(price)),
            high=Decimal(str(price + step * 0.4)),
            low=Decimal(str(price - step * 0.1)),
            close=Decimal(str(price + step * 0.5)),
            volume=Decimal("100"),
            volume_ccy=Decimal(str(price * 100)),
            timestamp=base_ts + timedelta(hours=i),
        ))
    return bars


# ─────────────────────── 测试用策略 ──────────────────────────


class BuyOnBar5Strategy(BaseStrategy):
    """
    在第 5 个 bar（index=4）的 on_bar 中下一笔市价买单。
    用于验证成交发生在 bar[5]（index=5），而非 bar[4]。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bar_idx = 0
        self.order_placed = False

    def on_init(self) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        if self._bar_idx == 4 and not self.order_placed:
            self.buy(price=Decimal("0"), quantity=Decimal("1"), order_type=OrderType.MARKET)
            self.order_placed = True
        self._bar_idx += 1


class RecordKlinesStrategy(BaseStrategy):
    """
    在每个 on_bar 中调用 get_klines 并记录返回的最后一根 bar 时间戳。
    用于验证不会看到未来数据。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kline_last_timestamps: list[datetime] = []
        self.current_bar_timestamps: list[datetime] = []

    def on_init(self) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        self.current_bar_timestamps.append(bar.timestamp)
        klines = self.get_klines(self.inst_id, "1H", limit=9999)
        if klines:
            self.kline_last_timestamps.append(klines[-1].timestamp)
        else:
            self.kline_last_timestamps.append(bar.timestamp)


class LimitBuyOnBar5Strategy(BaseStrategy):
    """
    在第 5 个 bar（index=4）的 on_bar 中下一个固定限价买单。
    限价被设置为 bars_shared_limit_price，该值会被 bar[4] 和 bar[5] 都触及。
    用于验证成交在 bar[5] 而非 bar[4]（同一根 bar 成交 = 未来函数）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bar_idx = 0
        self.order_placed = False
        # 限价由外部注入（通过 config）
        self.limit_price = Decimal(str(kwargs.get("config", {}).get("limit_price", "0")))

    def on_init(self) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        if self._bar_idx == 4 and not self.order_placed:
            self.buy(price=self.limit_price, quantity=Decimal("1"), order_type=OrderType.LIMIT)
            self.order_placed = True
        self._bar_idx += 1


class TruncationTestStrategy(BaseStrategy):
    """
    极简策略：每 10 根 bar 买入一次。
    用于截断一致性测试。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bar_idx = 0

    def on_init(self) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        if self._bar_idx > 0 and self._bar_idx % 10 == 0:
            # 如果已有持仓则卖出，否则买入
            pos = self.get_position()
            if pos is not None and pos.quantity > Decimal("0"):
                self.sell(price=Decimal("0"), quantity=pos.quantity, order_type=OrderType.MARKET)
            else:
                self.buy(price=Decimal("0"), quantity=Decimal("0.01"), order_type=OrderType.MARKET)
        self._bar_idx += 1


# ─────────────────────── 主测试类 ────────────────────────────


class TestNoLookahead:

    # ── 测试 1：截断一致性 ─────────────────────────────────────

    def test_truncation_consistency(self):
        """
        核心测试：截断一致性。

        用 bars[:200] 和 bars[:100] 分别回测同一策略。
        前 100 根 bar 期间产生的成交（时间、价格、方向、数量）必须完全一致。
        如果不一致 → 说明存在未来函数。
        """
        bars_200 = make_bars(200)
        bars_100 = bars_200[:100]

        def run_engine(bars):
            engine = BacktestEngine(
                strategy_class=TruncationTestStrategy,
                strategy_config={},
                inst_id="BTC-USDT",
                bars=bars,
                initial_capital=Decimal("100000"),
                taker_fee=Decimal("0"),
                maker_fee=Decimal("0"),
            )
            engine.run()
            return engine.broker.get_trades()

        trades_200 = run_engine(bars_200)
        trades_100 = run_engine(bars_100)

        # 只比较 bars[:100] 的时间范围内的成交（排除强制平仓）
        cutoff_ts = bars_100[-1].timestamp
        trades_200_early = [
            t for t in trades_200
            if t.timestamp <= cutoff_ts and t.order_id != "FORCE_CLOSE"
        ]
        trades_100_normal = [
            t for t in trades_100
            if t.order_id != "FORCE_CLOSE"
        ]

        # 成交数量必须相同
        assert len(trades_200_early) == len(trades_100_normal), (
            f"成交数量不一致：bars[:200] 前100根产生 {len(trades_200_early)} 笔，"
            f"bars[:100] 产生 {len(trades_100_normal)} 笔 → 存在未来函数！"
        )

        # 逐笔比较
        for i, (t200, t100) in enumerate(zip(trades_200_early, trades_100_normal)):
            assert t200.timestamp == t100.timestamp, (
                f"第{i}笔成交时间不一致: {t200.timestamp} vs {t100.timestamp}"
            )
            assert t200.price == t100.price, (
                f"第{i}笔成交价格不一致: {t200.price} vs {t100.price}"
            )
            assert t200.side == t100.side, (
                f"第{i}笔成交方向不一致: {t200.side} vs {t100.side}"
            )
            assert t200.quantity == t100.quantity, (
                f"第{i}笔成交数量不一致: {t200.quantity} vs {t100.quantity}"
            )

    # ── 测试 2：市价单延迟一根 bar 成交 ──────────────────────

    def test_order_delayed_one_bar(self):
        """
        策略在 bar[4]（index=4）的 on_bar 中下市价买单，
        验证成交发生在 bar[5]（index=5），成交价 = bar[5].open。

        不能在 bar[4] 就成交（这是未来函数）。
        """
        bars = make_bars(10)

        engine = BacktestEngine(
            strategy_class=BuyOnBar5Strategy,
            strategy_config={},
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            taker_fee=Decimal("0"),
            maker_fee=Decimal("0"),
        )
        engine.run()

        # 过滤掉强制平仓单
        normal_trades = [t for t in engine.broker.get_trades() if t.order_id != "FORCE_CLOSE"]

        assert len(normal_trades) >= 1, "应有至少一笔成交（策略在 bar[4] 下单）"

        first_trade = normal_trades[0]
        expected_fill_bar = bars[5]   # bar[index=5]，即第6根K线

        # 成交时间戳必须是 bar[5] 的时间戳
        assert first_trade.timestamp == expected_fill_bar.timestamp, (
            f"成交时间戳错误: 期望 {expected_fill_bar.timestamp}（bar[5]），"
            f"实际 {first_trade.timestamp}。"
            f"如果是 bars[4] 的时间戳，说明存在未来函数！"
        )

        # 成交价必须是 bar[5] 的开盘价（市价单，无滑点）
        assert first_trade.price == expected_fill_bar.open, (
            f"成交价格错误: 期望 bar[5].open={expected_fill_bar.open}，"
            f"实际 {first_trade.price}"
        )

    # ── 测试 3：get_klines 不包含未来数据 ────────────────────

    def test_get_klines_no_future(self):
        """
        验证策略通过 get_klines 获取的数据不包含未来 bar。

        在每个 on_bar 中，get_klines 返回的最后一根 bar 的时间戳
        必须 <= 当前 bar 的时间戳。
        """
        bars = make_bars(20)

        engine = BacktestEngine(
            strategy_class=RecordKlinesStrategy,
            strategy_config={},
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
        )
        engine.run()

        strategy: RecordKlinesStrategy = engine.strategy

        assert len(strategy.kline_last_timestamps) == len(strategy.current_bar_timestamps), (
            "记录的 kline 时间戳数量与 bar 数量不匹配"
        )

        for i, (kline_last_ts, current_bar_ts) in enumerate(
            zip(strategy.kline_last_timestamps, strategy.current_bar_timestamps)
        ):
            assert kline_last_ts <= current_bar_ts, (
                f"bar[{i}]（ts={current_bar_ts}）中 get_klines 返回了未来数据！"
                f"get_klines 最后一根 ts={kline_last_ts} > 当前 bar ts={current_bar_ts}"
            )

    # ── 测试 4：限价单不在同一根 bar 成交 ────────────────────

    def test_limit_order_not_fill_same_bar(self):
        """
        策略在 bar[4] 的 on_bar 中下限价买单，限价介于 bar[4].low 和 bar[4].open 之间。

        关键：构造下降行情，让 bar[4] 和 bar[5] 的 low 都低于 limit_price。
        - 旧代码（先 on_bar 再 match_orders）：在 bar[4] 的 match_orders 中立即成交
          → 成交 timestamp = bar[4].timestamp（未来函数！）
        - 修复后（先 match_orders 再 on_bar）：bar[4] 的 on_bar 刚产生订单，
          bar[4] 的 match_orders 已经执行过了，所以要等 bar[5] 的 match_orders
          → 成交 timestamp = bar[5].timestamp（正确）
        """
        # 构造下降行情：bar[4] 和 bar[5] 的 low 都低于 limit_price
        # 从高价开始下跌，使得 bar[4] 的 low 肯定低于 limit_price
        base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        limit_price = Decimal("50000")

        bars = []
        for i in range(10):
            # 从 50500 开始每根下跌 100，到 bar[4] 时 open ≈ 50100，low ≈ 49900
            open_ = Decimal(str(50500 - i * 100))
            bars.append(BarData(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                interval="1H",
                open=open_,
                high=open_ + Decimal("200"),
                low=open_ - Decimal("200"),   # 每根 low 都低于 limit_price(50000)，i>=2 时
                close=open_ - Decimal("50"),
                volume=Decimal("100"),
                volume_ccy=Decimal("1000000"),
                timestamp=base_ts + timedelta(hours=i),
            ))

        # 验证 bar[4] 和 bar[5] 的 low 都确实低于 limit_price
        assert bars[4].low < limit_price, f"bar[4].low={bars[4].low} 不低于 limit_price={limit_price}"
        assert bars[5].low < limit_price, f"bar[5].low={bars[5].low} 不低于 limit_price={limit_price}"

        engine = BacktestEngine(
            strategy_class=LimitBuyOnBar5Strategy,
            strategy_config={"limit_price": str(limit_price)},
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            taker_fee=Decimal("0"),
            maker_fee=Decimal("0"),
        )
        engine.run()

        normal_trades = [t for t in engine.broker.get_trades() if t.order_id != "FORCE_CLOSE"]

        assert len(normal_trades) >= 1, "应有至少一笔限价单成交"

        first_trade = normal_trades[0]
        bar4_ts = bars[4].timestamp
        bar5_ts = bars[5].timestamp

        # 成交时间不能是 bar[4] 的时间戳（那意味着在同一根 bar 成交了 = 未来函数）
        assert first_trade.timestamp != bar4_ts, (
            f"限价单在同一根 bar（bar[4]，ts={bar4_ts}）成交了！"
            f"这是未来函数 Bug。成交应该在 bar[5] 发生。"
        )

        # 成交时间应该是 bar[5] 的时间戳
        assert first_trade.timestamp == bar5_ts, (
            f"限价单成交时间异常: 期望 bar[5] ts={bar5_ts}，实际 {first_trade.timestamp}"
        )
