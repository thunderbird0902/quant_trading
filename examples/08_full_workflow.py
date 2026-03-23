#!/usr/bin/env python3
"""
示例 08：完整工作流

执行顺序：
1. 初始化引擎（EventBus + MainEngine + OKXGateway + RiskEngine）
2. 连接 OKX 模拟盘
3. 启动风控引擎
4. 订阅 BTC-USDT-SWAP WebSocket 行情（后台线程）
5. 查询账户余额和当前价格
6. 执行限价建仓（多头 1 张）
7. 监控持仓 10 秒
8. 设置止损委托
9. 平仓并打印盈亏报告
10. 优雅退出

⚠️  本示例在模拟盘执行
"""

from __future__ import annotations

import sys
import os
import time
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType, PositionSide
from core.event_bus import EventBus, EventType, Event
from core.engine import MainEngine
from gateway.okx.okx_gateway import OKXGateway
from risk.risk_engine import RiskEngine
from utils.config_loader import load_okx_config
from utils.helpers import round_to, now_utc


def main():
    print("=" * 70)
    print("🚀 量化交易系统 - 完整工作流演示")
    print("=" * 70)

    # ── 步骤 1：初始化引擎 ───────────────────────────────────────
    print("\n[1/9] 初始化引擎...")
    config = load_okx_config()
    okx_cfg = config["okx"]

    engine = MainEngine({"system": {"log_level": "WARNING"}})
    event_bus = engine.event_bus

    gateway = OKXGateway(event_bus, okx_cfg)
    engine.add_gateway(gateway)

    # 初始化风控引擎
    risk_config = {
        "enabled": True,
        "max_daily_loss_pct": 0.05,
        "max_single_loss_pct": 0.02,
        "max_position_pct": 0.50,
        "max_consecutive_losses": 5,
        "price_deviation_limit": 0.10,
    }
    risk_engine = RiskEngine(event_bus, risk_config)
    engine.set_risk_engine(risk_engine)
    print("   ✓ 引擎初始化完成")

    # ── 步骤 2：连接 OKX ─────────────────────────────────────────
    print("\n[2/9] 连接 OKX 模拟盘...")
    engine.connect(Exchange.OKX)
    print("   ✓ 连接成功")

    # ── 步骤 3：启动风控 ─────────────────────────────────────────
    print("\n[3/9] 启动风控引擎...")
    risk_engine.start()
    print("   ✓ 风控已启动")

    # ── 步骤 4：事件监听 ─────────────────────────────────────────
    print("\n[4/9] 注册事件监听...")

    received_events = []

    def on_any_event(event: Event):
        received_events.append(event.type.value)
        if event.type in (EventType.ORDER_FILLED, EventType.POSITION_UPDATED):
            print(f"   📣 事件: {event.type.value}")

    event_bus.subscribe_all(on_any_event)
    print("   ✓ 事件监听已注册")

    # ── 步骤 5：查询余额和行情 ────────────────────────────────────
    print("\n[5/9] 查询账户余额和行情...")
    INST_ID = "BTC-USDT-SWAP"

    balance = engine.get_balance(Exchange.OKX)
    print(f"   总权益:   ${float(balance.total_equity):,.2f} USDT")

    tick = engine.get_ticker(Exchange.OKX, INST_ID)
    current_price = tick.last_price
    print(f"   当前价格: ${float(current_price):,.2f}")

    # ── 步骤 6：限价建仓 ─────────────────────────────────────────
    print("\n[6/9] 提交限价开多委托...")
    tick_size = Decimal("0.1")
    open_price = round_to(current_price * Decimal("1.003"), tick_size)

    from core.models import OrderRequest
    request = OrderRequest(
        inst_id=INST_ID,
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=open_price,
        quantity=Decimal("1"),
        margin_mode=MarginMode.CROSS,
        position_side=PositionSide.NET,
    )

    try:
        order = engine.send_order(request)
        print(f"   ✓ 委托成功 orderId={order.order_id} price=${float(open_price):,.2f}")
    except Exception as e:
        print(f"   ✗ 委托失败: {e}")
        engine.stop()
        return

    # ── 步骤 7：监控持仓 10 秒 ───────────────────────────────────
    print("\n[7/9] 监控持仓（10秒）...")
    for i in range(10):
        time.sleep(1)
        positions = engine.get_positions(Exchange.OKX, INST_ID)
        if positions:
            pos = positions[0]
            print(f"   {i+1}s  持仓={pos.quantity}张 "
                  f"均价=${float(pos.avg_price):,.2f} "
                  f"pnl={float(pos.unrealized_pnl):+.2f}U")
        else:
            # 检查委托状态
            order_detail = gateway.get_order(order.order_id, INST_ID)
            print(f"   {i+1}s  委托状态={order_detail.status.value}（等待成交）")

    # ── 步骤 8：设置止损 ─────────────────────────────────────────
    print("\n[8/9] 设置止损委托...")
    sl_price = round_to(open_price * Decimal("0.97"), tick_size)

    try:
        algo_trader = gateway._algo_trader
        sl_order = algo_trader.send_tp_sl_order(
            inst_id=INST_ID,
            side=OrderSide.SELL,
            quantity="1",
            margin_mode=MarginMode.CROSS,
            position_side=PositionSide.NET,
            sl_trigger_price=str(sl_price),
            sl_order_price="-1",
        )
        print(f"   ✓ 止损委托 algoId={sl_order.algo_id} 触发价=${float(sl_price):,.2f}")
    except Exception as e:
        print(f"   止损设置失败: {e}")
        sl_order = None

    # ── 步骤 9：平仓并报告 ───────────────────────────────────────
    print("\n[9/9] 撤销委托并平仓...")

    # 撤销止损委托
    if sl_order and sl_order.algo_id:
        try:
            algo_trader.cancel_algo_order(sl_order.algo_id, INST_ID)
            print("   ✓ 止损委托已撤销")
        except Exception as e:
            print(f"   止损委托撤销失败: {e}")

    # 撤销原始委托（如果还未成交）
    order_final = gateway.get_order(order.order_id, INST_ID)
    if order_final.is_active:
        engine.cancel_order(Exchange.OKX, order.order_id, INST_ID)
        print(f"   ✓ 原始委托已撤销")
    else:
        # 已成交，市价平仓
        positions = engine.get_positions(Exchange.OKX, INST_ID)
        if positions:
            close_request = OrderRequest(
                inst_id=INST_ID,
                exchange=Exchange.OKX,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=positions[0].quantity,
                margin_mode=MarginMode.CROSS,
                position_side=PositionSide.NET,
            )
            close_order = engine.send_order(close_request)
            print(f"   ✓ 市价平仓 orderId={close_order.order_id}")

    # ── 盈亏报告 ──────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("📊 盈亏报告")
    print("=" * 70)

    final_balance = engine.get_balance(Exchange.OKX)
    print(f"最终总权益:   ${float(final_balance.total_equity):,.2f} USDT")
    print(f"总权益变化:   ${float(final_balance.total_equity - balance.total_equity):+,.2f} USDT")

    risk_status = risk_engine.get_status()
    loss_info = risk_status["loss_limit"]
    print(f"今日累计盈亏: ${loss_info['daily_pnl']:+.2f} USDT")
    print(f"连续亏损笔数: {loss_info['consecutive_losses']}")
    print(f"风控状态:     {'已停止' if loss_info['is_halted'] else '正常'}")

    print(f"\n共接收事件: {len(received_events)} 条")
    from collections import Counter
    for evt, cnt in Counter(received_events).most_common(10):
        print(f"   {evt}: {cnt}")

    # ── 清理 ────────────────────────────────────────────────────
    risk_engine.stop()
    engine.disconnect(Exchange.OKX)
    print("\n✅ 完整工作流演示结束")


if __name__ == "__main__":
    main()
