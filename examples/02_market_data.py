#!/usr/bin/env python3
"""
示例 02：获取行情数据（REST API）

演示：
1. 获取 BTC-USDT 产品信息（精度、下单限制等）
2. 获取最新行情快照
3. 获取 1H K 线（最近 100 根）
4. 获取 5 档深度
5. 获取资金费率（永续合约）
6. 获取标记价格
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.enums import MarketType
from core.event_bus import EventBus
from gateway.okx.okx_gateway import OKXGateway
from utils.config_loader import load_okx_config


def main():
    config = load_okx_config()
    okx_cfg = config["okx"]

    event_bus = EventBus()
    gateway = OKXGateway(event_bus, okx_cfg)
    gateway.connect()

    # ── 1. 产品信息 ───────────────────────────────────────────────
    print("=" * 60)
    print("📋 BTC-USDT 现货产品信息")
    print("=" * 60)

    spot_instruments = gateway.get_instruments(MarketType.SPOT)
    btc_usdt = next((i for i in spot_instruments if i.inst_id == "BTC-USDT"), None)

    if btc_usdt:
        print(f"产品 ID:     {btc_usdt.inst_id}")
        print(f"基础货币:    {btc_usdt.base_ccy}")
        print(f"计价货币:    {btc_usdt.quote_ccy}")
        print(f"价格精度:    {btc_usdt.tick_size}")
        print(f"数量精度:    {btc_usdt.lot_size}")
        print(f"最小下单量:  {btc_usdt.min_size}")
        print(f"状态:        {btc_usdt.state}")

    # ── 2. 最新行情 ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📈 BTC-USDT 最新行情")
    print("=" * 60)

    tick = gateway.get_ticker("BTC-USDT")
    print(f"最新价:   ${float(tick.last_price):,.2f}")
    print(f"买一价:   ${float(tick.bid_price):,.2f}  (量: {tick.bid_size})")
    print(f"卖一价:   ${float(tick.ask_price):,.2f}  (量: {tick.ask_size})")
    print(f"24h最高:  ${float(tick.high_24h):,.2f}")
    print(f"24h最低:  ${float(tick.low_24h):,.2f}")
    print(f"24h成交量: {float(tick.volume_24h):,.4f} BTC")
    print(f"24h成交额: ${float(tick.volume_ccy_24h):,.0f} USDT")
    print(f"时间:     {tick.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # ── 3. K 线数据 ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📊 BTC-USDT 1H K线（最近10根）")
    print("=" * 60)

    bars = gateway.get_klines("BTC-USDT", "1H", limit=10)
    print(f"{'时间':<25} {'开盘':<12} {'最高':<12} {'最低':<12} {'收盘':<12} {'成交量':<10}")
    print("-" * 85)
    for bar in bars[-10:]:
        print(
            f"{bar.timestamp.strftime('%Y-%m-%d %H:%M'):<25} "
            f"${float(bar.open):<11,.1f} "
            f"${float(bar.high):<11,.1f} "
            f"${float(bar.low):<11,.1f} "
            f"${float(bar.close):<11,.1f} "
            f"{float(bar.volume):.4f}"
        )

    # ── 4. 深度数据 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📒 BTC-USDT 5档深度")
    print("=" * 60)

    book = gateway.get_orderbook("BTC-USDT", depth=5)
    print(f"{'卖方（Ask）':<35} {'买方（Bid）'}")
    print("-" * 70)
    asks_display = list(reversed(book.asks[:5]))  # 最近5档，价格从高到低
    bids_display = book.bids[:5]
    for i in range(max(len(asks_display), len(bids_display))):
        ask = asks_display[i] if i < len(asks_display) else ("", "")
        bid = bids_display[i] if i < len(bids_display) else ("", "")
        ask_str = f"${float(ask[0]):>12,.2f}  {float(ask[1]):>10.4f}" if ask[0] else " " * 35
        bid_str = f"${float(bid[0]):>12,.2f}  {float(bid[1]):>10.4f}" if bid[0] else ""
        print(f"{ask_str:<35} {bid_str}")

    print(f"\n时间: {book.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # ── 5. 资金费率（永续合约）────────────────────────────────────
    print("\n" + "=" * 60)
    print("📉 BTC-USDT-SWAP 资金费率")
    print("=" * 60)

    try:
        fr = gateway.get_funding_rate("BTC-USDT-SWAP")
        print(f"当前费率:     {float(fr.funding_rate) * 100:.4f}%")
        print(f"预测下期费率: {float(fr.next_funding_rate) * 100:.4f}%")
        print(f"下次收取时间: {fr.funding_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    except Exception as e:
        print(f"获取资金费率失败: {e}")

    # ── 6. 标记价格 ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🎯 BTC-USDT-SWAP 标记价格")
    print("=" * 60)

    try:
        mp = gateway.get_mark_price("BTC-USDT-SWAP")
        print(f"标记价格: ${float(mp.mark_price):,.2f}")
        print(f"时间:     {mp.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    except Exception as e:
        print(f"获取标记价格失败: {e}")

    gateway.disconnect()
    print("\n✅ 示例完成")


if __name__ == "__main__":
    main()
