"""Walk-Forward Analysis 框架

核心思想：滚动窗口验证，防止过拟合。
- 将数据划分为 N 个重叠窗口
- 每个窗口：train 区间优化参数 → test 区间验证
- 只用 test 集表现评估策略真实能力

使用示例：
    results = walk_forward(
        bars=bars,
        strategy_class=RsiStrategy,
        base_config={"rsi_period": 14, "oversold": 30, "overbought": 70},
        n_splits=5,
        train_ratio=0.7,
        optimize_fn=my_optimizer,
    )
    print(results["test_sharpe_mean"], results["test_sharpe_std"])
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Type

import numpy as np

from backtest.engine import BacktestEngine
from core.models import BarData
from strategy_core.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class SplitResult:
    """单次 Walk-Forward 分割结果"""
    split_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_bars: list[BarData]
    test_bars: list[BarData]
    best_params: dict[str, Any] | None = None
    train_metrics: dict | None = None
    test_metrics: dict | None = None
    out_of_sample: bool = True  # 始终为 True，标识这是未见过的数据


@dataclass
class WalkForwardResult:
    """Walk-Forward 分析汇总结果"""
    n_splits: int
    train_ratio: float
    split_results: list[SplitResult] = field(default_factory=list)

    train_sharpe_mean: float = 0.0
    train_sharpe_std: float = 0.0
    train_sharpe_min: float = 0.0
    train_sharpe_max: float = 0.0

    test_sharpe_mean: float = 0.0
    test_sharpe_std: float = 0.0
    test_sharpe_min: float = 0.0
    test_sharpe_max: float = 0.0

    test_return_mean: float = 0.0
    test_return_std: float = 0.0
    test_max_dd_mean: float = 0.0
    test_win_rate_mean: float = 0.0

    def summary(self) -> str:
        """打印 Walk-Forward 分析摘要"""
        lines = [
            "=" * 60,
            "  Walk-Forward 分析报告",
            "=" * 60,
            f"  分割数        {self.n_splits}",
            f"  训练集比例    {self.train_ratio:.0%}",
            "",
            "  【训练集】",
            f"  Sharpe (mean±std)  {self.train_sharpe_mean:>8.2f} ± {self.train_sharpe_std:.2f}",
            f"  Sharpe (min~max)   {self.train_sharpe_min:>8.2f} ~ {self.train_sharpe_max:.2f}",
            "",
            "  【测试集（Out-of-Sample）】",
            f"  Sharpe (mean±std)  {self.test_sharpe_mean:>8.2f} ± {self.test_sharpe_std:.2f}",
            f"  Sharpe (min~max)   {self.test_sharpe_min:>8.2f} ~ {self.test_sharpe_max:.2f}",
            f"  收益率 (mean±std)  {self.test_return_mean:>8.2f}%% ± {self.test_return_std:.2f}",
            f"  最大回撤 (mean)    {self.test_max_dd_mean:>8.2f}%%",
            f"  胜率 (mean)        {self.test_win_rate_mean:>8.1f}%",
            "",
            "  【过拟合检测】",
        ]
        sharpe_drop = self.train_sharpe_mean - self.test_sharpe_mean
        drop_pct = sharpe_drop / max(abs(self.train_sharpe_mean), 0.001) * 100
        lines.append(f"  Sharpe 衰减          {sharpe_drop:>8.2f} ({drop_pct:.1f}%)")
        if sharpe_drop > 0.5:
            lines.append("  ⚠️ 警告：测试集 Sharpe 显著低于训练集，可能存在过拟合")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "n_splits": self.n_splits,
            "train_ratio": self.train_ratio,
            "train_sharpe_mean": self.train_sharpe_mean,
            "train_sharpe_std": self.train_sharpe_std,
            "train_sharpe_min": self.train_sharpe_min,
            "train_sharpe_max": self.train_sharpe_max,
            "test_sharpe_mean": self.test_sharpe_mean,
            "test_sharpe_std": self.test_sharpe_std,
            "test_sharpe_min": self.test_sharpe_min,
            "test_sharpe_max": self.test_sharpe_max,
            "test_return_mean": self.test_return_mean,
            "test_return_std": self.test_return_std,
            "test_max_dd_mean": self.test_max_dd_mean,
            "test_win_rate_mean": self.test_win_rate_mean,
            "sharpe_drop": self.train_sharpe_mean - self.test_sharpe_mean,
        }


def walk_forward(
    bars: list[BarData],
    strategy_class: Type[BaseStrategy],
    base_config: dict[str, Any],
    n_splits: int = 5,
    train_ratio: float = 0.7,
    initial_capital: Decimal = Decimal("100000"),
    taker_fee: float = 0.0005,
    maker_fee: float = 0.0002,
    optimize_fn: Callable[
        [list[BarData], Type[BaseStrategy], dict[str, Any], str],
        dict[str, Any],
    ] | None = None,
    metric_to_optimize: str = "sharpe_ratio",
    window_type: str = "expanding",
) -> WalkForwardResult:
    """
    Walk-Forward 滚动窗口分析。

    每个窗口：
        1. 用 train_bars 在 base_config 参数空间内网格搜索最优
        2. 用最优参数在 test_bars 上回测
        3. 收集 test 集指标

    参数
    ----
    bars : list[BarData]
        历史 K 线（必须按时间排序）
    strategy_class : Type[BaseStrategy]
        策略类（需支持 from_config 或接受 base_config）
    base_config : dict
        策略基础参数（optimize_fn 会在此基础上做网格搜索）
    n_splits : int
        分割数量（默认 5）
    train_ratio : float
        每个窗口内训练集比例（默认 0.7）
    initial_capital : Decimal
        初始资金
    taker_fee, maker_fee : float
        交易手续费率
    optimize_fn : Callable | None
        自定义优化函数，签名为：
            optimize_fn(train_bars, strategy_class, base_config, inst_id) -> dict
        若为 None，使用内置网格搜索（param_grid 需在 base_config 中指定）
    metric_to_optimize : str
        优化目标指标（默认 "sharpe_ratio"）
    window_type : str
        "expanding"（扩大窗口，每次 train 包含所有历史）
        "rolling"（滚动窗口，每次 train 窗口等长）

    返回
    ----
    WalkForwardResult
        包含各 split 的详细结果和汇总统计
    """
    if len(bars) < 100:
        raise ValueError(f"数据量不足（{len(bars)}），至少需要 100 根 K 线")

    if n_splits < 2:
        raise ValueError(f"n_splits 至少为 2，当前为 {n_splits}")

    n_bars = len(bars)
    test_size = n_bars // (n_splits + 1)

    split_results: list[SplitResult] = []

    for i in range(n_splits):
        if window_type == "rolling":
            train_start_idx = i * test_size
            train_end_idx = train_start_idx + test_size
        else:
            train_start_idx = 0
            train_end_idx = (i + 1) * test_size

        train_bars = bars[train_start_idx:train_end_idx]

        test_start_idx = train_end_idx
        test_end_idx = min(test_start_idx + test_size, n_bars)
        test_bars = bars[test_start_idx:test_end_idx]

        if len(train_bars) < 50 or len(test_bars) < 20:
            logger.warning(
                "Split %d: train=%d test=%d，数据不足跳过",
                i, len(train_bars), len(test_bars),
            )
            continue

        split = SplitResult(
            split_index=i,
            train_start=train_bars[0].timestamp,
            train_end=train_bars[-1].timestamp,
            test_start=test_bars[0].timestamp,
            test_end=test_bars[-1].timestamp,
            train_bars=train_bars,
            test_bars=test_bars,
        )

        logger.info(
            "Walk-Forward Split %d/%d | train: %s ~ %s (%d bars) | test: %s ~ %s (%d bars)",
            i + 1, n_splits,
            split.train_start.strftime("%Y-%m-%d"),
            split.train_end.strftime("%Y-%m-%d"),
            len(train_bars),
            split.test_start.strftime("%Y-%m-%d"),
            split.test_end.strftime("%Y-%m-%d"),
            len(test_bars),
        )

        inst_id = train_bars[0].inst_id

        if optimize_fn is not None:
            best_params = optimize_fn(train_bars, strategy_class, base_config, inst_id)
        else:
            best_params = _default_grid_search(
                train_bars, strategy_class, base_config, inst_id,
                metric=metric_to_optimize,
                initial_capital=initial_capital,
                taker_fee=taker_fee,
                maker_fee=maker_fee,
            )

        split.best_params = best_params

        merged_config = {**base_config, **best_params}

        train_metrics = _run_single_backtest(
            train_bars, strategy_class, merged_config, inst_id,
            initial_capital, taker_fee, maker_fee,
        )
        split.train_metrics = train_metrics

        test_metrics = _run_single_backtest(
            test_bars, strategy_class, merged_config, inst_id,
            initial_capital, taker_fee, maker_fee,
        )
        split.test_metrics = test_metrics

        split_results.append(split)

        logger.info(
            "Split %d 完成 | train Sharpe=%.2f | test Sharpe=%.2f",
            i + 1,
            train_metrics.get("sharpe_ratio", 0),
            test_metrics.get("sharpe_ratio", 0),
        )

    return _aggregate_results(split_results, n_splits, train_ratio)


def _run_single_backtest(
    bars: list[BarData],
    strategy_class: Type[BaseStrategy],
    config: dict[str, Any],
    inst_id: str,
    initial_capital: Decimal,
    taker_fee: float,
    maker_fee: float,
) -> dict:
    """运行单次回测并返回指标字典。"""
    engine = BacktestEngine(
        strategy_class=strategy_class,
        strategy_config=config,
        inst_id=inst_id,
        bars=bars,
        initial_capital=initial_capital,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        generate_report=False,
    )
    return engine.run()


def _default_grid_search(
    train_bars: list[BarData],
    strategy_class: Type[BaseStrategy],
    base_config: dict[str, Any],
    inst_id: str,
    metric: str = "sharpe_ratio",
    initial_capital: Decimal = Decimal("100000"),
    taker_fee: float = 0.0005,
    maker_fee: float = 0.0002,
) -> dict[str, Any]:
    """
    内置网格搜索优化器。

    base_config 中可以包含 param_grid 字典指定搜索空间，例如：
        base_config = {
            "rsi_period": 14,
            "oversold": 30,
            "param_grid": {
                "rsi_period": [10, 14, 20],
                "oversold": [25, 30, 35],
            }
        }

    Returns:
        最优参数字典（不含 param_grid 键）
    """
    param_grid = base_config.get("param_grid", {})
    if not param_grid:
        return {k: v for k, v in base_config.items() if k != "param_grid"}

    grid_keys = list(param_grid.keys())
    grid_values = list(param_grid.values())
    all_combinations = _cartesian_product(*grid_values)

    best_metric = float("-inf")
    best_params = {}

    for combo in all_combinations:
        trial_config = {**base_config, **dict(zip(grid_keys, combo))}
        trial_config = {k: v for k, v in trial_config.items() if k != "param_grid"}

        try:
            metrics = _run_single_backtest(
                train_bars, strategy_class, trial_config, inst_id,
                initial_capital, taker_fee, maker_fee,
            )
            score = metrics.get(metric, 0)
        except Exception as e:
            logger.debug("参数组合 %s 回测失败: %s", trial_config, e)
            score = float("-inf")

        if score > best_metric:
            best_metric = score
            best_params = trial_config

    logger.info(
        "网格搜索完成 | 参数数=%d | 最优 %s=%.2f | 最优参数=%s",
        len(all_combinations), metric, best_metric,
        {k: v for k, v in best_params.items() if k in grid_keys},
    )
    return best_params


def _cartesian_product(*arrays) -> list[tuple]:
    """计算多个列表的笛卡尔积。"""
    if not arrays:
        return [()]
    result = [[]]
    for arr in arrays:
        result = [x + [y] for x in result for y in arr]
    return [tuple(x) for x in result]


def _aggregate_results(
    split_results: list[SplitResult],
    n_splits: int,
    train_ratio: float,
) -> WalkForwardResult:
    """汇总各 split 的结果。"""
    if not split_results:
        return WalkForwardResult(n_splits=n_splits, train_ratio=train_ratio)

    train_sharpes = [s.train_metrics["sharpe_ratio"] for s in split_results if s.train_metrics]
    test_sharpes = [s.test_metrics["sharpe_ratio"] for s in split_results if s.test_metrics]
    test_returns = [s.test_metrics["total_return_pct"] for s in split_results if s.test_metrics]
    test_max_dds = [s.test_metrics["max_drawdown_pct"] for s in split_results if s.test_metrics]
    test_win_rates = [s.test_metrics.get("win_rate_pct", 0) for s in split_results if s.test_metrics]

    def _stats(values: list[float]) -> tuple[float, float, float, float]:
        if not values:
            return 0.0, 0.0, 0.0, 0.0
        return float(np.mean(values)), float(np.std(values)), float(np.min(values)), float(np.max(values))

    tr_sharpe_mean, tr_sharpe_std, tr_sharpe_min, tr_sharpe_max = _stats(train_sharpes)
    te_sharpe_mean, te_sharpe_std, te_sharpe_min, te_sharpe_max = _stats(test_sharpes)
    te_ret_mean, te_ret_std = (np.mean(test_returns), np.std(test_returns)) if test_returns else (0.0, 0.0)

    result = WalkForwardResult(
        n_splits=n_splits,
        train_ratio=train_ratio,
        split_results=split_results,
        train_sharpe_mean=tr_sharpe_mean,
        train_sharpe_std=tr_sharpe_std,
        train_sharpe_min=tr_sharpe_min,
        train_sharpe_max=tr_sharpe_max,
        test_sharpe_mean=te_sharpe_mean,
        test_sharpe_std=te_sharpe_std,
        test_sharpe_min=te_sharpe_min,
        test_sharpe_max=te_sharpe_max,
        test_return_mean=te_ret_mean,
        test_return_std=te_ret_std,
        test_max_dd_mean=float(np.mean(test_max_dds)) if test_max_dds else 0.0,
        test_win_rate_mean=float(np.mean(test_win_rates)) if test_win_rates else 0.0,
    )
    return result
