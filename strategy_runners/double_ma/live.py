#!/usr/bin/env python3
"""
strategy_runners/double_ma/live.py
====================================
双均线策略 — 实盘入口

在回测满意之后，把参数通过 CLI 传入（或直接使用 params.yaml 默认值）即可启动实盘。
策略代码（double_ma_strategy.py）与回测完全一致，零修改。

运行示例
--------
# 使用 params.yaml 默认参数启动
python -m strategy_runners.double_ma.live

# 覆盖关键参数（从回测拷贝最优参数）
python -m strategy_runners.double_ma.live \\
    --fast-period 10 \\
    --slow-period 30 \\
    --position-pct 0.95 \\
    --inst-id BTC-USDT \\
    --interval 1H

# 调整风控阈值
python -m strategy_runners.double_ma.live \\
    --risk-max-daily-loss 0.03 \\
    --risk-max-position 0.3

# 查看全部参数
python -m strategy_runners.double_ma.live --help

停止
----
  Ctrl+C  →  自动撤销挂单后安全退出

安全提示
--------
  ⚠️  实盘前务必先用小仓位（--position-pct 0.05）验证连接和策略行为！
  ⚠️  确保 config/okx_config.yaml 中 API Key 已正确配置！
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# ── 确保项目根目录在 sys.path ────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from core.engine import MainEngine
from core.enums import Exchange
from gateway.okx.okx_gateway import OKXGateway
from risk.risk_engine import RiskEngine
from strategy_core.strategy_engine import StrategyEngine
from strategy_core.impls.double_ma_strategy import DoubleMaStrategy
from utils.config_loader import load_okx_config
from strategy_runners.cli import (
    add_live_args, add_logging_args,
    parse_log_level, load_defaults,
)

logger = logging.getLogger("double_ma.live")

# ══════════════════════════════════════════════════════════════
# 默认配置加载
# ══════════════════════════════════════════════════════════════

_PARAMS_FILE = Path(__file__).parent / "params.yaml"


# ══════════════════════════════════════════════════════════════
# CLI 参数解析
# ══════════════════════════════════════════════════════════════

def build_parser(defaults: dict) -> argparse.ArgumentParser:
    s  = defaults.get("strategy", {})
    d  = defaults.get("data", {})
    lv = defaults.get("live", {}).get("risk", {})

    parser = argparse.ArgumentParser(
        prog="python -m strategy_runners.double_ma.live",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 通用参数（日志）
    add_logging_args(parser)

    # 交易品种
    g_data = parser.add_argument_group("交易品种")
    g_data.add_argument(
        "--inst-id", default=d.get("inst_id", "BTC-USDT"),
        metavar="SYMBOL", help=f"交易品种，默认 {d.get('inst_id', 'BTC-USDT')}",
    )
    g_data.add_argument(
        "--interval", default=d.get("interval", "1H"),
        choices=["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"],
        help=f"K 线周期，默认 {d.get('interval', '1H')}",
    )

    # 双均线策略专属参数
    g_strat = parser.add_argument_group("双均线策略参数")
    g_strat.add_argument(
        "--fast-period", type=int, default=s.get("fast_period", 10),
        metavar="N", help=f"快均线周期，默认 {s.get('fast_period', 10)}",
    )
    g_strat.add_argument(
        "--slow-period", type=int, default=s.get("slow_period", 30),
        metavar="N", help=f"慢均线周期，默认 {s.get('slow_period', 30)}",
    )
    g_strat.add_argument(
        "--position-pct", type=float, default=s.get("position_pct", 0.95),
        metavar="PCT",
        help=f"开仓使用可用资金比例（默认 {s.get('position_pct', 0.95):.0%}，实盘建议先小仓位测试！）",
    )
    g_strat.add_argument(
        "--lot-size", type=float, default=s.get("lot_size", 0.001),
        metavar="SIZE", help=f"下单精度，默认 {s.get('lot_size', 0.001)}",
    )

    # 风控参数
    add_live_args(parser)
    parser.set_defaults(
        risk_max_daily_loss=lv.get("max_daily_loss_pct", 0.05),
        risk_max_position=lv.get("max_position_pct", 0.5),
        risk_max_consecutive_losses=lv.get("max_consecutive_losses", 5),
    )

    return parser


# ══════════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════════

def main() -> None:
    defaults = load_defaults(str(_PARAMS_FILE))
    parser = build_parser(defaults)
    args = parser.parse_args()

    # ── 配置日志 ──────────────────────────────────────────────
    logging.basicConfig(
        level=parse_log_level(args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── 策略参数汇总 ──────────────────────────────────────────
    strategy_name = args.strategy_name or f"double_ma_{args.inst_id}"
    strategy_cfg = {
        "fast_period":  args.fast_period,
        "slow_period":  args.slow_period,
        "position_pct": args.position_pct,
        "lot_size":     args.lot_size,
        "interval":     args.interval,
    }
    risk_cfg = {
        "enabled":                True,
        "max_daily_loss_pct":     args.risk_max_daily_loss,
        "max_position_pct":       args.risk_max_position,
        "max_total_position_pct": defaults.get("live", {}).get("risk", {}).get(
                                      "max_total_position_pct", 0.9),
        "max_consecutive_losses": args.risk_max_consecutive_losses,
    }

    logger.info("=" * 62)
    logger.info("  双均线策略实盘启动")
    logger.info("=" * 62)
    logger.info("策略名: %s", strategy_name)
    logger.info("策略参数: %s", strategy_cfg)
    logger.info("风控参数: %s", risk_cfg)
    logger.warning("⚠️  实盘先用小仓位（--position-pct 0.05）验证连接和策略行为！")

    # ── 1. 加载 OKX 配置，初始化引擎 ──────────────────────────
    okx_config = load_okx_config()
    okx_cfg    = okx_config["okx"]

    engine     = MainEngine({"system": {"log_level": args.log_level}})
    gateway    = OKXGateway(engine.event_bus, okx_cfg)
    engine.add_gateway(gateway)

    # ── 2. 风控引擎 ───────────────────────────────────────────
    risk_engine = RiskEngine(engine.event_bus, config=risk_cfg)
    engine.set_risk_engine(risk_engine)

    # ── 3. 策略引擎 ───────────────────────────────────────────
    strategy_engine = StrategyEngine(engine)
    engine.set_strategy_engine(strategy_engine)

    strategy_engine.add_strategy(
        DoubleMaStrategy,
        name=strategy_name,
        inst_id=args.inst_id,
        exchange=Exchange.OKX,
        config=strategy_cfg,
    )

    # ── 4. 连接 OKX，启动引擎 ─────────────────────────────────
    logger.info("连接 OKX...")
    engine.connect(Exchange.OKX)
    risk_engine.start()
    strategy_engine.start()
    strategy_engine.start_strategy(strategy_name)

    # ── 5. 订阅 K 线推送 ──────────────────────────────────────
    gateway.subscribe_kline(args.inst_id, args.interval)

    logger.info("实盘已启动  inst=%s  config=%s", args.inst_id, strategy_cfg)
    logger.info("按 Ctrl+C 安全停止")

    # ── 6. 优雅停止（Ctrl+C / SIGTERM）──────────────────────
    def _shutdown(sig, frame):
        logger.info("收到停止信号 (signal=%d)，安全退出...", sig)
        try:
            strategy_engine.stop_strategy(strategy_name)
            strategy_engine.stop()
            risk_engine.stop()
            engine.disconnect(Exchange.OKX)
        except Exception as exc:
            logger.error("停止过程出错: %s", exc)
        finally:
            logger.info("实盘已停止")
            sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── 7. 主线程等待 + 健康检查 ────────────────────────────
    _health_check_interval = 30
    _last_health_check = time.time()

    while True:
        time.sleep(1)

        now = time.time()
        if now - _last_health_check < _health_check_interval:
            continue
        _last_health_check = now

        if not gateway.is_connected():
            logger.error("健康检查失败：Gateway 连接已断开！尝试重连...")
            try:
                engine.disconnect(Exchange.OKX)
                engine.connect(Exchange.OKX)
                if gateway.is_connected():
                    logger.info("Gateway 重连成功")
                else:
                    logger.error("Gateway 重连失败，系统可能以僵尸状态运行！")
            except Exception as exc:
                logger.error("Gateway 重连异常: %s", exc)


if __name__ == "__main__":
    main()
