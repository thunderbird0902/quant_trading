"""回测引擎单元测试

测试覆盖：
1. SimulatedBroker：市价单 / 限价单撮合、持仓更新、余额扣减
2. PerformanceAnalyzer：最大回撤、夏普比率、胜率计算
3. BacktestEngine：端到端回测、绩效指标范围检验
"""

from __future__ import annotations

import math
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.enums import Exchange, MarginMode, OrderSide, OrderStatus, OrderType, PositionSide
from core.models import BarData, OrderRequest
from backtest.broker import SimulatedBroker
from backtest.performance import PerformanceAnalyzer
from backtest.engine import BacktestEngine
from strategy_core.impls.double_ma_strategy import DoubleMaStrategy


# ─────────────────────── 测试夹具 ────────────────────────────


def make_bar(
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    ts: datetime | None = None,
    inst_id: str = "BTC-USDT",
) -> BarData:
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) * 1.005
    if low is None:
        low = min(open_, close) * 0.995
    return BarData(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        interval="1H",
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
        volume_ccy=Decimal(str(close * 100)),
        timestamp=ts or datetime.now(timezone.utc),
    )


def make_bars_trend(prices: list[float], inst_id: str = "BTC-USDT") -> list[BarData]:
    """按价格序列生成 K 线，时间依次 +1H"""
    base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        make_bar(p, ts=base_ts + timedelta(hours=i), inst_id=inst_id)
        for i, p in enumerate(prices)
    ]


# ─────────────────────── SimulatedBroker 测试 ────────────────


