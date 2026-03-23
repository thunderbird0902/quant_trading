#!/usr/bin/env python3
"""
示例 07：WebSocket 实时数据推送

演示：
1. 启动 WebSocket 连接
2. 订阅公共频道：BTC-USDT-SWAP 行情、深度、成交
3. 登录私有频道：订阅账户余额、持仓、订单状态
4. 运行 60 秒，打印收到的所有事件
5. 优雅退出

⚠️  需要有效的 API Key（私有频道必须登录）
"""

from __future__ import annotations

import sys
import os
import asyncio
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.enums import Exchange
from core.event_bus import EventBus, EventType, Event
from gateway.okx.okx_websocket import OKXWebSocket
from utils.config_loader import load_okx_config


# ── 事件统计 ──────────────────────────────────────────────────
event_counts = {
    "TICK": 0,
    "BAR": 0,
    "DEPTH": 0,
    "TRADE": 0,
    "BALANCE_UPDATED": 0,
    "POSITION_UPDATED": 0,
    "ORDER_UPDATED": 0,
}


def on_tick(event: Event):
    event_counts["TICK"] += 1
    tick = event.data
    if event_counts["TICK"] % 5 == 0:  # 每5条打印一次
        print(f"📈 TICK  {tick.inst_id} last=${float(tick.last_price):,.2f} "
              f"bid=${float(tick.bid_price):,.2f} ask=${float(tick.ask_price):,.2f}")


def on_bar(event: Event):
    event_counts["BAR"] += 1
    bar = event.data
    print(f"📊 BAR   {bar.inst_id} {bar.interval} "
          f"o={float(bar.open):,.2f} h={float(bar.high):,.2f} "
          f"l={float(bar.low):,.2f} c={float(bar.close):,.2f}")


def on_depth(event: Event):
    event_counts["DEPTH"] += 1
    book = event.data
    if event_counts["DEPTH"] % 10 == 0:
        best_bid = book.bids[0] if book.bids else (0, 0)
        best_ask = book.asks[0] if book.asks else (0, 0)
        print(f"📒 DEPTH {book.inst_id} "
              f"bid=${float(best_bid[0]):,.2f} ask=${float(best_ask[0]):,.2f}")


def on_trade(event: Event):
    event_counts["TRADE"] += 1
    trade = event.data
    if event_counts["TRADE"] % 5 == 0:
        print(f"✅ TRADE {trade.inst_id} {trade.side.value} "
              f"${float(trade.price):,.2f} × {trade.quantity}")


def on_balance(event: Event):
    event_counts["BALANCE_UPDATED"] += 1
    balance = event.data
    print(f"💰 BALANCE 总权益=${float(balance.total_equity):,.2f} USDT")


def on_position(event: Event):
    event_counts["POSITION_UPDATED"] += 1
    pos = event.data
    print(f"📊 POSITION {pos.inst_id} {pos.position_side.value} "
          f"qty={pos.quantity} pnl={float(pos.unrealized_pnl):+.2f}")


def on_order(event: Event):
    event_counts["ORDER_UPDATED"] += 1
    order = event.data
    print(f"📝 ORDER {order.inst_id} {order.side.value} "
          f"${float(order.price):,.2f} status={order.status.value}")


def main():
    config = load_okx_config()
    okx_cfg = config["okx"]

    print("=" * 60)
    print("🌐 WebSocket 实时数据推送演示（运行 60 秒）")
    print("=" * 60)

    # ── 初始化事件总线 ────────────────────────────────────────────
    event_bus = EventBus()

    # 注册事件处理器
    event_bus.subscribe(EventType.TICK, on_tick)
    event_bus.subscribe(EventType.BAR, on_bar)
    event_bus.subscribe(EventType.DEPTH, on_depth)
    event_bus.subscribe(EventType.TRADE, on_trade)
    event_bus.subscribe(EventType.BALANCE_UPDATED, on_balance)
    event_bus.subscribe(EventType.POSITION_UPDATED, on_position)
    event_bus.subscribe(EventType.ORDER_UPDATED, on_order)
    event_bus.subscribe(EventType.ORDER_FILLED, on_order)

    # ── 初始化 WebSocket ──────────────────────────────────────────
    ws = OKXWebSocket(
        event_bus=event_bus,
        api_key=okx_cfg.get("api_key", ""),
        secret_key=okx_cfg.get("secret_key", ""),
        passphrase=okx_cfg.get("passphrase", ""),
        flag=okx_cfg.get("flag", "1"),
    )

    # ── 订阅频道 ──────────────────────────────────────────────────
    print("\n订阅频道：")

    # 公共频道
    ws.subscribe_ticker("BTC-USDT-SWAP")
    ws.subscribe_ticker("ETH-USDT-SWAP")
    print("  ✓ tickers: BTC-USDT-SWAP, ETH-USDT-SWAP")

    ws.subscribe_orderbook("BTC-USDT-SWAP", depth=5)
    print("  ✓ books5: BTC-USDT-SWAP")

    ws.subscribe_trades("BTC-USDT-SWAP")
    print("  ✓ trades: BTC-USDT-SWAP")

    ws.subscribe_kline("BTC-USDT-SWAP", "1m")
    print("  ✓ candle1m: BTC-USDT-SWAP")

    # 私有频道（需要有效 API Key）
    if okx_cfg.get("api_key"):
        ws.subscribe_account()
        ws.subscribe_positions()
        ws.subscribe_orders()
        print("  ✓ 私有频道: account, positions, orders")
    else:
        print("  ⚠️  未设置 API Key，跳过私有频道")

    # ── 在后台线程启动 WebSocket ──────────────────────────────────
    ws_thread = threading.Thread(target=ws.start, daemon=True)
    ws_thread.start()
    print(f"\n🔌 WebSocket 已启动（后台线程）")

    # ── 运行 60 秒 ────────────────────────────────────────────────
    run_seconds = 30
    print(f"⏱️  运行 {run_seconds} 秒...\n")

    for i in range(run_seconds):
        time.sleep(1)
        if (i + 1) % 10 == 0:
            print(f"\n--- {i+1}s 统计 ---")
            for event_type, count in event_counts.items():
                if count > 0:
                    print(f"   {event_type}: {count} 条")
            print()

    # ── 停止 ────────────────────────────────────────────────────
    ws.stop()
    print("\n📊 最终统计:")
    for event_type, count in event_counts.items():
        print(f"   {event_type}: {count} 条")

    print("\n✅ WebSocket 示例完成")


if __name__ == "__main__":
    main()
