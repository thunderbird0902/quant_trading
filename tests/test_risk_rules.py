"""测试：风控规则 - order_validator, position_limit, loss_limit"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType, PositionSide
from core.exceptions import OrderValidationError, PositionLimitError, DailyLossLimitError, ConsecutiveLossError
from core.models import Instrument, OrderRequest, PositionData, TradeData
from risk.order_validator import OrderValidator
from risk.position_limit import PositionLimitChecker
from risk.loss_limit import LossLimitChecker
from utils.helpers import now_utc


# ── 工厂函数 ─────────────────────────────────────────────────

def make_instrument(inst_id="BTC-USDT", tick_size="0.1", lot_size="0.00001", min_size="0.00001") -> Instrument:
    return Instrument(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        market_type=MarketType.SPOT,
        base_ccy="BTC",
        quote_ccy="USDT",
        tick_size=Decimal(tick_size),
        lot_size=Decimal(lot_size),
        min_size=Decimal(min_size),
        max_limit_size=Decimal("9999"),
        max_market_size=Decimal("999"),
        contract_value=Decimal("0"),
        contract_multiplier=Decimal("1"),
        contract_value_ccy="USDT",
        state="live",
    )


def make_request(side=OrderSide.BUY, price=50000.0, qty=0.001, order_type=OrderType.LIMIT) -> OrderRequest:
    return OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=side,
        order_type=order_type,
        price=Decimal(str(price)) if price else None,
        quantity=Decimal(str(qty)),
        margin_mode=MarginMode.CASH,
    )


# ── OrderValidator 测试 ──────────────────────────────────────

class TestOrderValidatorRules:
    """订单校验器：触发和不触发两种 case"""

    def test_normal_order_passes(self):
        """正常订单通过"""
        validator = OrderValidator(price_deviation_limit=0.1)
        inst = make_instrument()
        req = make_request(price=50000.0, qty=0.001)
        validator.validate(req, inst, current_price=Decimal("50000"))  # 不抛异常

    def test_price_deviation_rejected(self):
        """价格偏离过大被拒"""
        validator = OrderValidator(price_deviation_limit=0.1)
        inst = make_instrument()
        req = make_request(price=60000.0, qty=0.001)  # 偏离 20%
        with pytest.raises(OrderValidationError, match="偏离"):
            validator.validate(req, inst, current_price=Decimal("50000"))

    def test_quantity_below_min_rejected(self):
        """数量过小被拒"""
        validator = OrderValidator()
        inst = make_instrument(min_size="0.001")
        req = make_request(qty=0.0001)  # 小于 minSz
        with pytest.raises(OrderValidationError, match="小于最小下单量"):
            validator.validate(req, inst)

    def test_quantity_precision_rejected(self):
        """数量精度不符被拒"""
        validator = OrderValidator()
        inst = make_instrument(lot_size="0.01")
        req = make_request(qty=0.015)  # 不是 0.01 整数倍
        with pytest.raises(OrderValidationError, match="精度不符合"):
            validator.validate(req, inst)

    def test_price_precision_rejected(self):
        """价格精度不符被拒"""
        validator = OrderValidator()
        inst = make_instrument(tick_size="0.1")
        req = make_request(price=50000.15, qty=0.001)  # 不是 0.1 整数倍
        with pytest.raises(OrderValidationError, match="精度不符合"):
            validator.validate(req, inst)


# ── PositionLimitChecker 测试 ────────────────────────────────

class TestPositionLimitRules:
    """仓位限制：未超限通过、超限被拒"""

    def test_normal_position_passes(self):
        """未超限通过"""
        checker = PositionLimitChecker(max_position_pct=0.3)
        checker.update_equity(Decimal("100000"))
        req = make_request(qty=0.1)  # 0.1 * 50000 = 5000 < 30% * 100000
        checker.check(req, current_price=Decimal("50000"))  # 不抛异常

    def test_position_limit_rejected(self):
        """超限被拒"""
        checker = PositionLimitChecker(max_position_pct=0.3)
        checker.update_equity(Decimal("10000"))
        req = make_request(qty=1.0)  # 1.0 * 50000 = 50000 > 30% * 10000
        with pytest.raises(PositionLimitError, match="仓位占比"):
            checker.check(req, current_price=Decimal("50000"))


# ── LossLimitChecker 测试 ────────────────────────────────────

class TestLossLimitRules:
    """亏损限制：未触发通过、日亏损超限后阻止开仓但允许平仓"""

    def test_no_loss_passes(self):
        """未触发通过"""
        checker = LossLimitChecker(max_daily_loss_pct=0.05)
        checker.update_equity(Decimal("10000"))
        req = make_request()
        checker.check(req)  # 不抛异常

    def test_daily_loss_blocks_new_orders(self):
        """日亏损超限后阻止开仓"""
        checker = LossLimitChecker(max_daily_loss_pct=0.05)
        checker.update_equity(Decimal("10000"))

        # 模拟亏损 600 USDT (6%)
        for i in range(3):
            trade = TradeData(
                trade_id=str(i),
                order_id=str(i),
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("50000"),
                quantity=Decimal("0.001"),
                fee=Decimal("0"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )
            checker.on_trade(trade, Decimal("-200"))  # 每次亏 200

        # 开仓被拒
        req = make_request(side=OrderSide.BUY)
        with pytest.raises(DailyLossLimitError):
            checker.check(req)

    def test_daily_loss_allows_close_orders(self):
        """日亏损超限后允许平仓"""
        checker = LossLimitChecker(max_daily_loss_pct=0.05)
        checker.update_equity(Decimal("10000"))

        # 触发停止
        for i in range(3):
            trade = TradeData(
                trade_id=str(i),
                order_id=str(i),
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("50000"),
                quantity=Decimal("0.001"),
                fee=Decimal("0"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )
            checker.on_trade(trade, Decimal("-200"))

        # 平仓订单（SELL + LONG）允许通过
        req = OrderRequest(
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            margin_mode=MarginMode.CASH,
            position_side=PositionSide.LONG,
        )
        checker.check(req)  # 不抛异常

    def test_consecutive_loss_blocks_orders(self):
        """连续亏损超限后阻止开仓"""
        checker = LossLimitChecker(max_consecutive_losses=3)
        checker.update_equity(Decimal("10000"))

        # 3 次连续亏损
        for i in range(3):
            trade = TradeData(
                trade_id=str(i),
                order_id=str(i),
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("50000"),
                quantity=Decimal("0.001"),
                fee=Decimal("0"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )
            checker.on_trade(trade, Decimal("-10"))

        req = make_request()
        with pytest.raises((ConsecutiveLossError, DailyLossLimitError)):
            checker.check(req)