class TestSimulatedBroker:

    def _broker(self, capital: float = 100_000) -> SimulatedBroker:
        return SimulatedBroker(
            initial_capital=Decimal(str(capital)),
            taker_fee=Decimal("0"),    # 简化：不计手续费
            maker_fee=Decimal("0"),
        )

    def _buy_request(self, inst_id="BTC-USDT", qty="1", price=None) -> OrderRequest:
        return OrderRequest(
            inst_id=inst_id,
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET if price is None else OrderType.LIMIT,
            quantity=Decimal(qty),
            price=Decimal(str(price)) if price else None,
            margin_mode=MarginMode.CASH,
        )

    def _sell_request(self, inst_id="BTC-USDT", qty="1", price=None) -> OrderRequest:
        return OrderRequest(
            inst_id=inst_id,
            exchange=Exchange.OKX,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET if price is None else OrderType.LIMIT,
            quantity=Decimal(qty),
            price=Decimal(str(price)) if price else None,
            margin_mode=MarginMode.CASH,
        )

    # ── 市价单成交 ──────────────────────────────────────────────

    def test_market_buy_fills_at_open(self):
        """市价买单在下一根 K 线开盘价成交"""
        broker = self._broker()
        bar = make_bar(close=50000, open_=49000, high=51000, low=48000)

        broker.send_order(self._buy_request(qty="1"))
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        assert trades[0].side == OrderSide.BUY
        assert trades[0].price == Decimal("49000")  # 开盘价
        assert trades[0].quantity == Decimal("1")

    def test_market_sell_fills_at_open(self):
        """市价卖单在下一根 K 线开盘价成交"""
        broker = self._broker()
        bar = make_bar(close=50000, open_=49000)

        broker.send_order(self._sell_request(qty="1"))
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        assert trades[0].side == OrderSide.SELL

    # ── 限价单成交 ──────────────────────────────────────────────

    def test_limit_buy_fills_when_low_reaches_price(self):
        """限价买单：K 线最低价 <= 委托价 → 成交"""
        broker = self._broker()
        # 委托价 48000，K 线最低价 47500 → 成交
        broker.send_order(self._buy_request(qty="1", price=48000))
        bar = make_bar(close=50000, open_=50000, high=51000, low=47500)
        trades = broker.match_orders(bar)
        assert len(trades) == 1
        assert trades[0].price == Decimal("48000")

    def test_limit_buy_no_fill_when_price_too_low(self):
        """限价买单：K 线最低价 > 委托价 → 不成交"""
        broker = self._broker()
        # 委托价 45000，K 线区间 47500-51000 → 不成交
        broker.send_order(self._buy_request(qty="1", price=45000))
        bar = make_bar(close=50000, open_=50000, high=51000, low=47500)
        trades = broker.match_orders(bar)
        assert len(trades) == 0

    def test_limit_sell_fills_when_high_reaches_price(self):
        """限价卖单：K 线最高价 >= 委托价 → 成交"""
        broker = self._broker()
        broker.send_order(self._sell_request(qty="1", price=51000))
        bar = make_bar(close=50000, open_=50000, high=51500, low=49000)
        trades = broker.match_orders(bar)
        assert len(trades) == 1

    # ── 持仓和余额更新 ──────────────────────────────────────────

    def test_position_updates_after_buy(self):
        """买入后持仓增加"""
        broker = self._broker(100_000)
        broker.send_order(self._buy_request(qty="2"))
        bar = make_bar(close=50000, open_=50000, high=51000, low=49000)
        broker.match_orders(bar)

        pos = broker.get_position("BTC-USDT")
        assert pos is not None
        assert pos.quantity == Decimal("2")
        assert pos.avg_price == Decimal("50000")

    def test_cash_decreases_after_buy(self):
        """买入后可用资金减少，总权益不变"""
        broker = self._broker(100_000)
        broker.send_order(self._buy_request(qty="1"))
        bar = make_bar(close=50000, open_=50000, high=51000, low=49000)
        broker.match_orders(bar)

        bal = broker.get_balance()
        # 可用资金减少（花费 50000 买 BTC）
        assert bal.available_balance == Decimal("50000")
        # 总权益 = 现金 + BTC市值 = 50000 + 50000 = 100000（未产生盈亏）
        assert bal.total_equity == Decimal("100000")

    def test_realized_pnl_on_close(self):
        """平仓时产生已实现盈亏，总权益正确"""
        broker = self._broker(100_000)
        buy_bar = make_bar(close=50000, open_=50000, high=51000, low=49000)
        sell_bar = make_bar(close=55000, open_=55000, high=56000, low=54000)

        # 买入 1 BTC @ 50000
        broker.send_order(self._buy_request(qty="1"))
        broker.match_orders(buy_bar)

        # 卖出 1 BTC @ 55000 → 盈利 5000
        broker.send_order(self._sell_request(qty="1"))
        broker.match_orders(sell_bar)

        assert broker.total_realized_pnl == Decimal("5000")
        # 平仓后现金 = 50000 + 55000（卖出所得）= 105000
        bal = broker.get_balance()
        assert bal.available_balance == Decimal("105000")
        assert bal.total_equity == Decimal("105000")

    def test_cancel_order(self):
        """撤销挂单"""
        broker = self._broker()
        order = broker.send_order(self._buy_request(qty="1", price=45000))
        success = broker.cancel_order(order.order_id, "BTC-USDT")
        assert success is True

        # 撤销后不应再成交
        bar = make_bar(close=50000, open_=44000, high=51000, low=43000)
        trades = broker.match_orders(bar)
        assert len(trades) == 0

    def test_equity_curve_recorded(self):
        """每根 K 线后都应记录权益快照"""
        broker = self._broker()
        for i in range(5):
            bar = make_bar(close=50000 + i * 100)
            broker.match_orders(bar)

        assert len(broker.equity_curve) == 5

    def test_on_order_callback_triggered(self):
        """订单状态变化时触发回调"""
        broker = self._broker()
        received = []
        broker.on_order = lambda o: received.append(o.status)

        broker.send_order(self._buy_request(qty="1"))
        bar = make_bar(close=50000, open_=50000)
        broker.match_orders(bar)

        assert OrderStatus.SUBMITTED in received
        assert OrderStatus.FILLED in received

    def test_on_trade_callback_triggered(self):
        """成交时触发成交回调"""
        broker = self._broker()
        received = []
        broker.on_trade = lambda t: received.append(t.price)

        broker.send_order(self._buy_request(qty="1"))
        bar = make_bar(close=50000, open_=49000)
        broker.match_orders(bar)

        assert len(received) == 1
        assert received[0] == Decimal("49000")


# ─────────────────────── PerformanceAnalyzer 测试 ────────────


