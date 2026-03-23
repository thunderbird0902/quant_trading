"""测试：核心数据模型"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderStatus, OrderType, PositionSide
from core.models import (
    BalanceData, BarData, CurrencyBalance, Instrument,
    OrderData, OrderRequest, PositionData, TickData,
)
from utils.helpers import now_utc, round_to, safe_decimal, ts_to_datetime


class TestInstrument:
    def test_basic_fields(self):
        inst = Instrument(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            market_type=MarketType.SPOT,
            base_ccy="BTC",
            quote_ccy="USDT",
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.00001"),
            min_size=Decimal("0.00001"),
            max_limit_size=Decimal("9999"),
            max_market_size=Decimal("999"),
            contract_value=Decimal("0"),
            contract_multiplier=Decimal("1"),
            contract_value_ccy="USDT",
            state="live",
        )
        assert inst.inst_id == "BTC-USDT"
        assert inst.exchange == Exchange.OKX
        assert inst.market_type == MarketType.SPOT
        assert inst.tick_size == Decimal("0.1")

    def test_decimal_conversion(self):
        """字段自动转换为 Decimal。"""
        inst = Instrument(
            inst_id="ETH-USDT",
            exchange=Exchange.OKX,
            market_type=MarketType.SPOT,
            base_ccy="ETH",
            quote_ccy="USDT",
            tick_size="0.01",     # 字符串
            lot_size=0.001,       # float
            min_size=Decimal("0.001"),
            max_limit_size="9999",
            max_market_size="999",
            contract_value="0",
            contract_multiplier="1",
            contract_value_ccy="USDT",
            state="live",
        )
        assert isinstance(inst.tick_size, Decimal)
        assert isinstance(inst.lot_size, Decimal)


class TestOrderData:
    def _make_order(self, status=OrderStatus.SUBMITTED) -> OrderData:
        return OrderData(
            order_id="12345",
            client_order_id="qt_abc123",
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            quantity=Decimal("0.001"),
            filled_quantity=Decimal("0"),
            filled_price=Decimal("0"),
            status=status,
            fee=Decimal("0"),
            pnl=Decimal("0"),
            create_time=now_utc(),
            update_time=now_utc(),
        )

    def test_unfilled_quantity(self):
        order = self._make_order()
        assert order.unfilled_quantity == Decimal("0.001")

    def test_is_active(self):
        active_order = self._make_order(OrderStatus.SUBMITTED)
        assert active_order.is_active is True

        filled_order = self._make_order(OrderStatus.FILLED)
        assert filled_order.is_active is False

        cancelled_order = self._make_order(OrderStatus.CANCELLED)
        assert cancelled_order.is_active is False


class TestPositionData:
    def test_notional_value(self):
        pos = PositionData(
            inst_id="BTC-USDT-SWAP",
            exchange=Exchange.OKX,
            position_side=PositionSide.LONG,
            quantity=Decimal("2"),
            avg_price=Decimal("45000"),
            unrealized_pnl=Decimal("1000"),
            unrealized_pnl_ratio=Decimal("0.011"),
            realized_pnl=Decimal("0"),
            leverage=5,
            liquidation_price=Decimal("40000"),
            margin=Decimal("9000"),
            margin_ratio=Decimal("0.1"),
            margin_mode=MarginMode.CROSS,
            mark_price=Decimal("50000"),
            update_time=now_utc(),
        )
        # 2张 × 50000 = 100000
        assert pos.notional_value == Decimal("100000")


class TestHelpers:
    def test_round_to(self):
        assert round_to("1.2345", "0.01") == Decimal("1.23")
        assert round_to("100.5", "5") == Decimal("100")
        assert round_to(Decimal("1.999"), Decimal("0.1")) == Decimal("1.9")

    def test_safe_decimal(self):
        assert safe_decimal("123.45") == Decimal("123.45")
        assert safe_decimal("") == Decimal("0")
        assert safe_decimal(None) == Decimal("0")
        assert safe_decimal("invalid") == Decimal("0")
        assert safe_decimal(None, Decimal("1")) == Decimal("1")

    def test_ts_to_datetime(self):
        # 已知时间戳：2024-01-01 00:00:00 UTC = 1704067200000 ms
        dt = ts_to_datetime(1704067200000)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_ts_to_datetime_string(self):
        dt = ts_to_datetime("1704067200000")
        assert dt.year == 2024


class TestEventBus:
    def test_subscribe_and_publish(self):
        from core.event_bus import EventBus, EventType

        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TICK, handler)
        bus.publish(EventType.TICK, "test_data", source="test")

        assert len(received) == 1
        assert received[0].data == "test_data"
        assert received[0].source == "test"

    def test_unsubscribe(self):
        from core.event_bus import EventBus, EventType

        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.BAR, handler)
        bus.publish(EventType.BAR, "bar1")
        bus.unsubscribe(EventType.BAR, handler)
        bus.publish(EventType.BAR, "bar2")

        assert len(received) == 1

    def test_no_duplicate_subscribe(self):
        from core.event_bus import EventBus, EventType

        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TICK, handler)
        bus.subscribe(EventType.TICK, handler)   # 重复订阅
        bus.publish(EventType.TICK, "data")

        assert len(received) == 1   # 只触发一次

    def test_handler_count(self):
        from core.event_bus import EventBus, EventType

        bus = EventBus()
        bus.subscribe(EventType.TICK, lambda e: None)
        bus.subscribe(EventType.TICK, lambda e: None)
        assert bus.handler_count(EventType.TICK) == 2
