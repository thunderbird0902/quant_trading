#!/usr/bin/env python3
"""
示例 05：策略委托（止盈止损、计划委托、移动止损）

演示：
1. 以市价简单建仓
2. 对持仓设置止盈止损委托（TP/SL）
3. 设置计划委托（突破某价格后自动下单）
4. 查询策略委托列表
5. 撤销策略委托

⚠️  本示例在模拟盘执行
"""

from __future__ import annotations

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType, PositionSide
from core.event_bus import EventBus
from core.models import OrderRequest
from gateway.okx.okx_gateway import OKXGateway
from gateway.okx.okx_algo_trader import OKXAlgoTrader
from utils.config_loader import load_okx_config
from utils.helpers import round_to


def main():
    config = load_okx_config()
    okx_cfg = config["okx"]

    event_bus = EventBus()
    gateway = OKXGateway(event_bus, okx_cfg)
    gateway.connect()

    INST_ID = "BTC-USDT-SWAP"

    # 获取当前价格
    tick = gateway.get_ticker(INST_ID)
    price = tick.last_price
    print(f"当前价格: ${float(price):,.2f}\n")

    # ── 1. 止盈止损委托（TP/SL）────────────────────────────────────
    print("=" * 60)
    print("🎯 设置止盈止损委托（conditional）")
    print("=" * 60)

    tp_price = round_to(price * Decimal("1.05"), Decimal("0.1"))   # 止盈 +5%
    sl_price = round_to(price * Decimal("0.97"), Decimal("0.1"))   # 止损 -3%

    print(f"止盈触发价: ${float(tp_price):,.2f} （+5%）")
    print(f"止损触发价: ${float(sl_price):,.2f} （-3%）")

    try:
        algo_trader = gateway._algo_trader
        tp_sl_order = algo_trader.send_tp_sl_order(
            inst_id=INST_ID,
            side=OrderSide.SELL,              # 平多（卖出）
            quantity="1",
            margin_mode=MarginMode.CROSS,
            position_side=PositionSide.NET,
            tp_trigger_price=str(tp_price),
            tp_order_price="-1",              # 市价止盈
            sl_trigger_price=str(sl_price),
            sl_order_price="-1",              # 市价止损
        )
        print(f"\n✅ 止盈止损委托已提交")
        print(f"   algoId:  {tp_sl_order.algo_id}")
        print(f"   状态:    {tp_sl_order.status.value}")
        tp_sl_algo_id = tp_sl_order.algo_id
    except Exception as e:
        print(f"止盈止损委托失败: {e}")
        tp_sl_algo_id = None

    # ── 2. 计划委托（突破 +2% 后买入）────────────────────────────
    print("\n" + "=" * 60)
    print("📌 设置计划委托（trigger）")
    print("=" * 60)

    trigger_price = round_to(price * Decimal("1.02"), Decimal("0.1"))
    print(f"触发价格: ${float(trigger_price):,.2f}（当前价 +2%，突破后触发买入）")

    try:
        trigger_order = algo_trader.send_trigger_order(
            inst_id=INST_ID,
            side=OrderSide.BUY,
            quantity="1",
            margin_mode=MarginMode.CROSS,
            trigger_price=str(trigger_price),
            order_price="-1",                 # 触发后市价成交
            trigger_price_type="last",
            position_side=PositionSide.NET,
        )
        print(f"\n✅ 计划委托已提交")
        print(f"   algoId:  {trigger_order.algo_id}")
        trigger_algo_id = trigger_order.algo_id
    except Exception as e:
        print(f"计划委托失败: {e}")
        trigger_algo_id = None

    # ── 3. 移动止损（trailing stop）──────────────────────────────
    print("\n" + "=" * 60)
    print("📈 设置移动止损委托（move_order_stop）")
    print("=" * 60)

    print("回调比例: 3%（价格回落 3% 时触发平仓）")

    try:
        trailing_order = algo_trader.send_trailing_stop_order(
            inst_id=INST_ID,
            side=OrderSide.SELL,
            quantity="1",
            margin_mode=MarginMode.CROSS,
            callback_ratio="0.03",            # 3% 回调
            position_side=PositionSide.NET,
        )
        print(f"\n✅ 移动止损委托已提交")
        print(f"   algoId:  {trailing_order.algo_id}")
        trailing_algo_id = trailing_order.algo_id
    except Exception as e:
        print(f"移动止损委托失败: {e}")
        trailing_algo_id = None

    # ── 4. 查询策略委托列表 ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📋 当前策略委托列表")
    print("=" * 60)

    time.sleep(1)
    try:
        # 查询所有未完成策略委托
        algo_orders = algo_trader.get_algo_orders()
        print(f"共 {len(algo_orders)} 笔策略委托")
        for o in algo_orders[:10]:
            print(
                f"   algoId={o.algo_id} | "
                f"{o.order_type.value} | "
                f"{o.side.value} | "
                f"{o.quantity}张 | "
                f"{o.status.value}"
            )
    except Exception as e:
        print(f"查询策略委托失败: {e}")

    # ── 5. 撤销策略委托 ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("❌ 撤销策略委托")
    print("=" * 60)

    for algo_id, name in [
        (tp_sl_algo_id, "止盈止损"),
        (trigger_algo_id, "计划委托"),
        (trailing_algo_id, "移动止损"),
    ]:
        if algo_id:
            try:
                success = algo_trader.cancel_algo_order(algo_id, INST_ID)
                print(f"撤销{name} {algo_id}: {'成功' if success else '失败'}")
            except Exception as e:
                print(f"撤销{name}失败: {e}")

    gateway.disconnect()
    print("\n✅ 策略委托示例完成")


if __name__ == "__main__":
    main()
