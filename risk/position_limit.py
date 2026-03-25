"""仓位限制检查"""

from __future__ import annotations

import logging
from decimal import Decimal

from core.enums import Exchange, OrderSide, PositionSide
from core.exceptions import PositionLimitError
from core.models import OrderRequest, PositionData

logger = logging.getLogger("risk.position_limit")


class PositionLimitChecker:
    """
    仓位限制检查器。

    检查项：
    1. 单品种最大持仓量（per_symbol_limits 自定义上限）
    2. 单品种最大持仓价值占总权益的比例（max_position_pct）
    3. 账户全部持仓价值占总权益的比例（max_total_position_pct）

    设计决策：
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. 持仓限制粒度：按「交易对（inst_id）」计算，不是按基础货币。 │
    │    例：BTC-USDT-SWAP 和 BTC-USD-SWAP 是独立品种，分别计算。  │
    │    如需按基础货币汇总，需在外部折算后传入，或扩展本类。       │
    │                                                             │
    │ 2. 限制维度：全部是「账户级别」，没有「单策略」维度。          │
    │    所有策略共享同一账户的仓位限额。若需要按策略隔离，建议在    │
    │    策略层自行管理订单数量，而非在此处拆分——因为此处的         │
    │    _positions 只有 inst_id 粒度，不携带 strategy_id。       │
    └─────────────────────────────────────────────────────────────┘

    配置示例（来自 settings.yaml risk 节）：
        max_position_pct: 0.3       # 单品种持仓价值 / 总权益 ≤ 30%
        max_total_position_pct: 0.9 # 所有品种累计持仓 / 总权益 ≤ 90%
        per_symbol_limits:          # 按交易对设置数量上限（可选）
            BTC-USDT-SWAP: {max_qty: 10, max_value_usd: 100000}
    """

    def __init__(
        self,
        max_position_pct: float = 0.3,
        max_total_position_pct: float = 0.9,
        per_symbol_limits: dict | None = None,
    ):
        self.max_position_pct = Decimal(str(max_position_pct))
        self.max_total_position_pct = Decimal(str(max_total_position_pct))
        self.per_symbol_limits: dict[str, dict] = per_symbol_limits or {}

        # 实时持仓快照（由 RiskEngine 更新）
        self._positions: dict[str, PositionData] = {}    # inst_id → PositionData
        self._total_equity: Decimal = Decimal("0")

    # ─────────────────────── 外部更新 ────────────────────────────

    def update_position(self, position: PositionData) -> None:
        """更新持仓快照（由 RiskEngine 在收到 POSITION_UPDATED 事件时调用）。"""
        pos_side = position.position_side or PositionSide.NET
        key = f"{position.inst_id}_{pos_side.value}"
        if position.quantity == 0:
            self._positions.pop(key, None)
        else:
            self._positions[key] = position

    def update_equity(self, total_equity: Decimal) -> None:
        """更新总权益（由 RiskEngine 在收到 BALANCE_UPDATED 事件时调用）。"""
        self._total_equity = total_equity

    # ─────────────────────── 检查 ────────────────────────────────

    def check(self, request: OrderRequest, current_price: Decimal | None = None) -> None:
        """
        下单前执行仓位限制检查。

        Args:
            request:       下单请求
            current_price: 当前价格（用于计算名义价值），None 时跳过价值检查

        Raises:
            PositionLimitError: 触发仓位限制
        """
        # 单品种配置限制
        self._check_per_symbol(request)

        # 基于价值的比例限制（需要知道当前价和总权益）
        if current_price and self._total_equity > 0:
            self._check_value_pct(request, current_price)

    def _check_per_symbol(self, request: OrderRequest) -> None:
        """
        检查品种级别的持仓量上限。

        使用 inst_id（交易对）作为 key，汇总该交易对下所有方向的持仓量之和。
        注意：多头和空头的数量会相加（均为正数），因此双向持仓时会触发更严的限制。
        """
        limit = self.per_symbol_limits.get(request.inst_id)
        if not limit:
            return

        max_qty = limit.get("max_qty")
        if max_qty is None:
            return

        # 计算该品种当前持仓量（多空汇总）
        current_qty = sum(
            p.quantity for key, p in self._positions.items()
            if p.inst_id == request.inst_id
        )

        is_closing = self._is_closing_order(request)
        after_qty = current_qty - request.quantity if is_closing \
                    else current_qty + request.quantity

        if after_qty > Decimal(str(max_qty)):
            raise PositionLimitError(
                f"[{request.inst_id}] 下单后持仓量 {after_qty} 超过上限 {max_qty}"
            )

    def _is_closing_order(self, request: OrderRequest) -> bool:
        """
        判断订单是否为平仓/减仓方向。

        在双向持仓模式下（hedge）：
        - SELL + LONG = 平多（减仓）
        - BUY + SHORT = 平空（减仓）

        在 NET 模式下（无 posSide），根据当前实际持仓方向判断：
        - 有多头时 SELL = 平多
        - 有空头时 BUY = 平空
        """
        if request.position_side is not None:
            pos_side = request.position_side
            side = request.side
            return (
                (pos_side == PositionSide.LONG and side == OrderSide.SELL) or
                (pos_side == PositionSide.SHORT and side == OrderSide.BUY)
            )
        inst_positions = [
            p for p in self._positions.values()
            if p.inst_id == request.inst_id
        ]
        has_long = any(p.position_side == PositionSide.LONG for p in inst_positions)
        has_short = any(p.position_side == PositionSide.SHORT for p in inst_positions)
        return (request.side == OrderSide.SELL and has_long) or (
            request.side == OrderSide.BUY and has_short
        )

    def _check_value_pct(self, request: OrderRequest, current_price: Decimal) -> None:
        """检查单品种持仓价值占总权益的比例。"""
        if self._total_equity <= 0:
            return

        new_order_value = request.quantity * current_price

        current_value = sum(
            p.quantity * p.mark_price
            for p in self._positions.values()
            if p.inst_id == request.inst_id
        )

        is_closing = self._is_closing_order(request)
        after_value = current_value - new_order_value if is_closing else current_value + new_order_value
        after_pct = abs(after_value) / self._total_equity

        if after_pct > self.max_position_pct:
            raise PositionLimitError(
                f"[{request.inst_id}] 下单后仓位占比 {float(after_pct):.1%} "
                f"超过单品种限制 {float(self.max_position_pct):.1%}"
            )

        # 总持仓价值检查
        total_current = sum(
            p.quantity * p.mark_price for p in self._positions.values()
        )
        total_value = total_current - new_order_value if is_closing else total_current + new_order_value
        total_pct = abs(total_value) / self._total_equity

        if total_pct > self.max_total_position_pct:
            raise PositionLimitError(
                f"下单后总持仓占比 {float(total_pct):.1%} "
                f"超过总仓位限制 {float(self.max_total_position_pct):.1%}"
            )
