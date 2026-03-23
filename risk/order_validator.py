"""订单参数校验 - 下单前检查精度、范围等"""

from __future__ import annotations

import logging
from decimal import Decimal

from core.enums import OrderSide, OrderType, PositionSide
from core.exceptions import OrderValidationError
from core.models import Instrument, OrderRequest
from utils.helpers import decimal_places, round_to

logger = logging.getLogger("risk.order_validator")


class OrderValidator:
    """
    下单前参数校验器。

    校验项：
    1. 价格精度（是否符合 tickSz）
    2. 数量精度（是否符合 lotSz）
    3. 最小下单量（>= minSz）
    4. 最大下单量（<= maxLmtSz / maxMktSz）
    5. 价格合理性（偏离当前价不超过阈值）
    6. 订单方向合理性（非对冲模式下，有多头不允许新开空头，反之亦然）
    """

    def __init__(
        self,
        price_deviation_limit: float = 0.1,
        hedge_mode: bool = True,
    ):
        """
        Args:
            price_deviation_limit: 委托价格相对最新价的最大偏离比例（默认 10%）
            hedge_mode:            True=对冲模式（允许多空同时持仓，如套利/对冲策略）；
                                   False=单向模式（有多头时不允许新开空头，反之亦然）。
                                   默认 True，与 OKX 双仓模式对齐。
        """
        self.price_deviation_limit = Decimal(str(price_deviation_limit))
        self.hedge_mode = hedge_mode

    def validate(
        self,
        request: OrderRequest,
        instrument: Instrument,
        current_price: Decimal | None = None,
        positions: list | None = None,
    ) -> None:
        """
        校验下单请求。校验失败时抛出 OrderValidationError。

        Args:
            request:       下单请求
            instrument:    产品信息（提供精度参数）
            current_price: 当前最新价（用于价格合理性检查，None 时跳过）
            positions:     当前持仓列表（用于方向合理性检查，None 时跳过）
        """
        self._check_quantity(request, instrument)
        if request.order_type != OrderType.MARKET and request.price is not None:
            self._check_price(request, instrument, current_price)
        if not self.hedge_mode and positions is not None:
            self._check_direction(request, positions)

    # ─────────────────────── 私有校验 ────────────────────────────

    def _check_quantity(self, request: OrderRequest, inst: Instrument) -> None:
        """数量校验：精度 + 最小量 + 最大量。"""
        qty = request.quantity

        # 最小下单量
        if qty < inst.min_size:
            raise OrderValidationError(
                f"[{request.inst_id}] 委托数量 {qty} 小于最小下单量 {inst.min_size}"
            )

        # 最大下单量
        if request.order_type == OrderType.MARKET:
            max_size = inst.max_market_size
        else:
            max_size = inst.max_limit_size

        if max_size > 0 and qty > max_size:
            raise OrderValidationError(
                f"[{request.inst_id}] 委托数量 {qty} 超过最大下单量 {max_size}"
            )

        # 数量精度
        if inst.lot_size > 0:
            adjusted = round_to(qty, inst.lot_size)
            if adjusted != qty:
                raise OrderValidationError(
                    f"[{request.inst_id}] 委托数量 {qty} 精度不符合 lotSz={inst.lot_size}，"
                    f"应调整为 {adjusted}"
                )

    def _check_price(
        self,
        request: OrderRequest,
        inst: Instrument,
        current_price: Decimal | None,
    ) -> None:
        """价格校验：精度 + 偏离限制。"""
        price = request.price
        if price is None or price <= 0:
            raise OrderValidationError(
                f"[{request.inst_id}] 委托价格无效: {price}"
            )

        # 价格精度
        if inst.tick_size > 0:
            adjusted = round_to(price, inst.tick_size)
            if adjusted != price:
                raise OrderValidationError(
                    f"[{request.inst_id}] 委托价格 {price} 精度不符合 tickSz={inst.tick_size}，"
                    f"应调整为 {adjusted}"
                )

        # 价格偏离检查（仅在提供最新价时执行）
        if current_price and current_price > 0:
            deviation = abs(price - current_price) / current_price
            if deviation > self.price_deviation_limit:
                raise OrderValidationError(
                    f"[{request.inst_id}] 委托价格 {price} 偏离最新价 {current_price} "
                    f"达 {float(deviation):.1%}，超过限制 {float(self.price_deviation_limit):.1%}"
                )

    def _check_direction(self, request: OrderRequest, positions: list) -> None:
        """
        订单方向合理性检查（仅在非对冲模式 hedge_mode=False 时调用）。

        规则：
        - 已有多头（LONG）持仓时，禁止新开空头（BUY + SHORT 方向）
        - 已有空头（SHORT）持仓时，禁止新开多头（SELL + LONG 方向）
        - 减仓/平仓方向（有多头时 SELL LONG，有空头时 BUY SHORT）始终允许
        - 单向 NET 持仓模式（position_side=None）不做方向检查

        对冲/套利策略应在初始化 OrderValidator 时设置 hedge_mode=True（默认值）。
        """
        req_side = request.side
        req_pos_side = request.position_side

        for pos in positions:
            if pos.inst_id != request.inst_id or pos.quantity <= 0:
                continue

            # 已有多头，禁止新开空头（即 side=BUY, position_side=SHORT）
            if (pos.position_side == PositionSide.LONG
                    and req_side == OrderSide.BUY
                    and req_pos_side == PositionSide.SHORT):
                raise OrderValidationError(
                    f"[{request.inst_id}] 非对冲模式：已有多头持仓 qty={pos.quantity}，"
                    f"禁止新开空头（请先平多头，或切换至对冲模式）"
                )

            # 已有空头，禁止新开多头（即 side=SELL, position_side=LONG）
            if (pos.position_side == PositionSide.SHORT
                    and req_side == OrderSide.SELL
                    and req_pos_side == PositionSide.LONG):
                raise OrderValidationError(
                    f"[{request.inst_id}] 非对冲模式：已有空头持仓 qty={pos.quantity}，"
                    f"禁止新开多头（请先平空头，或切换至对冲模式）"
                )

    def auto_adjust(self, request: OrderRequest, instrument: Instrument) -> OrderRequest:
        """
        自动调整数量和价格到合法精度（向下截断）。

        与 validate() 不同，此方法不抛异常，而是直接修正请求。
        适用于策略层希望自动对齐精度的场景。

        Returns:
            调整后的 OrderRequest（修改了 price 和 quantity）
        """
        import copy
        req = copy.copy(request)

        # 调整数量
        if instrument.lot_size > 0:
            req.quantity = round_to(req.quantity, instrument.lot_size)

        # 调整价格
        if req.price is not None and instrument.tick_size > 0:
            req.price = round_to(req.price, instrument.tick_size)

        return req
