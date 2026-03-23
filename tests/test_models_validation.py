"""测试：core/models.py 数据校验"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType
from core.models import Instrument, OrderRequest, TradeData
from utils.helpers import now_utc


class TestInstrumentValidation:
    """Instrument 数据校验"""

    def test_decimal_precision_correct(self):
        """Decimal 精度正确"""
        inst = Instrument(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            market_type=MarketType.SPOT,
            base_ccy="BTC",
            quote_ccy="USDT",
            tick_size="0.1",
            lot_size="0.00001",
            min_size="0.00001",
            max_limit_size="9999",
            max_market_size="999",
            contract_value="0",
            contract_multiplier="1",
            contract_value_ccy="USDT",
            state="live",
        )
        assert inst.tick_size == Decimal("0.1")
        assert inst.lot_size == Decimal("0.00001")
        assert isinstance(inst.tick_size, Decimal)

    def test_negative_tick_size_rejected(self):
        """非法值（负数 tick_size）被拒绝"""
        with pytest.raises(ValueError, match="tick_size must be positive"):
            Instrument(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                market_type=MarketType.SPOT,
                base_ccy="BTC",
                quote_ccy="USDT",
                tick_size="-0.1",
                lot_size="0.00001",
                min_size="0.00001",
                max_limit_size="9999",
                max_market_size="999",
                contract_value="0",
                contract_multiplier="1",
                contract_value_ccy="USDT",
                state="live",
            )

    def test_zero_lot_size_rejected(self):
        """非法值（零 lot_size）被拒绝"""
        with pytest.raises(ValueError, match="lot_size must be positive"):
            Instrument(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                market_type=MarketType.SPOT,
                base_ccy="BTC",
                quote_ccy="USDT",
                tick_size="0.1",
                lot_size="0",
                min_size="0.00001",
                max_limit_size="9999",
                max_market_size="999",
                contract_value="0",
                contract_multiplier="1",
                contract_value_ccy="USDT",
                state="live",
            )


class TestOrderRequestValidation:
    """OrderRequest 数据校验"""

    def test_negative_quantity_rejected(self):
        """非法值（负数数量）被拒绝"""
        with pytest.raises(ValueError, match="quantity must be positive"):
            OrderRequest(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("50000"),
                quantity=Decimal("-1"),
                margin_mode=MarginMode.CASH,
            )

    def test_zero_quantity_rejected(self):
        """非法值（零数量）被拒绝"""
        with pytest.raises(ValueError, match="quantity must be positive"):
            OrderRequest(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("50000"),
                quantity=Decimal("0"),
                margin_mode=MarginMode.CASH,
            )

    def test_negative_price_rejected(self):
        """非法值（负数价格）被拒绝"""
        with pytest.raises(ValueError, match="price must be positive"):
            OrderRequest(
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("-50000"),
                quantity=Decimal("1"),
                margin_mode=MarginMode.CASH,
            )


class TestTradeDataValidation:
    """TradeData 数据校验"""

    def test_negative_price_rejected(self):
        """非法值（负数价格）被拒绝"""
        with pytest.raises(ValueError, match="price must be positive"):
            TradeData(
                trade_id="1",
                order_id="1",
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("-50000"),
                quantity=Decimal("1"),
                fee=Decimal("0"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )

    def test_zero_quantity_rejected(self):
        """非法值（零数量）被拒绝"""
        with pytest.raises(ValueError, match="quantity must be positive"):
            TradeData(
                trade_id="1",
                order_id="1",
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("50000"),
                quantity=Decimal("0"),
                fee=Decimal("0"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )
