#!/usr/bin/env python3
"""
strategy_runners/double_ma/backtest.py
========================================
双均线策略 — 回测入口

支持 CLI 传参，可按需覆盖 params.yaml 中的任意参数。

运行示例
--------
# 使用默认参数（从 params.yaml 读取）
python -m strategy_runners.double_ma.backtest

# 覆盖策略参数
python -m strategy_runners.double_ma.backtest --fast-period 5 --slow-period 20

# 使用模拟数据（无需网络）
python -m strategy_runners.double_ma.backtest --data-source mock --mock-days 365

# 开启网格搜索，结果保存到 output/ 目录
python -m strategy_runners.double_ma.backtest --grid-search --output-dir ./output

# 不自动打开浏览器（仍生成 HTML 报告）
python -m strategy_runners.double_ma.backtest --no-open

# 不生成 HTML 报告（纯命令行输出）
python -m strategy_runners.double_ma.backtest --no-report

# 完整参数说明
python -m strategy_runners.double_ma.backtest --help

流程
----
  1. 加载 params.yaml 默认参数（CLI 参数优先级更高）
  2. 从 OKX 拉取历史数据（或生成模拟数据）
  3. [可选] 参数网格搜索，找最优 Sharpe 参数
  4. 用最终参数跑完整回测，打印绩效报告
  5. 生成 HTML 交互报告并自动打开浏览器（可用 --no-report / --no-open 控制）
  6. [可选] 保存 JSON 报告 + CSV 网格结果
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 确保项目根目录在 sys.path ────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from backtest.engine import BacktestEngine
from strategy_core.impls.double_ma_strategy import DoubleMaStrategy
from strategy_runners.cli import (
    add_data_args, add_backtest_args, add_logging_args,
    parse_log_level, parse_date, load_defaults,
)
from strategy_core.data_utils import load_bars
from backtest.report import (
    print_grid_table, print_best_summary,
    save_json, save_grid_csv,
)


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
    bt = defaults.get("backtest", {})

    parser = argparse.ArgumentParser(
        prog="python -m strategy_runners.double_ma.backtest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 通用参数（日志、数据源）
    add_logging_args(parser)
    add_data_args(parser)
    parser.set_defaults(
        inst_id=d.get("inst_id", "BTC-USDT"),
        interval=d.get("interval", "1H"),
        start_date=bt.get("start_date", None),
        end_date=bt.get("end_date", None),
        data_limit=bt.get("data_limit", 1000),
        mock_days=bt.get("mock_days", 365),
        mock_seed=bt.get("mock_seed", 42),
    )

    # 回测引擎参数
    add_backtest_args(parser)
    parser.set_defaults(
        capital=bt.get("capital", 100_000),
        taker_fee=bt.get("taker_fee", 0.0005),
        maker_fee=bt.get("maker_fee", 0.0002),
        slippage=bt.get("slippage", 0.0001),
    )

    # HTML 报告
    parser.add_argument(
        "--no-report", action="store_true",
        help="禁止生成 HTML 交互报告（默认自动生成并在浏览器打开）",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="生成 HTML 报告但不自动打开浏览器",
    )

    # 双均线策略专属参数
    g = parser.add_argument_group("双均线策略参数")
    g.add_argument(
        "--fast-period", type=int, default=s.get("fast_period", 10),
        metavar="N", help=f"快均线周期，默认 {s.get('fast_period', 10)}",
    )
    g.add_argument(
        "--slow-period", type=int, default=s.get("slow_period", 30),
        metavar="N", help=f"慢均线周期，默认 {s.get('slow_period', 30)}",
    )
    g.add_argument(
        "--position-pct", type=float, default=s.get("position_pct", 0.95),
        metavar="PCT", help=f"开仓使用可用资金比例，默认 {s.get('position_pct', 0.95):.0%}",
    )
    g.add_argument(
        "--lot-size", type=float, default=s.get("lot_size", 0.001),
        metavar="SIZE", help=f"下单精度，默认 {s.get('lot_size', 0.001)}",
    )

    return parser


# ══════════════════════════════════════════════════════════════
# 核心：单次回测
# ══════════════════════════════════════════════════════════════

def run_single_backtest(bars, strategy_cfg: dict, args: argparse.Namespace):
    """执行一次回测，返回 (metrics_dict, engine)。"""
    engine = BacktestEngine(
        strategy_class=DoubleMaStrategy,
        strategy_config=strategy_cfg,
        inst_id=args.inst_id,
        bars=bars,
        initial_capital=Decimal(str(args.capital)),
        taker_fee=Decimal(str(args.taker_fee)),
        maker_fee=Decimal(str(args.maker_fee)),
        slippage_pct=Decimal(str(args.slippage)),
        warmup_bars=strategy_cfg.get("slow_period", 30),
        generate_report=False,   # 网格搜索中禁用报告，最终回测再单独生成
    )
    metrics = engine.run()
    return metrics, engine


# ══════════════════════════════════════════════════════════════
# 网格搜索
# ══════════════════════════════════════════════════════════════

def run_grid_search(bars, args: argparse.Namespace, grid_cfg: dict) -> list[dict]:
    """
    遍历参数网格，返回所有结果（含参数 + 指标），按 Sharpe 降序排列。

    网格搜索会自动过滤 fast_period >= slow_period 的无效组合。
    """
    fast_periods = grid_cfg.get("fast_period", [5, 10, 20])
    slow_periods = grid_cfg.get("slow_period", [20, 30, 60])

    param_grid = [
        (f, s) for f, s in product(fast_periods, slow_periods)
        if f < s  # 快线周期必须小于慢线周期
    ]
    logger.info("网格搜索共 %d 组参数...", len(param_grid))

    results = []
    for fast, slow in param_grid:
        cfg = {
            "fast_period":  fast,
            "slow_period":  slow,
            "position_pct": args.position_pct,
            "lot_size":     args.lot_size,
            "interval":     args.interval,
        }
        try:
            m, _ = run_single_backtest(bars, cfg, args)
            results.append({
                "fast_period":      fast,
                "slow_period":      slow,
                "total_return_pct": round(m.get("total_return_pct", 0), 3),
                "sharpe_ratio":     round(m.get("sharpe_ratio", 0), 4),
                "max_drawdown_pct": round(m.get("max_drawdown_pct", 0), 3),
                "total_trades":     m.get("total_trades", 0),
                "win_rate_pct":     round(m.get("win_rate_pct", 0), 2),
                "calmar_ratio":     round(m.get("calmar_ratio", 0), 4),
            })
        except Exception as exc:
            logger.warning("参数 fast=%d slow=%d 回测异常: %s", fast, slow, exc)

    results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)
    return results


# ══════════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════════

def main() -> None:
    defaults = load_defaults(str(_PARAMS_FILE))
    parser = build_parser(defaults)
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=parse_log_level(args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("双均线策略回测启动")

    print("=" * 62)
    print("  双均线策略回测")
    print("=" * 62)
    print(f"  品种={args.inst_id}  周期={args.interval}  "
          f"数据来源={args.data_source}  初始资金={args.capital:,.0f} USDT")

    # ── 1. 加载历史数据 ───────────────────────────────────────
    print("\n[1/3] 加载历史数据...")

    bars = load_bars(
        source=args.data_source,
        inst_id=args.inst_id,
        interval=args.interval,
        limit=args.data_limit,
        start=parse_date(args.start_date),
        end=parse_date(args.end_date),
        mock_days=args.mock_days,
        mock_seed=args.mock_seed,
        fallback_to_mock=True,
    )
    print(f"  ✓ {len(bars)} 根 K 线  "
          f"[{bars[0].timestamp:%Y-%m-%d} → {bars[-1].timestamp:%Y-%m-%d}]")

    # ── 2. 网格搜索 / 单次回测 ────────────────────────────────
    grid_results: list[dict] = []
    best_strategy_cfg: dict

    if args.grid_search:
        print("\n[2/3] 参数网格搜索...\n")
        grid_cfg = defaults.get("backtest", {}).get("grid", {})
        grid_results = run_grid_search(bars, args, grid_cfg)

        param_keys = ["fast_period", "slow_period"]
        print_grid_table(grid_results, param_keys)

        best = grid_results[0]
        best_strategy_cfg = {
            "fast_period":  best["fast_period"],
            "slow_period":  best["slow_period"],
            "position_pct": args.position_pct,
            "lot_size":     args.lot_size,
            "interval":     args.interval,
        }
        print(f"\n  → 最优参数（按 Sharpe 排序）: "
              f"fast={best['fast_period']}  slow={best['slow_period']}")
    else:
        print("\n[2/3] 使用指定参数...")
        best_strategy_cfg = {
            "fast_period":  args.fast_period,
            "slow_period":  args.slow_period,
            "position_pct": args.position_pct,
            "lot_size":     args.lot_size,
            "interval":     args.interval,
        }
        print(f"  fast={args.fast_period}  slow={args.slow_period}  "
              f"仓位={args.position_pct:.0%}")

    # ── 3. 最终完整回测 + 绩效报告 ───────────────────────────
    print("\n[3/3] 完整回测报告...")

    report_dir = args.output_dir or "./output/"
    generate_html = not args.no_report

    final_engine = BacktestEngine(
        strategy_class=DoubleMaStrategy,
        strategy_config=best_strategy_cfg,
        inst_id=args.inst_id,
        bars=bars,
        initial_capital=Decimal(str(args.capital)),
        taker_fee=Decimal(str(args.taker_fee)),
        maker_fee=Decimal(str(args.maker_fee)),
        slippage_pct=Decimal(str(args.slippage)),
        warmup_bars=best_strategy_cfg.get("slow_period", 30),
        generate_report=generate_html,
        report_output_dir=report_dir,
    )
    final_metrics = final_engine.run()
    print_best_summary(best_strategy_cfg, final_engine)

    # ── 4. 结果持久化（可选）────────────────────────────────
    if args.output_dir:
        save_json(
            metrics=final_metrics,
            strategy_name="double_ma",
            output_dir=args.output_dir,
            extra={"params": best_strategy_cfg, "data_source": args.data_source},
        )
        if grid_results:
            save_grid_csv(grid_results, strategy_name="double_ma", output_dir=args.output_dir)
        print(f"\n  ✓ JSON/CSV 已保存至: {args.output_dir}")

    # ── 5. HTML 报告提示 ──────────────────────────────────
    if generate_html and final_engine._report_path:
        print(f"\n  ✓ HTML 报告: {final_engine._report_path}")
        if not args.no_open:
            final_engine.open_report()

    # ── 6. 给实盘的建议 ───────────────────────────────────────
    print("\n✅ 回测完成")
    print("   → 对结果满意后，用以下命令启动实盘：")
    print(
        f"    python -m strategy_runners.double_ma.live"
        f" --fast-period {best_strategy_cfg['fast_period']}"
        f" --slow-period {best_strategy_cfg['slow_period']}"
        f" --position-pct {best_strategy_cfg['position_pct']}"
        f" --lot-size {best_strategy_cfg['lot_size']}"
        f" --inst-id {args.inst_id}"
        f" --interval {args.interval}"
    )


if __name__ == "__main__":
    main()
