"""测试：风控引擎"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType
from core.event_bus import EventBus, EventType
from core.exceptions import (
    ConsecutiveLossError,
    DailyLossLimitError,
    OrderValidationError,
    PositionLimitError,
)
from core.models import (
    BalanceData, CurrencyBalance, Instrument,
    OrderRequest, PositionData, TickData,
)
from risk.risk_engine import RiskEngine
from risk.order_validator import OrderValidator
from risk.rate_limiter import RateLimiter
from utils.helpers import now_utc


# ── 工厂函数 ─────────────────────────────────────────────────

def make_balance(equity: float = 10000.0) -> BalanceData:
    from core.enums import Exchange
    eq = Decimal(str(equity))
    return BalanceData(
        exchange=Exchange.OKX,
        total_equity=eq,
        available_balance=eq,
        frozen_balance=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        details=[CurrencyBalance(
            currency="USDT",
            available=eq,
            frozen=Decimal("0"),
            equity=eq,
            equity_usd=eq,
        )],
        update_time=now_utc(),
    )


def make_tick(inst_id: str = "BTC-USDT", price: float = 50000.0) -> TickData:
    p = Decimal(str(price))
    return TickData(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        last_price=p,
        bid_price=p - Decimal("10"),
        ask_price=p + Decimal("10"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
        high_24h=p + Decimal("1000"),
        low_24h=p - Decimal("1000"),
        volume_24h=Decimal("1000"),
        volume_ccy_24h=Decimal("50000000"),
        timestamp=now_utc(),
    )


def make_instrument(inst_id: str = "BTC-USDT") -> Instrument:
    return Instrument(
        inst_id=inst_id,
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


def make_request(price: float = 50000.0, qty: float = 0.001) -> OrderRequest:
    return OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal(str(price)),
        quantity=Decimal(str(qty)),
        margin_mode=MarginMode.CASH,
    )


# ── 测试：订单校验器 ─────────────────────────────────────────

class TestOrderValidator:
    def setup_method(self):
        self.validator = OrderValidator(price_deviation_limit=0.1)
        self.inst = make_instrument()

    def test_valid_order(self):
        req = make_request(price=50000.0, qty=0.001)
        # should not raise
        self.validator.validate(req, self.inst, current_price=Decimal("50000"))

    def test_price_deviation_too_large(self):
        req = make_request(price=60000.0, qty=0.001)
        with pytest.raises(OrderValidationError, match="偏离"):
            self.validator.validate(req, self.inst, current_price=Decimal("50000"))

    def test_quantity_below_minimum(self):
        req = make_request(qty=0.000001)  # 小于 minSz=0.00001
        with pytest.raises(OrderValidationError, match="小于最小下单量"):
            self.validator.validate(req, self.inst)

    def test_quantity_precision_error(self):
        req = make_request(qty=0.000015)  # 不是 0.00001 整数倍
        with pytest.raises(OrderValidationError, match="精度不符合"):
            self.validator.validate(req, self.inst)

    def test_auto_adjust(self):
        req = make_request(price=50001.23, qty=0.001234)
        adjusted = self.validator.auto_adjust(req, self.inst)
        # 价格应截断到 tick_size=0.1
        assert adjusted.price == Decimal("50001.2")
        # 数量应截断到 lot_size=0.00001
        assert adjusted.quantity == Decimal("0.00123")


# ── 测试：亏损限额 ────────────────────────────────────────────

class TestLossLimit:
    def setup_method(self):
        from risk.loss_limit import LossLimitChecker
        self.checker = LossLimitChecker(
            max_daily_loss_pct=0.05,
            max_single_loss_pct=0.02,
            max_consecutive_losses=3,
        )
        self.checker.update_equity(Decimal("10000"))

    def test_no_loss_passes(self):
        req = make_request()
        self.checker.check(req)  # should not raise

    def test_consecutive_losses_trigger_halt(self):
        from core.models import TradeData
        from core.enums import OrderSide

        # 模拟 3 次连续亏损
        for i in range(3):
            dummy_trade = TradeData(
                trade_id=str(i),
                order_id=str(i),
                inst_id="BTC-USDT",
                exchange=Exchange.OKX,
                side=OrderSide.BUY,
                price=Decimal("50000"),
                quantity=Decimal("0.001"),
                fee=Decimal("-1"),
                fee_ccy="USDT",
                timestamp=now_utc(),
            )
            self.checker.on_trade(dummy_trade, Decimal("-100"))

        assert self.checker.is_halted

        req = make_request()
        with pytest.raises((ConsecutiveLossError, DailyLossLimitError)):
            self.checker.check(req)

    def test_reset_halt(self):
        self.checker._is_halted = True
        self.checker.reset_halt()
        assert not self.checker.is_halted


# ── 测试：频率限制器 ─────────────────────────────────────────

class TestRateLimiter:
    def test_basic_rate_limit(self):
        import time
        limiter = RateLimiter(max_calls=3, period=1.0)

        start = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        elapsed = time.monotonic() - start

        # 前3次应立即完成
        assert elapsed < 0.5

    def test_rate_limit_blocks_on_overflow(self):
        import time
        limiter = RateLimiter(max_calls=2, period=0.5)

        start = time.monotonic()
        for _ in range(3):  # 第3次应等待
            limiter.acquire()
        elapsed = time.monotonic() - start

        # 第3次触发限速，至少等0.5s
        assert elapsed >= 0.4


# ── 测试：风控引擎集成 ───────────────────────────────────────

class TestRiskEngine:
    def setup_method(self):
        self.event_bus = EventBus()
        self.engine = RiskEngine(self.event_bus, {
            "enabled": True,
            "max_daily_loss_pct": 0.05,
            "max_single_loss_pct": 0.02,
            "max_position_pct": 0.30,
            "max_consecutive_losses": 5,
            "price_deviation_limit": 0.10,
        })
        self.engine.start()

        # 注入余额和价格
        self.event_bus.publish(EventType.BALANCE_UPDATED, make_balance(10000), source="test")
        self.event_bus.publish(EventType.TICK, make_tick("BTC-USDT", 50000), source="test")

    def teardown_method(self):
        self.engine.stop()

    def test_normal_order_passes(self):
        req = make_request(price=50000.0, qty=0.001)
        inst = make_instrument()
        self.engine.check_order(req, inst)  # should NOT raise

    def test_price_deviation_rejected(self):
        req = make_request(price=60000.0, qty=0.001)  # 20% 偏离
        inst = make_instrument()
        with pytest.raises(OrderValidationError):
            self.engine.check_order(req, inst)

    def test_disabled_engine_always_passes(self):
        self.engine.enabled = False
        req = make_request(price=99999.0, qty=100)   # 明显超限价格
        # 禁用风控时不应抛出异常
        self.engine.check_order(req)
        self.engine.enabled = True

    def test_risk_breach_event_published(self):
        alerts = []
        self.event_bus.subscribe(EventType.RISK_BREACH, lambda e: alerts.append(e))

        req = make_request(price=60000.0, qty=0.001)  # 价格偏离
        inst = make_instrument()
        try:
            self.engine.check_order(req, inst)
        except Exception:
            pass

        assert len(alerts) > 0
