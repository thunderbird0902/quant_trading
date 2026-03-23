"""绩效分析模块（已修复版）

修复清单:
  [P2-4] Sortino Ratio 改为标准算法（以 0 为目标，用全量收益率序列）
  [P2-5] 增加 round_trips（完整交易轮次）指标，与 total_trades 区分
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence


def _to_float(d: Decimal | float) -> float:
    return float(d)


def _max_consecutive(pnls: list[float], positive: bool) -> int:
    """计算最大连续盈利（positive=True）或亏损（positive=False）笔数"""
    max_streak = 0
    current = 0
    for p in pnls:
        if (positive and p > 0) or (not positive and p < 0):
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


class PerformanceAnalyzer:
    """
    绩效分析器。

    使用方式：
        analyzer = PerformanceAnalyzer(
            equity_curve=broker.equity_curve,
            trades=broker.get_trades(),
            initial_capital=Decimal("100000"),
            risk_free_rate=0.02,
            interval_seconds=3600,
        )
        report = analyzer.compute()
        print(analyzer.summary())
    """

    def __init__(
        self,
        equity_curve: list[tuple[datetime, Decimal]],
        trades: list,
        initial_capital: Decimal,
        risk_free_rate: float = 0.02,
        interval_seconds: int = 3600,
        filled_orders: list | None = None,
    ):
        self.equity_curve = equity_curve
        self.trades = trades
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate
        self.interval_seconds = interval_seconds
        self.filled_orders = filled_orders or []

        self._metrics: dict = {}

    # ─────────────────────── 主计算入口 ──────────────────────────

    def compute(self) -> dict:
        """计算全部指标，返回结果字典"""
        if not self.equity_curve:
            return {"error": "无权益曲线数据"}

        equities = [_to_float(e) for _, e in self.equity_curve]
        timestamps = [ts for ts, _ in self.equity_curve]

        init = _to_float(self.initial_capital)
        final = equities[-1]

        # ── 收益指标 ──────────────────────────────────────────────
        total_return = (final - init) / init
        duration_days = self._duration_days(timestamps)
        annual_return = self._annualize_return(total_return, duration_days)

        # ── 回撤指标 ──────────────────────────────────────────────
        max_dd, max_dd_duration = self._max_drawdown(equities, timestamps)
        peak_equity = max(equities) if equities else init
        max_dd_amount = max_dd * peak_equity   # 回撤金额（USDT）

        # ── 波动率 / 夏普比率 ─────────────────────────────────────
        returns = self._period_returns(equities)
        annual_vol = self._annual_volatility(returns)
        sharpe = self._sharpe_ratio(annual_return, annual_vol)

        # ── 卡玛比率 ──────────────────────────────────────────────
        calmar = annual_return / max_dd if max_dd > 0 else 0.0

        # ── Sortino 比率（[P2-4] 修正为标准算法）──────────────────
        sortino = self._sortino_ratio(annual_return, returns)

        # ── 交易统计 ──────────────────────────────────────────────
        trade_stats = self._trade_statistics()

        self._metrics = {
            # 基础
            "initial_capital": init,
            "final_equity": final,
            "total_pnl": final - init,
            "total_return_pct": total_return * 100,
            "annual_return_pct": annual_return * 100,
            # 回撤
            "max_drawdown_pct": max_dd * 100,
            "max_drawdown_amount": max_dd_amount,       # 最大回撤金额（USDT）
            "max_drawdown_duration_days": max_dd_duration,
            # 风险调整
            "annual_volatility_pct": annual_vol * 100,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            # 时间
            "backtest_days": duration_days,
            "start_date": timestamps[0].strftime("%Y-%m-%d") if timestamps else "N/A",
            "end_date": timestamps[-1].strftime("%Y-%m-%d") if timestamps else "N/A",
            # 交易
            **trade_stats,
        }
        return self._metrics

    def summary(self) -> str:
        """打印美化的绩效摘要"""
        if not self._metrics:
            self.compute()
        m = self._metrics

        lines = [
            "=" * 60,
            "  回测绩效报告",
            "=" * 60,
            f"  回测区间        {m.get('start_date')} → {m.get('end_date')} ({m.get('backtest_days', 0):.0f} 天)",
            "",
            "  【收益】",
            f"  初始资金        ${m['initial_capital']:>15,.2f} USDT",
            f"  最终权益        ${m['final_equity']:>15,.2f} USDT",
            f"  总盈亏          ${m['total_pnl']:>+15,.2f} USDT",
            f"  总收益率        {m['total_return_pct']:>+12.2f} %",
            f"  年化收益率      {m['annual_return_pct']:>+12.2f} %",
            "",
            "  【风险】",
            f"  最大回撤        {m['max_drawdown_pct']:>12.2f} %",
            f"  最大回撤金额    ${m.get('max_drawdown_amount', 0):>15,.2f} USDT",
            f"  最大回撤持续    {m['max_drawdown_duration_days']:>12.1f} 天",
            f"  年化波动率      {m['annual_volatility_pct']:>12.2f} %",
            "",
            "  【风险调整收益】",
            f"  夏普比率        {m['sharpe_ratio']:>12.4f}",
            f"  Sortino比率     {m['sortino_ratio']:>12.4f}",
            f"  卡玛比率        {m['calmar_ratio']:>12.4f}",
            "",
            "  【交易统计】",
            f"  成交笔数        {m['total_trades']:>12}  （开仓+平仓合计）",
            f"  完整交易轮次    {m.get('round_trips', 0):>12}  （一开一平为一轮）",
            f"  平均持仓时长    {m.get('avg_holding_hours', 0):>11.1f}h",
            f"  盈利次数        {m['win_trades']:>12}  ({m['win_rate_pct']:.1f}%)",
            f"  亏损次数        {m['loss_trades']:>12}",
            f"  胜率            {m['win_rate_pct']:>12.1f} %",
            f"  盈亏比          {m['profit_factor']:>12.4f}",
            f"  平均每笔盈亏    ${m['avg_trade_pnl']:>+15.2f} USDT",
            f"  最大单笔盈利    ${m.get('max_win', 0):>+15.2f} USDT",
            f"  最大单笔亏损    ${m.get('max_loss', 0):>+15.2f} USDT",
            f"  平均盈利        ${m.get('avg_win', 0):>+15.2f} USDT",
            f"  平均亏损        ${m.get('avg_loss', 0):>+15.2f} USDT",
            f"  最大连续盈利    {m.get('max_consecutive_wins', 0):>12}  笔",
            f"  最大连续亏损    {m.get('max_consecutive_losses', 0):>12}  笔",
            f"  期望收益        ${m.get('expectancy', 0):>+15.2f} USDT",
            f"  总手续费        ${m['total_fees']:>15,.2f} USDT",
            "=" * 60,
        ]
        return "\n".join(lines)

    # ─────────────────────── 内部计算方法 ────────────────────────

    def _duration_days(self, timestamps: list[datetime]) -> float:
        if len(timestamps) < 2:
            return 0.0
        return (timestamps[-1] - timestamps[0]).total_seconds() / 86400

    def _annualize_return(self, total_return: float, duration_days: float) -> float:
        if duration_days <= 0:
            return 0.0
        years = duration_days / 365.0
        if years == 0:
            return 0.0
        if years < 7 / 365:
            return total_return * (365.0 / duration_days)
        try:
            return (1 + total_return) ** (1 / years) - 1
        except (OverflowError, ZeroDivisionError):
            return 0.0

    def _period_returns(self, equities: list[float]) -> list[float]:
        """相邻两期收益率序列"""
        returns = []
        for i in range(1, len(equities)):
            prev = equities[i - 1]
            if prev == 0:
                returns.append(0.0)
            else:
                returns.append((equities[i] - prev) / prev)
        return returns

    def _annual_volatility(self, returns: list[float]) -> float:
        """年化波动率（基于 K 线周期换算）"""
        if len(returns) < 2:
            return 0.0
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        period_vol = math.sqrt(variance)
        periods_per_year = 365 * 86400 / self.interval_seconds
        return period_vol * math.sqrt(periods_per_year)

    def _sharpe_ratio(self, annual_return: float, annual_vol: float) -> float:
        if annual_vol == 0:
            return 0.0
        return (annual_return - self.risk_free_rate) / annual_vol

    def _sortino_ratio(self, annual_return: float, returns: list[float]) -> float:
        """
        [P2-4] 标准 Sortino Ratio:

        Sortino = (annual_return - rf) / annual_downside_deviation

        下行偏差 = sqrt(mean(min(r - target, 0)^2))，target = 0
        """
        if len(returns) < 2:
            return 0.0

        target = 0.0  # 目标收益率（可改为 rf / periods_per_year）
        squared = [(min(r - target, 0.0)) ** 2 for r in returns]
        downside_dev = math.sqrt(sum(squared) / len(squared))

        if downside_dev == 0:
            return 0.0

        periods_per_year = 365 * 86400 / self.interval_seconds
        annual_downside = downside_dev * math.sqrt(periods_per_year)

        return (annual_return - self.risk_free_rate) / annual_downside

    def _max_drawdown(
        self, equities: list[float], timestamps: list[datetime]
    ) -> tuple[float, float]:
        """
        计算最大回撤及其持续时间（天）。

        Returns:
            (max_drawdown, duration_days)
            max_drawdown 为正数（如 0.15 表示 15% 回撤）
        """
        if not equities:
            return 0.0, 0.0

        peak = equities[0]
        peak_ts = timestamps[0]
        max_dd = 0.0
        max_dd_duration = 0.0

        for equity, ts in zip(equities, timestamps):
            if equity > peak:
                peak = equity
                peak_ts = ts
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
                max_dd_duration = (ts - peak_ts).total_seconds() / 86400

        return max_dd, max_dd_duration

    def _trade_statistics(self) -> dict:
        """
        从成交明细计算交易统计。

        [P2-5] 增加 round_trips（完整交易轮次）指标。
        """
        from core.enums import OrderSide

        total = len(self.trades)
        total_fees = sum(_to_float(t.fee) for t in self.trades)
        long_trades = sum(1 for t in self.trades if t.side == OrderSide.BUY)
        short_trades = total - long_trades

        if not self.filled_orders:
            return {
                "total_trades": total,
                "round_trips": 0,
                "win_trades": 0,
                "loss_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl": 0.0,
                "total_fees": total_fees,
                "long_trades": long_trades,
                "short_trades": short_trades,
                "max_win": 0.0,
                "max_loss": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
                "expectancy": 0.0,
            }

        # 从已成交订单的 pnl 字段统计（只计平仓单，pnl != 0）
        close_pnls = [
            _to_float(o.pnl) for o in self.filled_orders
            if _to_float(o.pnl) != 0.0
        ]

        round_trips = len(close_pnls)
        win_trades = sum(1 for p in close_pnls if p > 0)
        loss_trades = sum(1 for p in close_pnls if p < 0)
        gross_profit = sum(p for p in close_pnls if p > 0)
        gross_loss = abs(sum(p for p in close_pnls if p < 0))

        win_rate = (win_trades / round_trips * 100) if round_trips else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        avg_pnl = (sum(close_pnls) / round_trips) if round_trips else 0.0

        # 增强指标
        max_win = max(close_pnls) if close_pnls else 0.0
        max_loss = min(close_pnls) if close_pnls else 0.0
        avg_win = (gross_profit / win_trades) if win_trades > 0 else 0.0
        avg_loss = (gross_loss / loss_trades) if loss_trades > 0 else 0.0
        max_cons_wins = _max_consecutive(close_pnls, positive=True)
        max_cons_losses = _max_consecutive(close_pnls, positive=False)
        win_rate_ratio = win_rate / 100.0
        expectancy = win_rate_ratio * avg_win - (1.0 - win_rate_ratio) * avg_loss

        return {
            "total_trades": total,
            "round_trips": round_trips,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "avg_trade_pnl": avg_pnl,
            "total_fees": total_fees,
            "long_trades": long_trades,
            "short_trades": short_trades,
            "max_win": max_win,
            "max_loss": max_loss,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_consecutive_wins": max_cons_wins,
            "max_consecutive_losses": max_cons_losses,
            "expectancy": expectancy,
            "avg_holding_hours": self._calc_avg_holding_time(),  # 平均持仓时长（小时）
        }

    def _calc_avg_holding_time(self) -> float:
        """
        计算平均持仓时长（小时）。

        通过 FIFO 配对开仓订单和平仓订单（按 inst_id 分组）：
        - 开仓订单：pnl == 0（成本计入，尚未产生盈亏）
        - 平仓订单：pnl != 0（产生实际盈亏）

        Returns:
            平均持仓时长（小时），无完整轮次时返回 0.0
        """
        from collections import deque
        if not self.filled_orders:
            return 0.0

        entry_queues: dict[str, deque] = {}
        hold_times: list[float] = []

        for order in self.filled_orders:
            pnl = float(order.pnl)
            inst = order.inst_id
            if pnl == 0.0:
                # 开仓单：记录实际成交时间（update_time）
                if inst not in entry_queues:
                    entry_queues[inst] = deque()
                entry_queues[inst].append(order.update_time)
            else:
                # 平仓单：与最早的开仓时间配对
                q = entry_queues.get(inst)
                if q:
                    entry_time = q.popleft()
                    delta_h = (
                        (order.update_time - entry_time).total_seconds() / 3600
                    )
                    hold_times.append(max(delta_h, 0.0))

        return sum(hold_times) / len(hold_times) if hold_times else 0.0