"""模拟撮合引擎（已修复版 - 支持做空）

修复清单:
  [P0-1] send_order 增加资金充足性检查（支持做空保证金模式）
  [P1-1] _execute_fill BUY 平空后反向开多
  [P1-2] _execute_fill SELL 平多后反向开空
  [P1-3] force_close_position 空头平仓 fee 不再双扣
  [P2-1] 订单时间戳使用模拟 bar 时间而非 datetime.now
  [P2-2] _snapshot_equity 同一时间戳覆盖而非追加
  [P2-3] order.pnl 改为净利润（含开平两端手续费），修正胜率/盈亏比统计
  [P2-4] get_balance() total_equity 改为实时计算而非上一次快照
  [P2-5] 开空注释修正：frozen_margin 只记录保证金，不含手续费
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from core.enums import OrderSide, OrderStatus, OrderType, PositionSide, Exchange, MarginMode
from core.models import (
    BarData, OrderData, OrderRequest, PositionData, BalanceData,
    CurrencyBalance, TradeData,
)

logger = logging.getLogger(__name__)


class SimulatedBroker:
    """
    模拟撮合引擎（支持做空）。

    持有虚拟账户余额、持仓快照，并在每根 K 线上撮合挂单。
    支持加密货币合约双向交易：做多 / 做空 / 反转仓位。

    资金模型：
    - 做多开仓：扣除 price * qty（全额占用）
    - 做空开仓：冻结 price * qty 作为保证金（1x 杠杆等效）
    - 平仓：释放保证金 + 结算盈亏

    Args:
        initial_capital: 初始资金（USDT）
        taker_fee:       市价单手续费率（默认 0.05%）
        maker_fee:       限价单手续费率（默认 0.02%）
        slippage_pct:    市价单滑点（相对偏移，默认 0）
        exchange:        交易所枚举

    Callbacks:
        on_order: 订单状态变化回调 (order: OrderData) -> None
        on_trade: 成交回调 (trade: TradeData) -> None
    """

    def __init__(
        self,
        initial_capital: Decimal,
        taker_fee: Decimal = Decimal("0.0005"),
        maker_fee: Decimal = Decimal("0.0002"),
        slippage_pct: Decimal = Decimal("0"),
        exchange: Exchange = Exchange.OKX,
    ):
        self.initial_capital = initial_capital
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.slippage_pct = slippage_pct
        self.exchange = exchange

        # 账户状态
        self._cash: Decimal = initial_capital
        self._total_equity: Decimal = initial_capital

        # 持仓: inst_id -> {quantity, avg_price, realized_pnl, mark_price, frozen_margin}
        # quantity > 0 = 多头, quantity < 0 = 空头
        self._positions: dict[str, dict] = {}

        # 挂单队列
        self._pending_orders: dict[str, OrderData] = {}

        # 历史成交
        self._trades: list[TradeData] = []
        self._filled_orders: list[OrderData] = []

        # 权益序列（每根 K 线结束时快照）
        self.equity_curve: list[tuple[datetime, Decimal]] = []

        # 模拟时钟：由 engine 在每根 bar 更新
        self._current_timestamp: datetime = datetime.now(timezone.utc)

        # Callbacks
        self.on_order: Callable[[OrderData], None] | None = None
        self.on_trade: Callable[[TradeData], None] | None = None

    # ─────────────────── 模拟时钟 ───────────────────────────────

    def set_current_timestamp(self, ts: datetime) -> None:
        """由 BacktestEngine 在每根 bar 开始时调用"""
        self._current_timestamp = ts

    # ─────────────────────── 账户查询 ────────────────────────────

    def get_balance(self) -> BalanceData:
        """获取模拟账户余额（total_equity 实时计算，available_balance 为当前现金）"""
        current_equity = self._cash + self._calc_position_value()
        return BalanceData(
            exchange=self.exchange,
            total_equity=current_equity,
            available_balance=self._cash,
            frozen_balance=current_equity - self._cash,
            unrealized_pnl=self._calc_unrealized_pnl(),
            details=[CurrencyBalance(
                currency="USDT",
                available=self._cash,
                frozen=current_equity - self._cash,
                equity=current_equity,
                equity_usd=current_equity,
            )],
            update_time=self._current_timestamp,
        )

    def get_position(self, inst_id: str) -> PositionData | None:
        """获取单一产品持仓"""
        pos_info = self._positions.get(inst_id)
        if not pos_info or pos_info["quantity"] == Decimal("0"):
            return None
        qty = pos_info["quantity"]
        avg_price = pos_info["avg_price"]
        mark_price = pos_info.get("mark_price", avg_price)
        unrealized = (mark_price - avg_price) * qty
        return PositionData(
            inst_id=inst_id,
            exchange=self.exchange,
            position_side=PositionSide.NET,
            quantity=qty,
            avg_price=avg_price,
            unrealized_pnl=unrealized,
            unrealized_pnl_ratio=(
                unrealized / (avg_price * abs(qty))
                if avg_price * abs(qty) else Decimal("0")
            ),
            realized_pnl=pos_info.get("realized_pnl", Decimal("0")),
            leverage=1,
            liquidation_price=Decimal("0"),
            margin=avg_price * abs(qty),
            margin_ratio=Decimal("1"),
            margin_mode=MarginMode.CASH,
            mark_price=mark_price,
            update_time=self._current_timestamp,
        )

    def get_all_positions(self) -> list[PositionData]:
        """获取所有持仓"""
        result = []
        for inst_id in self._positions:
            pos = self.get_position(inst_id)
            if pos:
                result.append(pos)
        return result

    # ─────────────────────── 下单 / 撤单 ─────────────────────────

    def send_order(self, request: OrderRequest) -> OrderData:
        """
        提交模拟订单（不立即撮合，等下一根 K 线）。

        资金检查逻辑（支持做空）：
        - BUY:
          - 如果当前有空头持仓，买入 <= 空头数量 是平仓，不需额外资金
          - 买入超过空头数量的部分（或无空头时全部）= 开多头，需要 cash >= price * qty
        - SELL:
          - 如果当前有多头持仓，卖出 <= 多头数量 是平仓，不需额外资金
          - 卖出超过多头数量的部分（或无多头时全部）= 开空头，需要 cash >= price * qty（保证金）
        """
        order_id = f"BT-{uuid.uuid4().hex[:12].upper()}"
        ts = self._current_timestamp

        # ── 估算价格 ────────────────────────────────────────────
        if request.order_type == OrderType.MARKET:
            est_price = self._get_latest_mark_price(request.inst_id)
            if est_price is None or est_price == Decimal("0"):
                est_price = Decimal("0")  # 首次交易前无标记价，跳过检查
            fee_rate = self.taker_fee
        else:
            est_price = request.price or Decimal("0")
            fee_rate = self.maker_fee

        # ── 资金检查 ────────────────────────────────────────────
        if est_price > Decimal("0"):
            pos_info = self._positions.get(request.inst_id)
            cur_qty = pos_info["quantity"] if pos_info else Decimal("0")

            if request.side == OrderSide.BUY:
                # 空头持仓可以被平仓的部分
                closable = abs(cur_qty) if cur_qty < 0 else Decimal("0")
                # 需要新开多头的部分
                open_qty = max(request.quantity - closable, Decimal("0"))
                if open_qty > 0:
                    est_cost = est_price * open_qty * (Decimal("1") + fee_rate)
                    if self._cash < est_cost:
                        return self._reject_order(
                            order_id, request, ts,
                            f"资金不足 cash={self._cash:.2f} < cost={est_cost:.2f}"
                        )

            elif request.side == OrderSide.SELL:
                # 多头持仓可以被平仓的部分
                closable = cur_qty if cur_qty > 0 else Decimal("0")
                # 需要新开空头的部分（做空需要保证金）
                open_qty = max(request.quantity - closable, Decimal("0"))
                if open_qty > 0:
                    est_margin = est_price * open_qty * (Decimal("1") + fee_rate)
                    if self._cash < est_margin:
                        return self._reject_order(
                            order_id, request, ts,
                            f"保证金不足 cash={self._cash:.2f} < margin={est_margin:.2f}"
                        )

        # ── 订单通过检查，加入挂单队列 ──────────────────────────
        order = OrderData(
            order_id=order_id,
            client_order_id=request.client_order_id or order_id,
            inst_id=request.inst_id,
            exchange=self.exchange,
            side=request.side,
            order_type=request.order_type,
            price=request.price or Decimal("0"),
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            filled_price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
            fee=Decimal("0"),
            pnl=Decimal("0"),
            create_time=ts,
            update_time=ts,
            position_side=request.position_side,
        )
        self._pending_orders[order_id] = order

        if self.on_order:
            self.on_order(order)

        return order

    def _reject_order(
        self, order_id: str, request: OrderRequest, ts: datetime, reason: str
    ) -> OrderData:
        """生成一个 REJECTED 状态的订单"""
        order = OrderData(
            order_id=order_id,
            client_order_id=request.client_order_id or order_id,
            inst_id=request.inst_id,
            exchange=self.exchange,
            side=request.side,
            order_type=request.order_type,
            price=request.price or Decimal("0"),
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            filled_price=Decimal("0"),
            status=OrderStatus.REJECTED,
            fee=Decimal("0"),
            pnl=Decimal("0"),
            create_time=ts,
            update_time=ts,
            position_side=request.position_side,
        )
        logger.warning("订单被拒绝: %s inst=%s qty=%s", reason, request.inst_id, request.quantity)
        if self.on_order:
            self.on_order(order)
        return order

    def cancel_order(self, order_id: str, inst_id: str) -> bool:
        """撤销挂单"""
        order = self._pending_orders.pop(order_id, None)
        if not order:
            return False
        order.status = OrderStatus.CANCELLED
        order.update_time = self._current_timestamp
        if self.on_order:
            self.on_order(order)
        return True

    # ─────────────────────── 核心撮合 ────────────────────────────

    def match_orders(self, bar: BarData) -> list[TradeData]:
        """
        用一根已完成的 K 线撮合所有挂单。

        撮合规则：
        - 市价单：以开盘价成交（加滑点）
        - 限价买单：low <= price → 以 min(price, open) 成交
        - 限价卖单：high >= price → 以 max(price, open) 成交

        Returns:
            本次撮合产生的成交列表
        """
        if not self._pending_orders:
            self._update_mark_prices(bar)
            self._snapshot_equity(bar.timestamp)
            return []

        trades = []
        to_fill = []

        for order_id, order in list(self._pending_orders.items()):
            if order.inst_id != bar.inst_id:
                continue

            fill_price: Decimal | None = None

            if order.order_type == OrderType.MARKET:
                raw_price = bar.open
                if order.side == OrderSide.BUY:
                    fill_price = raw_price * (1 + self.slippage_pct)
                else:
                    fill_price = raw_price * (1 - self.slippage_pct)

            elif order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and bar.low <= order.price:
                    fill_price = min(order.price, bar.open)
                elif order.side == OrderSide.SELL and bar.high >= order.price:
                    fill_price = max(order.price, bar.open)

            if fill_price is not None:
                to_fill.append((order, fill_price))

        for order, fill_price in to_fill:
            trade = self._execute_fill(order, fill_price, bar.timestamp)
            trades.append(trade)
            del self._pending_orders[order.order_id]

        self._update_mark_prices(bar)
        self._snapshot_equity(bar.timestamp)
        return trades

    # ─────────────────────── 内部方法 ────────────────────────────

    def _get_latest_mark_price(self, inst_id: str) -> Decimal | None:
        """获取最近的标记价格（用于资金检查估算）"""
        pos_info = self._positions.get(inst_id)
        if pos_info and pos_info.get("mark_price"):
            return pos_info["mark_price"]
        return None

    def _execute_fill(
        self, order: OrderData, fill_price: Decimal, ts: datetime
    ) -> TradeData:
        """
        执行成交，更新账户状态。

        资金模型（支持做空）：
        - 做多开仓: cash -= price * qty + fee
        - 做多平仓: cash += price * qty - fee
                    pnl_net = (fill - avg) * qty - open_fee - close_fee
        - 做空开仓: cash -= price * qty + fee  （fee 直接扣，保证金冻结 price*qty）
        - 做空平仓: cash += avg * qty + pnl_gross - fee
                    pnl_net = (avg - fill) * qty - open_fee - close_fee

        order.pnl 存储净利润（含开平两端手续费），与 total_fees 合计等于毛利润。
        """
        qty = order.quantity
        is_maker = order.order_type == OrderType.LIMIT
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        fee = fill_price * qty * fee_rate

        pnl = Decimal("0")
        pos_info = self._positions.get(order.inst_id, {
            "quantity": Decimal("0"),
            "avg_price": Decimal("0"),
            "realized_pnl": Decimal("0"),
            "mark_price": fill_price,
            "frozen_margin": Decimal("0"),
        })
        cur_qty = pos_info["quantity"]
        avg_price = pos_info["avg_price"]
        frozen_margin = pos_info.get("frozen_margin", Decimal("0"))

        if order.side == OrderSide.BUY:
            if cur_qty >= 0:
                # ── 加仓多头 / 新开多头 ──────────────────────────
                new_qty = cur_qty + qty
                pos_info["avg_price"] = (
                    (avg_price * cur_qty + fill_price * qty) / new_qty
                    if new_qty > 0 else fill_price
                )
                pos_info["quantity"] = new_qty
                self._cash -= fill_price * qty + fee
            else:
                # ── 平空头（可能反转为多头）─────────────────────
                close_qty = min(qty, abs(cur_qty))
                pnl_gross = (avg_price - fill_price) * close_qty  # 空头毛盈亏
                # 净利润 = 毛利润 - 平仓手续费 - 等比例开仓手续费
                open_fee  = avg_price * close_qty * fee_rate
                pnl = pnl_gross - fee - open_fee

                # 释放平仓部分的保证金
                released_margin = avg_price * close_qty
                self._cash += released_margin + pnl_gross - fee

                # 更新冻结保证金
                frozen_margin -= released_margin
                if frozen_margin < 0:
                    frozen_margin = Decimal("0")
                pos_info["frozen_margin"] = frozen_margin

                pos_info["realized_pnl"] = pos_info.get("realized_pnl", Decimal("0")) + pnl

                open_qty = qty - close_qty  # 反转部分
                if open_qty > Decimal("0"):
                    # 平完空头后，剩余部分开多头
                    pos_info["quantity"] = open_qty
                    pos_info["avg_price"] = fill_price
                    pos_info["frozen_margin"] = Decimal("0")
                    self._cash -= fill_price * open_qty  # 开多扣款
                else:
                    remaining = cur_qty + close_qty
                    pos_info["quantity"] = remaining
                    if remaining == Decimal("0"):
                        pos_info["avg_price"] = Decimal("0")
                        pos_info["frozen_margin"] = Decimal("0")

        else:  # SELL
            if cur_qty <= 0:
                # ── 加仓空头 / 新开空头 ──────────────────────────
                new_qty = cur_qty - qty
                pos_info["avg_price"] = (
                    (avg_price * abs(cur_qty) + fill_price * qty) / abs(new_qty)
                    if new_qty != 0 else fill_price
                )
                pos_info["quantity"] = new_qty
                # 做空冻结保证金 = fill_price * qty（1x 杠杆等效）
                # 手续费单独从 cash 扣除，不计入 frozen_margin
                self._cash -= fill_price * qty + fee
                frozen_margin += fill_price * qty
                pos_info["frozen_margin"] = frozen_margin
            else:
                # ── 平多头（可能反转为空头）─────────────────────
                close_qty = min(qty, cur_qty)
                pnl_gross = (fill_price - avg_price) * close_qty
                # 净利润 = 毛利润 - 平仓手续费 - 等比例开仓手续费
                open_fee  = avg_price * close_qty * fee_rate
                pnl = pnl_gross - fee - open_fee
                pos_info["realized_pnl"] = pos_info.get("realized_pnl", Decimal("0")) + pnl
                self._cash += fill_price * close_qty - fee

                open_qty = qty - close_qty  # 反转部分
                if open_qty > Decimal("0"):
                    # 平完多头后，剩余部分开空头
                    pos_info["quantity"] = -open_qty
                    pos_info["avg_price"] = fill_price
                    # 做空冻结保证金
                    self._cash -= fill_price * open_qty  # 冻结保证金
                    pos_info["frozen_margin"] = fill_price * open_qty
                else:
                    remaining = cur_qty - close_qty
                    pos_info["quantity"] = remaining
                    if remaining == Decimal("0"):
                        pos_info["avg_price"] = Decimal("0")
                        pos_info["frozen_margin"] = Decimal("0")

        self._positions[order.inst_id] = pos_info

        # 更新订单状态
        order.filled_quantity = qty
        order.filled_price = fill_price
        order.fee = fee
        order.pnl = pnl
        order.status = OrderStatus.FILLED
        order.update_time = ts

        if self.on_order:
            self.on_order(order)

        self._filled_orders.append(order)

        # 生成成交记录
        trade = TradeData(
            trade_id=f"T-{uuid.uuid4().hex[:10].upper()}",
            order_id=order.order_id,
            inst_id=order.inst_id,
            exchange=self.exchange,
            side=order.side,
            price=fill_price,
            quantity=qty,
            fee=fee,
            fee_ccy="USDT",
            timestamp=ts,
        )
        self._trades.append(trade)

        if self.on_trade:
            self.on_trade(trade)

        return trade

    def _update_mark_prices(self, bar: BarData) -> None:
        """用 K 线收盘价更新标记价格"""
        pos_info = self._positions.get(bar.inst_id)
        if pos_info:
            pos_info["mark_price"] = bar.close

    def _calc_unrealized_pnl(self) -> Decimal:
        """计算所有持仓的未实现盈亏"""
        total = Decimal("0")
        for pos_info in self._positions.values():
            qty = pos_info.get("quantity", Decimal("0"))
            avg = pos_info.get("avg_price", Decimal("0"))
            mark = pos_info.get("mark_price", avg)
            total += (mark - avg) * qty
        return total

    def _calc_position_value(self) -> Decimal:
        """
        计算持仓对总权益的贡献。

        - 多头：开仓时已从现金扣除成本，需加回当前市值（mark * qty）
        - 空头：开仓时已冻结保证金（从 cash 扣除），需加回保证金 + 未实现盈亏
                 保证金 = avg * |qty|, 未实现盈亏 = (avg - mark) * |qty|
                 合计 = avg * |qty| + (avg - mark) * |qty| = mark * |qty| 的镜像
                 简化: frozen_margin + (avg - mark) * |qty|
        """
        total = Decimal("0")
        for pos_info in self._positions.values():
            qty = pos_info.get("quantity", Decimal("0"))
            avg = pos_info.get("avg_price", Decimal("0"))
            mark = pos_info.get("mark_price", avg)
            frozen = pos_info.get("frozen_margin", Decimal("0"))
            if qty > 0:
                # 多头：加回当前市值
                total += mark * qty
            elif qty < 0:
                # 空头：保证金 + 未实现盈亏
                unrealized = (avg - mark) * abs(qty)
                total += frozen + unrealized
        return total

    def _snapshot_equity(self, ts: datetime) -> None:
        """
        快照当前总权益 = 现金 + 持仓市值贡献。

        同一时间戳覆盖而非追加，避免重复。
        """
        equity = self._cash + self._calc_position_value()
        self._total_equity = equity
        if self.equity_curve and self.equity_curve[-1][0] == ts:
            self.equity_curve[-1] = (ts, equity)
        else:
            self.equity_curve.append((ts, equity))

    # ─────────────────────── 统计 ────────────────────────────────

    @property
    def total_realized_pnl(self) -> Decimal:
        return sum(
            (p.get("realized_pnl", Decimal("0")) for p in self._positions.values()),
            Decimal("0"),
        )

    @property
    def total_fees(self) -> Decimal:
        return sum(t.fee for t in self._trades)

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    def get_trades(self) -> list[TradeData]:
        return list(self._trades)

    def get_filled_orders(self) -> list[OrderData]:
        return list(self._filled_orders)

    def clear_pending_orders(self) -> None:
        """清空所有挂单（warmup 结束时使用）"""
        self._pending_orders.clear()

    def force_close_position(
        self, inst_id: str, close_price: Decimal, timestamp: datetime
    ) -> None:
        """
        回测结束时强制平仓，不走订单队列。
        直接以 close_price 结算。
        """
        pos_info = self._positions.get(inst_id)
        if not pos_info or pos_info["quantity"] == Decimal("0"):
            return

        qty = pos_info["quantity"]
        avg_price = pos_info["avg_price"]
        frozen_margin = pos_info.get("frozen_margin", Decimal("0"))

        fee = close_price * abs(qty) * self.taker_fee

        if qty > 0:
            side = OrderSide.SELL
            pnl_gross = (close_price - avg_price) * qty
            self._cash += close_price * qty  # 收回卖出所得
        else:
            side = OrderSide.BUY
            pnl_gross = (avg_price - close_price) * abs(qty)
            # 释放保证金 + 毛盈亏
            self._cash += frozen_margin + pnl_gross

        # fee 只扣一次
        self._cash -= fee

        # 净利润 = 毛利润 - 平仓手续费 - 等比例开仓手续费
        open_fee = avg_price * abs(qty) * self.taker_fee
        pnl_net = pnl_gross - fee - open_fee

        pos_info["realized_pnl"] = pos_info.get("realized_pnl", Decimal("0")) + pnl_net
        pos_info["quantity"] = Decimal("0")
        pos_info["avg_price"] = Decimal("0")
        pos_info["frozen_margin"] = Decimal("0")

        order_id = f"FORCE-{uuid.uuid4().hex[:8].upper()}"
        trade = TradeData(
            trade_id=f"T-CLOSE-{uuid.uuid4().hex[:8].upper()}",
            order_id=order_id,
            inst_id=inst_id,
            exchange=self.exchange,
            side=side,
            price=close_price,
            quantity=abs(qty),
            fee=fee,
            fee_ccy="USDT",
            timestamp=timestamp,
        )
        self._trades.append(trade)

        close_order = OrderData(
            order_id=order_id,
            client_order_id=order_id,
            inst_id=inst_id,
            exchange=self.exchange,
            side=side,
            order_type=OrderType.MARKET,
            price=close_price,
            quantity=abs(qty),
            filled_quantity=abs(qty),
            filled_price=close_price,
            status=OrderStatus.FILLED,
            fee=fee,
            pnl=pnl_net,
            create_time=timestamp,
            update_time=timestamp,
        )
        self._filled_orders.append(close_order)

        if self.on_order:
            self.on_order(close_order)
        if self.on_trade:
            self.on_trade(trade)