class TestPerformanceAnalyzer:

    def _make_equity_curve(
        self, values: list[float], start: datetime | None = None
    ) -> list[tuple[datetime, Decimal]]:
        base = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            (base + timedelta(hours=i), Decimal(str(v)))
            for i, v in enumerate(values)
        ]

    def test_total_return(self):
        """总收益率计算正确"""
        curve = self._make_equity_curve([100_000, 110_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        m = analyzer.compute()
        assert abs(m["total_return_pct"] - 10.0) < 0.01

    def test_max_drawdown_flat(self):
        """无回撤时最大回撤为 0"""
        curve = self._make_equity_curve([100_000, 101_000, 102_000, 103_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        m = analyzer.compute()
        assert m["max_drawdown_pct"] == pytest.approx(0.0, abs=0.01)

    def test_max_drawdown_with_recovery(self):
        """回撤后恢复，计算正确"""
        # 峰值 120k → 跌至 90k → 回撤 = 25%
        curve = self._make_equity_curve([100_000, 120_000, 90_000, 110_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        m = analyzer.compute()
        assert m["max_drawdown_pct"] == pytest.approx(25.0, rel=0.01)

    def test_sharpe_ratio_zero_vol(self):
        """零波动率时夏普比率为 0（避免除零）"""
        curve = self._make_equity_curve([100_000, 100_000, 100_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        m = analyzer.compute()
        assert m["sharpe_ratio"] == 0.0

    def test_no_trades_statistics(self):
        """无交易记录时交易统计为 0"""
        curve = self._make_equity_curve([100_000, 105_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        m = analyzer.compute()
        assert m["total_trades"] == 0
        assert m["win_rate_pct"] == 0.0

    def test_summary_output_contains_key_fields(self):
        """summary() 输出包含核心字段"""
        curve = self._make_equity_curve([100_000, 110_000, 105_000, 115_000])
        analyzer = PerformanceAnalyzer(
            equity_curve=curve,
            trades=[],
            initial_capital=Decimal("100000"),
        )
        summary = analyzer.summary()
        assert "夏普" in summary
        assert "最大回撤" in summary
        assert "总收益率" in summary
        assert "年化收益率" in summary


# ─────────────────────── BacktestEngine 端到端测试 ─────────────


class TestBacktestEngine:
    """端到端回测测试，使用简单的趋势行情"""

    def _simple_bars(self, n: int = 100, start: float = 50000.0, step: float = 100.0) -> list[BarData]:
        """生成单调上涨 K 线序列"""
        base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = []
        price = start
        for i in range(n):
            bars.append(BarData(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                interval="1H",
                open=Decimal(str(price)),
                high=Decimal(str(price + step * 0.5)),
                low=Decimal(str(price - step * 0.1)),
                close=Decimal(str(price + step)),
                volume=Decimal("100"),
                volume_ccy=Decimal("0"),
                timestamp=base_ts + timedelta(hours=i),
            ))
            price += step
        return bars

    def test_engine_runs_without_error(self):
        """回测引擎可以正常运行，不抛出异常"""
        bars = self._simple_bars(200)
        engine = BacktestEngine(
            strategy_class=DoubleMaStrategy,
            strategy_config={
                "fast_period": 5,
                "slow_period": 20,
                "interval": "1H",
                "quantity": "0.1",
            },
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            taker_fee=Decimal("0"),
            maker_fee=Decimal("0"),
            warmup_bars=20,
        )
        metrics = engine.run()
        assert isinstance(metrics, dict)
        assert "total_return_pct" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown_pct" in metrics

    def test_uptrend_generates_positive_return(self):
        """
        持续上涨行情下，双均线策略应产生正收益。
        （快线持续在慢线上方 → 持续持多头）
        """
        bars = self._simple_bars(300, step=50.0)  # 持续上涨
        engine = BacktestEngine(
            strategy_class=DoubleMaStrategy,
            strategy_config={
                "fast_period": 5,
                "slow_period": 20,
                "interval": "1H",
                "quantity": "0.1",
            },
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            taker_fee=Decimal("0"),
            maker_fee=Decimal("0"),
            warmup_bars=20,
        )
        metrics = engine.run()
        # 上涨行情中双均线应有正收益
        assert metrics["total_return_pct"] > 0

    def test_performance_object_available_after_run(self):
        """run() 后 performance 对象可用"""
        bars = self._simple_bars(100)
        engine = BacktestEngine(
            strategy_class=DoubleMaStrategy,
            strategy_config={"fast_period": 5, "slow_period": 20, "interval": "1H", "quantity": "0.1"},
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            warmup_bars=20,
        )
        engine.run()
        assert engine.performance is not None
        summary = engine.performance.summary()
        assert len(summary) > 0

    def test_equity_curve_length_matches_bars(self):
        """权益曲线快照数量应等于或略多于 K 线数量（含强制平仓额外快照）"""
        n = 150
        bars = self._simple_bars(n)
        engine = BacktestEngine(
            strategy_class=DoubleMaStrategy,
            strategy_config={"fast_period": 5, "slow_period": 20, "interval": "1H", "quantity": "0.1"},
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            warmup_bars=20,
        )
        engine.run()
        # 每根 K 线至少产生 1 个快照，总共应 >= n 个
        # 注意：强制平仓可能再多 1 个，所以 >= n
        assert len(engine.broker.equity_curve) >= n

    def test_cancel_all_pending_on_end(self):
        """回测结束后所有持仓应被强制平仓"""
        bars = self._simple_bars(100)
        engine = BacktestEngine(
            strategy_class=DoubleMaStrategy,
            strategy_config={
                "fast_period": 5, "slow_period": 20,
                "interval": "1H", "quantity": "0.1",
            },
            inst_id="BTC-USDT",
            bars=bars,
            initial_capital=Decimal("100000"),
            taker_fee=Decimal("0"),
            maker_fee=Decimal("0"),
            warmup_bars=20,
        )
        engine.run()
        # 回测结束后所有持仓应为 0
        remaining = engine.broker.get_all_positions()
        for pos in remaining:
            assert pos.quantity == Decimal("0"), f"持仓未平: {pos}"
