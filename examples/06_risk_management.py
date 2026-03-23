#!/usr/bin/env python3
"""
示例 06：风控功能演示

演示：
1. 初始化风控引擎，配置各项限制
2. 模拟一个超出仓位限制的下单（被拒绝）
3. 模拟一个超出价格偏离的下单（被拒绝）
4. 模拟一个正常下单（通过风控）
5. 展示风控状态
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType
from core.event_bus import EventBus, EventType, Event
from core.models import OrderRequest, BalanceData, CurrencyBalance
from core.exceptions import RiskError, PositionLimitError, OrderValidationError
from risk.risk_engine import RiskEngine
from utils.helpers import now_utc


def main():
    print("=" * 60)
    print("🛡️  风控引擎演示")
    print("=" * 60)

    # ── 初始化风控引擎 ────────────────────────────────────────────
    event_bus = EventBus()

    risk_config = {
        "enabled": True,
        "max_daily_loss_pct": 0.05,
        "max_single_loss_pct": 0.02,
        "max_position_pct": 0.30,
        "max_total_position_pct": 0.90,
        "max_consecutive_losses": 5,
        "price_deviation_limit": 0.10,
        "per_symbol_limits": {
            "BTC-USDT": {"max_qty": 1.0},  # BTC 最多持有 1 个
        },
    }

    risk_engine = RiskEngine(event_bus, risk_config)
    risk_engine.start()

    # 模拟账户总权益 $10,000
    print("\n📊 模拟账户总权益: $10,000 USDT")
    mock_balance = BalanceData(
        exchange=Exchange.OKX,
        total_equity=Decimal("10000"),
        available_balance=Decimal("10000"),
        frozen_balance=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        details=[CurrencyBalance(
            currency="USDT",
            available=Decimal("10000"),
            frozen=Decimal("0"),
            equity=Decimal("10000"),
            equity_usd=Decimal("10000"),
        )],
        update_time=now_utc(),
    )
    # 注入余额更新事件
    event_bus.publish(EventType.BALANCE_UPDATED, mock_balance, source="test")

    # 设置当前 BTC 价格（$50,000）
    from core.models import TickData
    btc_tick = TickData(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        last_price=Decimal("50000"),
        bid_price=Decimal("49990"),
        ask_price=Decimal("50010"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
        high_24h=Decimal("51000"),
        low_24h=Decimal("49000"),
        volume_24h=Decimal("1000"),
        volume_ccy_24h=Decimal("50000000"),
        timestamp=now_utc(),
    )
    event_bus.publish(EventType.TICK, btc_tick, source="test")

    # ── 测试 1：超出价格偏离（委托价偏离当前价 20%）────────────────
    print("\n" + "=" * 60)
    print("❌ 测试 1：价格偏离超限（委托价偏离 20%，限制 10%）")
    print("=" * 60)

    from core.models import Instrument
    btc_usdt_inst = Instrument(
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

    bad_price_request = OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("60000"),   # 偏离当前价 20%，超过限制
        quantity=Decimal("0.001"),
        margin_mode=MarginMode.CASH,
    )

    try:
        risk_engine.check_order(bad_price_request, btc_usdt_inst)
        print("🤔 未被风控拦截（意外）")
    except OrderValidationError as e:
        print(f"✅ 风控正确拦截：{e}")
    except RiskError as e:
        print(f"✅ 风控正确拦截：{e}")

    # ── 测试 2：超出仓位限制 ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("❌ 测试 2：下单金额超出单品种仓位限制（30% × $10,000 = $3,000）")
    print("=" * 60)

    # 买 0.1 BTC @ $50,000 = $5,000，超过仓位上限 $3,000（30%）
    over_position_request = OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        quantity=Decimal("0.1"),   # 0.1 * 50000 = $5000 > $3000
        margin_mode=MarginMode.CASH,
    )

    try:
        risk_engine.check_order(over_position_request, btc_usdt_inst)
        print("🤔 未被风控拦截（仓位检查需要价格数据）")
    except PositionLimitError as e:
        print(f"✅ 风控正确拦截（仓位超限）：{e}")
    except RiskError as e:
        print(f"✅ 风控正确拦截：{e}")

    # ── 测试 3：正常下单（通过风控）────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ 测试 3：正常下单（$50,000 × 0.001 BTC = $50，通过风控）")
    print("=" * 60)

    normal_request = OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        quantity=Decimal("0.001"),  # 0.001 * 50000 = $50 < $3000
        margin_mode=MarginMode.CASH,
    )

    try:
        risk_engine.check_order(normal_request, btc_usdt_inst)
        print("✅ 风控检查通过，可以下单")
        print(f"   委托价: ${float(normal_request.price):,.2f}")
        print(f"   委托量: {normal_request.quantity} BTC")
        print(f"   名义价值: ${float(normal_request.price * normal_request.quantity):,.2f} USDT")
    except RiskError as e:
        print(f"❌ 风控拦截（意外）：{e}")

    # ── 测试 4：数量精度错误 ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("❌ 测试 4：委托数量精度错误（0.000015 不是 0.00001 整数倍）")
    print("=" * 60)

    bad_qty_request = OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        quantity=Decimal("0.000015"),   # 精度不符合 lotSz=0.00001
        margin_mode=MarginMode.CASH,
    )

    try:
        risk_engine.check_order(bad_qty_request, btc_usdt_inst)
        print("🤔 未被拦截")
    except OrderValidationError as e:
        print(f"✅ 风控正确拦截（精度错误）：{e}")

    # ── 风控状态总结 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📊 风控状态总结")
    print("=" * 60)

    status = risk_engine.get_status()
    print(f"风控状态:     {'启用' if status['enabled'] else '禁用'}")
    loss_status = status["loss_limit"]
    print(f"是否停止交易:  {'是' if loss_status['is_halted'] else '否'}")
    print(f"今日盈亏:      ${loss_status['daily_pnl']:+.2f} USDT")
    print(f"连续亏损笔数:  {loss_status['consecutive_losses']}")
    print(f"总权益:        ${loss_status['total_equity']:,.2f} USDT")

    risk_engine.stop()
    print("\n✅ 风控示例完成")


if __name__ == "__main__":
    main()
