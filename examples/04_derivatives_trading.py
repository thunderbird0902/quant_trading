#!/usr/bin/env python3
"""
示例 04：衍生品交易全流程（BTC-USDT-SWAP 永续合约）

演示：
1. 查询合约信息（合约面值、杠杆限制）
2. 查询资金费率
3. 设置杠杆为 5x（全仓）
4. 限价开多（买入开多 1 张）
5. 查询持仓
6. 市价平多（卖出全部多头仓位）

⚠️  本示例在模拟盘执行，确保 OKX_FLAG=1
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
from utils.config_loader import load_okx_config
from utils.helpers import round_to


def main():
    config = load_okx_config()
    okx_cfg = config["okx"]

    event_bus = EventBus()
    gateway = OKXGateway(event_bus, okx_cfg)
    gateway.connect()

    INST_ID = "BTC-USDT-SWAP"

    # ── 1. 查询合约信息 ───────────────────────────────────────────
    print("=" * 60)
    print(f"📋 {INST_ID} 合约信息")
    print("=" * 60)

    instruments = gateway.get_instruments(MarketType.SWAP)
    inst = next((i for i in instruments if i.inst_id == INST_ID), None)

    if inst:
        print(f"合约面值: {inst.contract_value} {inst.contract_value_ccy}")
        print(f"合约乘数: {inst.contract_multiplier}")
        print(f"价格精度: {inst.tick_size}")
        print(f"最小张数: {inst.min_size}")
        print(f"状态:     {inst.state}")
        tick_size = inst.tick_size
        min_sz = inst.min_size
    else:
        tick_size = Decimal("0.1")
        min_sz = Decimal("1")

    # ── 2. 查询资金费率 ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"📉 {INST_ID} 资金费率")
    print("=" * 60)

    fr = gateway.get_funding_rate(INST_ID)
    print(f"当前资金费率:   {float(fr.funding_rate) * 100:.4f}%")
    print(f"预测下期费率:   {float(fr.next_funding_rate) * 100:.4f}%")
    print(f"下次结算时间:   {fr.funding_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # ── 3. 设置杠杆 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("⚙️  设置杠杆 5x（全仓）")
    print("=" * 60)

    try:
        success = gateway.set_leverage(INST_ID, 5, MarginMode.CROSS)
        print(f"杠杆设置: {'成功' if success else '失败'}")
    except Exception as e:
        print(f"设置杠杆失败（可能账户模式不支持）: {e}")

    # ── 4. 查询当前价格 ───────────────────────────────────────────
    tick = gateway.get_ticker(INST_ID)
    current_price = tick.last_price
    print(f"\n当前价格: ${float(current_price):,.2f}")

    # ── 5. 限价开多（合约 1 张）────────────────────────────────────
    print("\n" + "=" * 60)
    print("📈 限价开多 1 张")
    print("=" * 60)

    # 在当前价格附近挂单（比当前价高 0.5%，确保能成交）
    open_price = round_to(current_price * Decimal("1.005"), tick_size)
    print(f"开仓价格: ${float(open_price):,.2f}")
    print(f"委托张数: 1 张")
    print(f"名义价值: ${float(open_price * (inst.contract_value if inst else Decimal('0.01'))):,.2f} USDT")

    open_request = OrderRequest(
        inst_id=INST_ID,
        exchange=Exchange.OKX,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=open_price,
        quantity=Decimal("1"),   # 1 张合约
        margin_mode=MarginMode.CROSS,
        position_side=PositionSide.NET,  # 买卖模式（net_mode）
    )

    try:
        open_order = gateway.send_order(open_request)
        print(f"\n✅ 开仓委托成功")
        print(f"   订单 ID: {open_order.order_id}")
        print(f"   状态:    {open_order.status.value}")

        # 稍等1秒查询订单状态
        time.sleep(1)
        open_order_detail = gateway.get_order(open_order.order_id, INST_ID)
        print(f"   当前状态: {open_order_detail.status.value}")
        print(f"   已成交量: {open_order_detail.filled_quantity} 张")

    except Exception as e:
        print(f"开仓失败: {e}")
        gateway.disconnect()
        return

    # ── 6. 查询持仓 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📊 当前持仓")
    print("=" * 60)

    time.sleep(1)
    positions = gateway.get_positions(INST_ID)

    if positions:
        for pos in positions:
            print(f"产品:       {pos.inst_id}")
            print(f"持仓方向:   {pos.position_side.value}")
            print(f"持仓量:     {pos.quantity} 张")
            print(f"开仓均价:   ${float(pos.avg_price):,.2f}")
            print(f"标记价格:   ${float(pos.mark_price):,.2f}")
            print(f"未实现盈亏: ${float(pos.unrealized_pnl):+,.2f} USDT ({float(pos.unrealized_pnl_ratio)*100:+.2f}%)")
            print(f"占用保证金: ${float(pos.margin):,.2f} USDT")
            print(f"强平价格:   ${float(pos.liquidation_price):,.2f}")
    else:
        print("当前无持仓（委托可能尚未成交）")

    # ── 7. 市价平多 ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📉 撤销委托或市价平多")
    print("=" * 60)

    if open_order_detail.is_active:
        # 委托未成交，直接撤单
        success = gateway.cancel_order(open_order.order_id, INST_ID)
        print(f"撤销开仓委托: {'成功' if success else '失败'}")
    elif positions:
        # 已成交，市价平仓
        for pos in positions:
            if pos.quantity > 0:
                close_request = OrderRequest(
                    inst_id=INST_ID,
                    exchange=Exchange.OKX,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    margin_mode=MarginMode.CROSS,
                    position_side=PositionSide.NET,
                )
                try:
                    close_order = gateway.send_order(close_request)
                    print(f"市价平多成功，订单 ID: {close_order.order_id}")
                except Exception as e:
                    print(f"平仓失败: {e}")
    else:
        print("无需平仓")

    gateway.disconnect()
    print("\n✅ 衍生品交易示例完成")


if __name__ == "__main__":
    main()
