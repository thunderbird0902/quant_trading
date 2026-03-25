"""亏损限额检查 - 单笔最大亏损 + 每日累计亏损 + 连续亏损"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from core.enums import OrderSide, PositionSide
from core.exceptions import (
    ConsecutiveLossError,
    DailyLossLimitError,
    SingleLossLimitError,
)
from core.models import OrderRequest, PositionData, TradeData

logger = logging.getLogger("risk.loss_limit")


class LossLimitChecker:
    """
    亏损限额检查器。

    检查项：
    1. 单笔交易最大亏损（基于预估仓位价值）
    2. 当日累计亏损金额 / 比例
    3. 连续亏损笔数

    说明：
    - 单笔亏损检查在下单时执行（预估最大可能亏损）
    - 每日亏损 & 连续亏损在成交回报后更新
    """

    def __init__(
        self,
        max_daily_loss_pct: float = 0.05,
        max_single_loss_pct: float = 0.02,
        max_consecutive_losses: int = 5,
    ):
        """
        Args:
            max_daily_loss_pct:      每日最大亏损占总权益的比例（默认 5%）
            max_single_loss_pct:     单笔最大亏损占总权益的比例（默认 2%）
            max_consecutive_losses:  最大连续亏损笔数（默认 5 次）

        设计决策 - 日亏损重置时机：
            按 UTC 0 点重置（不是 24 小时滚动窗口）。
            理由：与交易所结算时间对齐，统计口径一致。
            若需要 24 小时滚动窗口，需将 _today 改为记录起始时间戳。
        """
        self.max_daily_loss_pct = Decimal(str(max_daily_loss_pct))
        self.max_single_loss_pct = Decimal(str(max_single_loss_pct))
        self.max_consecutive_losses = max_consecutive_losses

        # UTC 结算窗口宽限期（秒）：OKX 结算期间暂缓重置
        self._settlement_window_seconds: int = 60

        # 运行时统计
        self._total_equity: Decimal = Decimal("0")
        self._initial_equity: Decimal = Decimal("0")    # 今日开始时的权益（UTC 0 点）
        self._today: date = datetime.now(timezone.utc).date()   # 以 UTC 0 点为分界
        self._daily_pnl: Decimal = Decimal("0")         # 今日累计已实现盈亏
        self._consecutive_losses: int = 0               # 当前连续亏损笔数
        self._is_halted: bool = False                   # 是否已触发停止交易
        self._settlement_end_ts: datetime | None = None  # 当前结算窗口截止时间

        # 未实现盈亏跟踪（按持仓 key：inst_id_posside → unrealized_pnl）
        self._position_unrealized_pnl: dict[str, Decimal] = {}
        self._total_unrealized_pnl: Decimal = Decimal("0")

    # ─────────────────────── 外部更新 ────────────────────────────

    def update_equity(self, total_equity: Decimal) -> None:
        """更新总权益（由 RiskEngine 在收到 BALANCE_UPDATED 事件时调用）。"""
        now = datetime.now(timezone.utc)
        today = now.date()

        if self._settlement_end_ts is not None and now >= self._settlement_end_ts:
            self._settlement_end_ts = None

        if today != self._today:
            self._today = today
            from datetime import timedelta
            settlement_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            self._settlement_end_ts = settlement_start + timedelta(seconds=self._settlement_window_seconds)
            logger.info(
                "UTC 新日期 %s 检测到，进入结算窗口（宽限期 %ds），暂缓重置日统计",
                today, self._settlement_window_seconds,
            )
        elif self._settlement_end_ts is None:
            self._do_daily_reset(total_equity, today)

        if self._initial_equity == 0:
            self._initial_equity = total_equity

        self._total_equity = total_equity

    def _do_daily_reset(self, total_equity: Decimal, today: date) -> None:
        """执行日统计重置（供内部和测试调用）。"""
        logger.info("UTC 新交易日，重置风控统计 (上日权益=%.2f)", float(self._total_equity))
        self._today = today
        self._daily_pnl = Decimal("0")
        self._consecutive_losses = 0
        self._is_halted = False
        self._initial_equity = total_equity

    def update_unrealized_pnl(self, position: PositionData) -> None:
        """
        更新指定持仓的浮动盈亏，并检查是否触发亏损限额。

        由 RiskEngine 在收到 POSITION_UPDATED 事件时调用。
        同时监控「已实现 + 浮动」的综合亏损，防止持仓大幅亏损但未平仓时漏掉检测。
        """
        key = f"{position.inst_id}_{position.position_side.value}"
        if position.quantity <= 0:
            self._position_unrealized_pnl.pop(key, None)
        else:
            self._position_unrealized_pnl[key] = position.unrealized_pnl

        self._total_unrealized_pnl = sum(self._position_unrealized_pnl.values())

        # 综合亏损检查（已实现 + 浮动）
        if self._initial_equity > 0:
            combined = self._daily_pnl + self._total_unrealized_pnl
            if combined < 0:
                pct = abs(combined) / self._initial_equity
                if pct >= self.max_daily_loss_pct and not self._is_halted:
                    self._is_halted = True
                    logger.error(
                        "触发日亏损限额（含浮亏）| realized=%.2f unrealized=%.2f "
                        "combined=%.2f (%.1f%%)",
                        float(self._daily_pnl),
                        float(self._total_unrealized_pnl),
                        float(combined),
                        float(pct * 100),
                    )

    def on_trade(self, trade: TradeData, pnl: Decimal) -> None:
        """
        成交后更新统计。

        Args:
            trade: 成交记录
            pnl:   本次成交盈亏（正=盈利，负=亏损）
        """
        self._daily_pnl += pnl

        if pnl < 0:
            self._consecutive_losses += 1
            logger.warning(
                "成交亏损 %.2f USDT，连续亏损 %d 笔，今日累计 %.2f USDT",
                float(pnl), self._consecutive_losses, float(self._daily_pnl),
            )
        else:
            if self._consecutive_losses > 0:
                logger.info("成交盈利，连续亏损计数重置（之前 %d 笔）", self._consecutive_losses)
            self._consecutive_losses = 0

        # 检查是否触发今日亏损限额
        if self._total_equity > 0:
            daily_loss_pct = abs(self._daily_pnl) / self._initial_equity
            if self._daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss_pct:
                self._is_halted = True
                logger.error(
                    "⚠️ 触发每日亏损限额！今日亏损 %.2f USDT (%.1f%%)，已停止交易",
                    float(abs(self._daily_pnl)), float(daily_loss_pct * 100),
                )

        # 检查连续亏损
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._is_halted = True
            logger.error(
                "⚠️ 连续亏损 %d 笔，已停止交易",
                self._consecutive_losses,
            )

    # ─────────────────────── 检查 ────────────────────────────────

    def check(
        self,
        request: OrderRequest,
        current_price: Decimal | None = None,
        positions: list | None = None,
    ) -> None:
        """
        下单前检查亏损限额。

        触发停止后的策略：
        - 平仓/减仓订单（_is_reducing_order=True）始终允许通过，不扩大风险
        - 新开仓/加仓订单被拒绝，直到手动调用 reset_halt() 解除

        Args:
            request:       下单请求
            current_price: 当前价格（用于估算单笔最大亏损）
            positions:     当前持仓列表（用于判断 NET 模式下是否为减仓订单）
        """
        # 已触发停止：区分开仓和平仓
        if self._is_halted:
            if self._is_reducing_order(request, positions):
                logger.info(
                    "风控已停止，允许平仓/减仓订单通过 | "
                    "inst=%s side=%s pos_side=%s qty=%s",
                    request.inst_id,
                    request.side.value,
                    request.position_side.value if request.position_side else "NET",
                    request.quantity,
                )
                return
            raise DailyLossLimitError(
                f"风控已触发停止（仅允许平仓减仓）：今日已实现盈亏 "
                f"{float(self._daily_pnl):.2f} USDT，"
                f"浮动盈亏 {float(self._total_unrealized_pnl):.2f} USDT，"
                f"连续亏损 {self._consecutive_losses} 笔"
            )

        # 今日累计亏损检查（已实现）
        if self._initial_equity > 0 and self._daily_pnl < 0:
            daily_loss_pct = abs(self._daily_pnl) / self._initial_equity
            if daily_loss_pct >= self.max_daily_loss_pct:
                raise DailyLossLimitError(
                    f"今日累计亏损 {float(abs(self._daily_pnl)):.2f} USDT "
                    f"({float(daily_loss_pct):.1%})，超过每日限额 {float(self.max_daily_loss_pct):.1%}"
                )

        # 连续亏损检查
        if self._consecutive_losses >= self.max_consecutive_losses:
            raise ConsecutiveLossError(
                f"连续亏损 {self._consecutive_losses} 笔，"
                f"超过限制 {self.max_consecutive_losses} 笔"
            )

        # 单笔最大亏损预估（止损价已知时）
        if (current_price and request.stop_loss_price and
                self._total_equity > 0 and request.price):
            estimated_loss = abs(request.price - request.stop_loss_price) * request.quantity
            max_single_loss = self._total_equity * self.max_single_loss_pct
            if estimated_loss > max_single_loss:
                raise SingleLossLimitError(
                    f"[{request.inst_id}] 预估单笔亏损 {float(estimated_loss):.2f} USDT "
                    f"超过限额 {float(max_single_loss):.2f} USDT "
                    f"({float(self.max_single_loss_pct):.1%} × 总权益)"
                )

    def _is_reducing_order(
        self, request: OrderRequest, positions: list | None = None
    ) -> bool:
        """
        判断是否为减仓/平仓方向的订单。

        判断规则（基于 OKX 双仓模式）：
        - SELL + LONG 持仓方向 → 平多头（减仓）
        - BUY  + SHORT 持仓方向 → 平空头（减仓）
        - NET 仓位模式（position_side=None）：根据当前实际持仓判断
          - 有多头时 SELL → 减仓
          - 有空头时 BUY → 减仓

        注意：本函数只判断「方向意图」，不验证实际持仓是否存在。
        """
        # 双向持仓模式：直接根据 pos_side 判断
        if request.position_side is not None:
            pos_side = request.position_side
            side = request.side
            return (
                (pos_side == PositionSide.LONG  and side == OrderSide.SELL) or
                (pos_side == PositionSide.SHORT and side == OrderSide.BUY)
            )

        # NET 模式：根据当前持仓方向判断
        if positions is None:
            return False
        inst_positions = [p for p in positions if p.inst_id == request.inst_id]
        has_long = any(p.position_side == PositionSide.LONG for p in inst_positions)
        has_short = any(p.position_side == PositionSide.SHORT for p in inst_positions)
        # SELL 平多头，或 BUY 平空头
        return (request.side == OrderSide.SELL and has_long) or (
            request.side == OrderSide.BUY and has_short
        )

    # ─────────────────────── 状态查询 ────────────────────────────

    @property
    def is_halted(self) -> bool:
        """是否已停止交易。"""
        return self._is_halted

    @property
    def daily_pnl(self) -> Decimal:
        """今日累计盈亏。"""
        return self._daily_pnl

    @property
    def consecutive_losses(self) -> int:
        """当前连续亏损笔数。"""
        return self._consecutive_losses

    def reset_halt(self) -> None:
        """手动解除停止状态（需人工确认）。"""
        self._is_halted = False
        logger.warning("风控停止状态已手动解除")

    def get_status(self) -> dict:
        """返回风控状态摘要（已实现盈亏 + 浮动盈亏）。"""
        return {
            "is_halted": self._is_halted,
            "daily_realized_pnl": float(self._daily_pnl),
            "daily_unrealized_pnl": float(self._total_unrealized_pnl),
            "daily_combined_pnl": float(self._daily_pnl + self._total_unrealized_pnl),
            "daily_loss_pct": (
                float(abs(self._daily_pnl) / self._initial_equity)
                if self._initial_equity > 0 else 0.0
            ),
            "consecutive_losses": self._consecutive_losses,
            "total_equity": float(self._total_equity),
            "initial_equity": float(self._initial_equity),
            "today_utc": str(self._today),
        }
