#!/usr/bin/env python3
"""
示例 01：连接 OKX 并查看账户信息

演示：
1. 从环境变量读取 API Key
2. 初始化 MainEngine + OKXGateway
3. 连接 OKX 模拟盘
4. 打印账户配置（账户模式）
5. 打印各币种余额

运行前设置环境变量：
    export OKX_API_KEY=xxx
    export OKX_SECRET_KEY=xxx
    export OKX_PASSPHRASE=xxx
    export OKX_FLAG=1   # 1=模拟盘
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import MainEngine
from core.event_bus import EventBus
from gateway.okx.okx_gateway import OKXGateway
from utils.config_loader import load_okx_config


def main():
    # ── 加载配置（API Key 从环境变量）────────────────────────────
    config = load_okx_config()
    okx_cfg = config["okx"]

    if not okx_cfg.get("api_key"):
        print("⚠️  请设置环境变量 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE")
        return

    print(f"使用 OKX {'模拟盘' if okx_cfg['flag'] == '1' else '实盘'}")

    # ── 初始化引擎 ──────────────────────────────────────────────
    event_bus = EventBus()
    engine = MainEngine({"system": {"log_level": "WARNING"}})

    gateway = OKXGateway(event_bus, okx_cfg)
    engine.add_gateway(gateway)

    # ── 连接 ────────────────────────────────────────────────────
    print("\n🔌 连接中...")
    engine.connect(gateway.exchange)

    # ── 账户配置 ─────────────────────────────────────────────────
    account_config = gateway.get_account_config()
    acct_lv = account_config.get("acctLv", "未知")
    pos_mode = account_config.get("posMode", "未知")
    acct_lv_desc = {
        "1": "简单交易模式（仅现货）",
        "2": "单币种保证金模式",
        "3": "跨币种保证金模式",
        "4": "组合保证金模式",
    }.get(str(acct_lv), acct_lv)

    print(f"\n📊 账户配置")
    print(f"   账户模式: {acct_lv} - {acct_lv_desc}")
    print(f"   持仓模式: {pos_mode} ({'单向持仓' if pos_mode == 'net_mode' else '双向持仓'})")

    # ── 账户余额 ─────────────────────────────────────────────────
    balance = gateway.get_balance()

    print(f"\n💰 账户总权益: ${float(balance.total_equity):,.2f} USDT")
    print(f"   可用余额:   ${float(balance.available_balance):,.2f} USDT")
    print(f"   未实现盈亏: ${float(balance.unrealized_pnl):+,.2f} USDT")

    # 打印各币种明细
    if balance.details:
        print(f"\n📋 各币种余额:")
        print(f"   {'币种':<10} {'可用':<15} {'冻结':<15} {'权益(USD)':<15}")
        print("   " + "-" * 55)
        for detail in sorted(balance.details, key=lambda x: float(x.equity_usd), reverse=True):
            if float(detail.equity) > 0:
                print(
                    f"   {detail.currency:<10} "
                    f"{float(detail.available):<15.6f} "
                    f"{float(detail.frozen):<15.6f} "
                    f"${float(detail.equity_usd):<14,.2f}"
                )

    # ── 断开 ────────────────────────────────────────────────────
    engine.disconnect(gateway.exchange)
    print("\n✅ 示例完成")


if __name__ == "__main__":
    main()
