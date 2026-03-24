"""RSI 均值回归策略

逻辑：
- RSI < 超卖线（默认 30）→ 买入开多
- RSI > 超买线（默认 70）→ 卖出平多
- 止损：入场后亏损超过 stop_loss_pct → 市价止损

适用：震荡行情，BTC/ETH 1H 或 4H
"""

from __future__ import annotations

from decimal import Decimal

from core.enums import OrderSide, OrderStatus, OrderType
from core.models import BarData, OrderData, PositionData, TradeData
from strategy_core.array_manager import ArrayManager
from strategy_core.base_strategy import BaseStrategy


class RsiStrategy(BaseStrategy):
    """
    RSI 超买超卖策略。

    参数（config 字典）：
        rsi_period:     RSI 计算周期（默认 14）
        oversold:       超卖线，RSI 低于此值买入（默认 30）
        overbought:     超买线，RSI 高于此值平仓（默认 70）
        position_pct:   开仓使用可用资金比例（默认 0.95，即 95%）
        lot_size:       下单精度（默认 0.001）
        stop_loss_pct:  止损比例，如 0.03 = 亏损 3% 止损（默认 0.03）
        interval:       K 线周期（默认 "1H"）
    """

    def __init__(self, name, strategy_engine, inst_id, config=None):
        super().__init__(name, strategy_engine, inst_id, config)

        # ── 策略参数 ──────────────────────────────────────────────
        self.rsi_period: int      = int(config.get("rsi_period", 14))
        self.oversold: float      = float(config.get("oversold", 30))
        self.overbought: float    = float(config.get("overbought", 70))
        self.position_pct: float  = float(config.get("position_pct", 0.95))
        self.lot_size: float      = float(config.get("lot_size", 0.001))
        self.stop_loss_pct: float = float(config.get("stop_loss_pct", 0.03))
        self.interval: str        = config.get("interval", "1H")

        # ── ArrayManager：缓冲区大小取 rsi_period × 3 + 10，确保指标稳定收敛
        self._am: ArrayManager = ArrayManager(size=self.rsi_period * 3 + 10)

        # ── 状态 ──────────────────────────────────────────────────
        self._entry_price: Decimal | None = None   # 入场价，用于止损
        self._open_order_id: str | None = None

    # ─────────────────────── 生命周期 ────────────────────────────

    def on_init(self) -> None:
        """加载历史 K 线预热 RSI 指标"""
        self.write_log(f"RSI 策略初始化 period={self.rsi_period}")
        bars = self.get_klines(
            self.inst_id, self.interval,
            limit=self.rsi_period * 3 + 10,
        )
        for bar in bars[:-1]:
            self._am.update_bar(bar)

        if self._am.inited:
            rsi_val = self._am.rsi(self.rsi_period)
            self.write_log(f"预热完成，当前 RSI={rsi_val:.2f}")
        else:
            self.write_log("数据不足，等待更多 K 线")

    def on_stop(self) -> None:
        """停止时撤销所有挂单"""
        if self._open_order_id:
            self.cancel(self._open_order_id)

    # ─────────────────────── 核心逻辑 ────────────────────────────

    def on_bar(self, bar: BarData) -> None:
        """每根 K 线完成时触发"""
        if bar.inst_id != self.inst_id or bar.interval != self.interval:
            return

        # 更新 ArrayManager
        self._am.update_bar(bar)

        if not self._am.inited:
            return  # 数据不足，等待

        rsi_val = self._am.rsi(self.rsi_period)
        self.write_log(f"close={bar.close}  RSI={rsi_val:.2f}  pos={self.pos}")

        # 有挂单时不重复下单
        if self._open_order_id:
            return

        # ── 止损检查（优先于信号）────────────────────────────────
        if self.pos > 0 and self._entry_price:
            loss_pct = float(bar.close - self._entry_price) / float(self._entry_price)
            if loss_pct < -self.stop_loss_pct:
                self.write_log(f"触发止损！多头亏损 {loss_pct:.2%}，市价平仓")
                order = self.sell(price=None, quantity=self.pos,
                                  order_type=OrderType.MARKET)
                if order:
                    self._open_order_id = order.order_id
                return
        elif self.pos < 0 and self._entry_price:
            loss_pct = float(self._entry_price - bar.close) / float(self._entry_price)
            if loss_pct < -self.stop_loss_pct:
                self.write_log(f"触发止损！空头亏损 {loss_pct:.2%}，市价平仓")
                order = self.cover(price=None, quantity=abs(self.pos),
                                   order_type=OrderType.MARKET)
                if order:
                    self._open_order_id = order.order_id
                return

        # ── 信号判断 ──────────────────────────────────────────────
        # 注意：仅在无持仓（pos == 0）时开多，空头持仓时 RSI 超卖是强势信号应持有空头
        if rsi_val < self.oversold and self.pos == Decimal("0"):
            qty = self.calc_quantity(
                price=float(bar.close),
                pct=self.position_pct,
                lot_size=self.lot_size,
            )
            if qty <= Decimal("0"):
                self.write_log("RSI 超卖，但可用资金不足，跳过")
                return
            self.write_log(f"RSI 超卖（{rsi_val:.1f} < {self.oversold}），买入 qty={qty}")
            order = self.buy(price=None, quantity=qty,
                             order_type=OrderType.MARKET)
            if order:
                self._open_order_id = order.order_id

        elif rsi_val > self.overbought and self.pos > 0:
            self.write_log(f"RSI 超买（{rsi_val:.1f} > {self.overbought}），平仓 qty={self.pos}")
            order = self.sell(price=None, quantity=self.pos,
                              order_type=OrderType.MARKET)
            if order:
                self._open_order_id = order.order_id

    def on_order(self, order: OrderData) -> None:
        """订单状态变化"""
        if order.status == OrderStatus.FILLED:
            self.write_log(
                f"成交 side={order.side.value} price={order.filled_price} qty={order.filled_quantity}"
            )
            self._open_order_id = None
        elif order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self._open_order_id = None

    def on_trade(self, trade: TradeData) -> None:
        """成交回报：仅记录日志和更新入场价，pos 以 on_position 为准"""
        self.write_log(
            f"成交回报 price={trade.price} qty={trade.quantity} "
            f"side={trade.side.value} order_id={trade.order_id}"
        )
        if trade.side == OrderSide.BUY:
            if self.pos > 0 and self._entry_price is not None:
                total_qty = self.pos + trade.quantity
                avg_price = (self._entry_price * self.pos + trade.price * trade.quantity) / total_qty
                self._entry_price = avg_price
                self.write_log(f"加仓成功，更新加权平均入场价 {avg_price}")
            else:
                self._entry_price = trade.price
        else:
            if self._entry_price is not None:
                self.write_log(f"平仓成交，入场价 {self._entry_price} 已清除")
            self._entry_price = None

    def on_position(self, position: PositionData) -> None:
        """持仓更新：交易所权威状态"""
        self.pos = position.quantity
        self.write_log(f"持仓更新: pos={self.pos}")
