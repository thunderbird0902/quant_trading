"""
strategy_runners/cli.py
=======================
公共 CLI 构建器。

各策略的 backtest.py / live.py 通过调用这里的 helper 函数
向自己的 ArgumentParser 注册「通用参数组」，避免重复代码。

用法（策略脚本中）：
    from strategy_runners.cli import (
        add_data_args, add_backtest_args, add_live_args, parse_log_level
    )

    parser = argparse.ArgumentParser(...)
    add_data_args(parser)
    add_backtest_args(parser)
    args = parser.parse_args()
"""

from __future__ import annotations

import argparse
import logging


# ──────────────────────────────────────────────────────────────
# 通用参数组注册函数
# ──────────────────────────────────────────────────────────────

def add_data_args(parser: argparse.ArgumentParser) -> None:
    """数据源相关参数（回测 / 实盘共用）"""
    g = parser.add_argument_group("数据源")
    g.add_argument(
        "--inst-id", default="BTC-USDT", metavar="SYMBOL",
        help="交易品种，默认 BTC-USDT",
    )
    g.add_argument(
        "--interval", default="1H", metavar="INTERVAL",
        choices=["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"],
        help="K 线周期，默认 1H",
    )
    g.add_argument(
        "--data-source", default="real", choices=["real", "mock"],
        help="数据来源：real=从 OKX 拉取真实历史，mock=本地生成模拟数据（默认 real）",
    )
    g.add_argument(
        "--start-date", default=None, metavar="YYYY-MM-DD",
        help="回测起始日期（UTC），与 --end-date 同时指定时按时间段拉取历史数据，"
             "优先于 --data-limit",
    )
    g.add_argument(
        "--end-date", default=None, metavar="YYYY-MM-DD",
        help="回测结束日期（UTC），默认为当前时间；需与 --start-date 配合使用",
    )
    g.add_argument(
        "--data-limit", type=int, default=1000, metavar="N",
        help="从 OKX 拉取的 K 线条数（未指定 --start-date 时生效，默认 1000）",
    )
    g.add_argument(
        "--mock-days", type=int, default=180, metavar="DAYS",
        help="模拟 K 线天数（data-source=mock 时有效，默认 180）",
    )
    g.add_argument(
        "--mock-seed", type=int, default=42, metavar="SEED",
        help="模拟数据随机种子，固定种子保证可复现（默认 42）",
    )


def add_backtest_args(parser: argparse.ArgumentParser) -> None:
    """回测引擎相关参数"""
    g = parser.add_argument_group("回测引擎")
    g.add_argument(
        "--capital", type=float, default=100_000, metavar="USDT",
        help="初始资金，默认 100000 USDT",
    )
    g.add_argument(
        "--taker-fee", type=float, default=0.0005, metavar="RATE",
        help="Taker 手续费率，默认 0.0005（0.05%%）",
    )
    g.add_argument(
        "--maker-fee", type=float, default=0.0002, metavar="RATE",
        help="Maker 手续费率，默认 0.0002（0.02%%）",
    )
    g.add_argument(
        "--slippage", type=float, default=0.0001, metavar="RATE",
        help="滑点比例，默认 0.0001（0.01%%）",
    )
    g.add_argument(
        "--grid-search", action="store_true",
        help="开启参数网格搜索（找最优参数后再跑完整回测报告）",
    )
    g.add_argument(
        "--output-dir", default=None, metavar="DIR",
        help="结果输出目录（JSON + CSV），不指定则只打印到控制台",
    )


def add_live_args(parser: argparse.ArgumentParser) -> None:
    """实盘运行相关参数"""
    g = parser.add_argument_group("实盘配置")
    g.add_argument(
        "--strategy-name", default=None, metavar="NAME",
        help="策略实例名称，默认由策略脚本自动生成（如 rsi_BTC-USDT）",
    )
    g.add_argument(
        "--risk-max-daily-loss", type=float, default=0.05, metavar="PCT",
        help="风控：单日最大亏损比例，默认 0.05（5%%）",
    )
    g.add_argument(
        "--risk-max-position", type=float, default=0.5, metavar="PCT",
        help="风控：单品种最大仓位比例，默认 0.5（50%%）",
    )
    g.add_argument(
        "--risk-max-consecutive-losses", type=int, default=5, metavar="N",
        help="风控：最大连续亏损次数，默认 5",
    )


def add_logging_args(parser: argparse.ArgumentParser) -> None:
    """日志级别参数"""
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO",
    )


# ──────────────────────────────────────────────────────────────
# 便捷工具函数
# ──────────────────────────────────────────────────────────────

def parse_log_level(level_str: str) -> int:
    """将字符串日志级别转换为 logging 模块的整数常量。"""
    return getattr(logging, level_str.upper(), logging.INFO)


def parse_date(s: str | None) -> datetime | None:
    """将 YYYY-MM-DD 字符串解析为 UTC datetime；None 原样返回。"""
    if s is None:
        return None
    from datetime import datetime, timezone
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def load_defaults(default_params_file: str) -> dict:
    """从 YAML 加载策略默认配置。

    若环境变量 STRATEGY_PARAMS_YAML 已设置，则优先使用该路径（支持 cli.main()
    的 --config 传参）；否则使用 default_params_file。
    """
    import os
    from utils.config_loader import load_yaml
    actual = os.environ.get("STRATEGY_PARAMS_YAML", default_params_file)
    return load_yaml(actual)


def build_base_parser(description: str) -> argparse.ArgumentParser:
    """
    创建一个已注册通用参数（日志、数据源）的基础 ArgumentParser。

    策略脚本在此基础上追加策略专属参数即可。
    """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_logging_args(parser)
    add_data_args(parser)
    return parser


# ──────────────────────────────────────────────────────────────
# 统一 CLI 入口（可选）
# ──────────────────────────────────────────────────────────────

def main() -> None:
    """
    统一策略运行入口（可选）。

    用法：
        python -m strategy_runners.cli --strategy double_ma --mode backtest --config path/to/params.yaml
    """
    import os
    import sys
    parser = argparse.ArgumentParser(
        prog="python -m strategy_runners.cli",
        description="统一策略运行入口",
    )
    parser.add_argument("--strategy", required=True, choices=["double_ma", "rsi"],
                        help="策略名称")
    parser.add_argument("--mode", required=True, choices=["backtest", "live"],
                        help="运行模式")
    parser.add_argument("--config", default=None, metavar="YAML",
                        help="配置文件路径（覆盖默认 params.yaml）")
    args, rest = parser.parse_known_args()

    if args.config:
        os.environ["STRATEGY_PARAMS_YAML"] = args.config

    module_name = f"strategy_runners.{args.strategy}.{args.mode}"
    sys.argv = [sys.argv[0]] + rest
    __import__(module_name)
    sys.modules[module_name].main()


if __name__ == "__main__":
    main()
