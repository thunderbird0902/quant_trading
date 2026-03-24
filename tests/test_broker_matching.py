"""测试：backtest/broker.py 撮合逻辑"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from core.enums import Exchange, MarginMode, OrderSide, OrderType
from core.models import BarData, OrderRequest
from backtest.broker import FeeSchedule, FeeTier, SimulatedBroker


def make_bar(close: float, open_: float = None, high: float = None, low: float = None) -> BarData:
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) * 1.01
    if low is None:
        low = min(open_, close) * 0.99
    return BarData(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        interval="1H",
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
        volume_ccy=Decimal("0"),
        timestamp=datetime.now(timezone.utc),
    )


class TestBrokerMatching:
    """撮合逻辑测试"""

    def test_limit_buy_no_fill_when_price_not_reached(self):
        """限价单在价格未触及时不成交"""
        broker = SimulatedBroker(
            initial_capital=Decimal("100000"),
            fee_schedule=FeeSchedule([FeeTier(Decimal("0"), Decimal("0"), Decimal("0"))]),
        )

        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("45000"),
            quantity=Decimal("1"),
            margin_mode=MarginMode.CASH,
        )
        broker.send_order(req)

        # K线最低价 48000 > 委托价 45000，不成交
        bar = make_bar(close=50000, open_=50000, high=51000, low=48000)
        trades = broker.match_orders(bar)

        assert len(trades) == 0

    def test_limit_buy_fills_when_price_reached(self):
        """限价单触及时成交"""
        broker = SimulatedBroker(
            initial_capital=Decimal("100000"),
            fee_schedule=FeeSchedule([FeeTier(Decimal("0"), Decimal("0"), Decimal("0"))]),
        )

        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("48000"),
            quantity=Decimal("1"),
            margin_mode=MarginMode.CASH,
        )
        broker.send_order(req)

        # K线最低价 47000 <= 委托价 48000，成交
        bar = make_bar(close=50000, open_=50000, high=51000, low=47000)
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        assert trades[0].price == Decimal("48000")

    def test_market_order_slippage(self):
        """市价单滑点计算正确"""
        broker = SimulatedBroker(
            initial_capital=Decimal("100000"),
            fee_schedule=FeeSchedule([FeeTier(Decimal("0"), Decimal("0"), Decimal("0"))]),
            slippage_pct=Decimal("0.001")  # 0.1% 滑点
        )

        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            margin_mode=MarginMode.CASH,
        )
        broker.send_order(req)

        bar = make_bar(close=50000, open_=50000)
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        # 买入滑点向上：50000 * (1 + 0.001) = 50050
        assert trades[0].price == Decimal("50000") * (Decimal("1") + Decimal("0.001"))

    def test_fee_calculation_taker(self):
        """手续费计算正确（taker）"""
        broker = SimulatedBroker(
            initial_capital=Decimal("100000"),
            fee_schedule=FeeSchedule([FeeTier(Decimal("0"), Decimal("0.001"), Decimal("0"))]),
        )

        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            margin_mode=MarginMode.CASH,
        )
        broker.send_order(req)

        bar = make_bar(close=50000, open_=50000)
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        # 手续费 = 50000 * 1 * 0.001 = 50
        assert trades[0].fee == Decimal("50")

    def test_fee_calculation_maker(self):
        """手续费计算正确（maker）"""
        broker = SimulatedBroker(
            initial_capital=Decimal("100000"),
            fee_schedule=FeeSchedule([FeeTier(Decimal("0"), Decimal("0"), Decimal("0.0005"))]),
        )

        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            quantity=Decimal("1"),
            margin_mode=MarginMode.CASH,
        )
        broker.send_order(req)

        bar = make_bar(close=50000, open_=50000, low=49000)
        trades = broker.match_orders(bar)

        assert len(trades) == 1
        # 手续费 = 50000 * 1 * 0.0005 = 25
        assert trades[0].fee == Decimal("25")
