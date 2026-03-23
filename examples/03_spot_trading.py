#!/usr/bin/env python3
"""
示例 03：现货交易全流程

演示：
1. 查询 BTC-USDT 当前价格
2. 查询 USDT 余额
3. 以市价稍低处挂限价买单
4. 查询订单状态
5. 撤销订单
6. 市价买入少量（可选，需足够余额）

⚠️  本示例在模拟盘执行，不会产生实际损失
"""

from __future__ import annotations

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType
from core.event_bus import EventBus
from core.models import OrderRequest
from gateway.okx.okx_gateway import OKXGateway
from utils.config_loader import load_okx_config
from utils.helpers import round_to


def main():
    config = load_okx_config()
    okx_cfg = config["okx"]

    event_bus = EventBus()
    gateway = OKXGateway(event_bus, okx_cfg)
    gateway.connect()

    # ── 1. 查询当前价格 ───────────────────────────────────────────
    print("=" * 60)
    print("📈 查询 BTC-USDT 当前价格")
    print("=" * 60)

    tick = gateway.get_ticker("BTC-USDT")
    current_price = tick.last_price
    print(f"最新价: ${float(current_price):,.2f}")

    # 获取产品信息（精度）
    instruments = gateway.get_instruments(MarketType.SPOT)
    btc_usdt_info = next((i for i in instruments if i.inst_id == "BTC-USDT"), None)
    if btc_usdt_info:
        tick_size = btc_usdt_info.tick_size
        min_size = btc_usdt_info.min_size
        print(f"价格精度: {tick_size}，最小下单量: {min_size}")
    else:
        tick_size = Decimal("0.1")
        min_size = Decimal("0.00001")

    # ── 2. 查询余额 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("💰 账户余额")
    print("=" * 60)

    balance = gateway.get_balance()
    usdt_detail = next((d for d in balance.details if d.currency == "USDT"), None)
    usdt_available = usdt_detail.available if usdt_detail else Decimal("0")
    print(f"总权益:   ${float(balance.total_equity):,.2f} USDT")
    print(f"USDT可用: ${float(usdt_available):,.2f}")

    # ── 3. 限价买单（挂在当前价格 1% 以下）────────────────────────
    print("\n" + "=" * 60)
    print("📝 挂限价买单（低于当前价 1%）")
    print("=" * 60)

    limit_price = round_to(current_price * Decimal("0.99"), tick_size)
    buy_qty = Decimal("0.001")  # 0.001 BTC

    print(f"委托价格: ${float(limit_price):,.2f}")
    print(f"委托数量: {buy_qty} BTC")
    print(f"委托金额: ${float(limit_price * buy_qty):,.2f} USDT")

    request = OrderRequest(
        inst_id="BTC-USDT",
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=limit_price,
        quantity=buy_qty,
        margin_mode=MarginMode.CASH,
    )

    order = gateway.send_order(request)
    print(f"\n✅ 下单成功!")
    print(f"   订单 ID:     {order.order_id}")
    print(f"   客户端ID:    {order.client_order_id}")
    print(f"   状态:        {order.status.value}")

    # ── 4. 查询订单状态 ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🔍 查询订单状态")
    print("=" * 60)

    time.sleep(1)
    order_detail = gateway.get_order(order.order_id, "BTC-USDT")
    print(f"订单 ID:      {order_detail.order_id}")
    print(f"状态:         {order_detail.status.value}")
    print(f"委托价:       ${float(order_detail.price):,.2f}")
    print(f"委托量:       {order_detail.quantity} BTC")
    print(f"已成交量:     {order_detail.filled_quantity} BTC")

    # ── 5. 查询未完成订单 ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📋 未完成订单列表")
    print("=" * 60)

    open_orders = gateway.get_open_orders("BTC-USDT")
    print(f"当前未完成订单: {len(open_orders)} 笔")
    for o in open_orders[:5]:
        print(f"   {o.order_id} | {o.side.value} | ${float(o.price):,.2f} | {o.quantity} BTC | {o.status.value}")

    # ── 6. 撤销订单 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("❌ 撤销订单")
    print("=" * 60)

    if order_detail.is_active:
        success = gateway.cancel_order(order.order_id, "BTC-USDT")
        print(f"撤单结果: {'成功' if success else '失败'}")

        time.sleep(0.5)
        order_after = gateway.get_order(order.order_id, "BTC-USDT")
        print(f"撤单后状态: {order_after.status.value}")
    else:
        print(f"订单已{order_detail.status.value}，无需撤单")

    # ── 7. 查询历史成交 ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📜 近期成交记录（最近3天）")
    print("=" * 60)

    trades = gateway.get_trade_history(MarketType.SPOT, days=3)
    print(f"共 {len(trades)} 笔成交")
    for t in trades[:5]:
        print(
            f"   {t.timestamp.strftime('%m-%d %H:%M')} | "
            f"{t.inst_id} | {t.side.value} | "
            f"${float(t.price):,.2f} | {t.quantity} | "
            f"fee={t.fee} {t.fee_ccy}"
        )

    gateway.disconnect()
    print("\n✅ 现货交易示例完成")


if __name__ == "__main__":
    main()
