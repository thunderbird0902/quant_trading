"""示例策略：双均线策略（Double MA Strategy）

逻辑：
- 计算快均线（短周期）和慢均线（长周期）
- 快线上穿慢线 → 买入开多
- 快线下穿慢线 → 卖出平多

适用：趋势跟踪场景，币圈 1H/4H 效果较好
"""

from __future__ import annotations

import logging
from decimal import Decimal

from core.enums import OrderType
from core.models import BarData, OrderData, PositionData, TradeData
from strategy_core.array_manager import ArrayManager
from strategy_core.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class DoubleMaStrategy(BaseStrategy):
    """
    双均线策略示例。

    参数（config 字典）：
        fast_period:  快均线周期（默认 10）
        slow_period:  慢均线周期（默认 30）
        interval:     K 线周期（默认 "1H"）
        position_pct: 每次开仓使用可用资金的比例（默认 0.95，即 95%）
        lot_size:     下单精度，如 0.001（默认 0.001）

    使用示例：
        engine.add_gateway(okx_gw)
        strategy_engine = StrategyEngine(engine)
        strategy_engine.add_strategy(
            DoubleMaStrategy,
            name="double_ma_btcusdt",
            inst_id="BTC-USDT",
            exchange=Exchange.OKX,
            config={"fast_period": 10, "slow_period": 30, "position_pct": 0.95},
        )
        strategy_engine.start_strategy("double_ma_btcusdt")
    """

    def __init__(self, name, strategy_engine, inst_id, config=None):
        super().__init__(name, strategy_engine, inst_id, config)

        # 策略参数
        self.fast_period: int = int(config.get("fast_period", 10))
        self.slow_period: int = int(config.get("slow_period", 30))
        self.interval: str = config.get("interval", "1H")
        self.position_pct: float = float(config.get("position_pct", 0.95))
        self.lot_size: float = float(config.get("lot_size", 0.001))

        # ArrayManager：缓冲区大小取慢均线周期 + 50，确保指标有效
        self._am: ArrayManager = ArrayManager(size=self.slow_period + 50)

        # 状态
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._open_order_id: str | None = None

    # ─────────────────────── 生命周期 ────────────────────────────

    def on_init(self) -> None:
        """加载历史 K 线，预热均线指标。"""
        self.write_log(f"初始化双均线策略 fast={self.fast_period} slow={self.slow_period}")

        # 加载历史 K 线预热指标（至少 slow_period + 50 根）
        bars = self.get_klines(
            self.inst_id, self.interval,
            limit=self.slow_period + 50,
        )
        for bar in bars[:-1]:  # 最后一根可能未完成，跳过
            self._am.update_bar(bar)

        self.write_log(f"历史 K 线加载完成，共 {len(bars)} 根，inited={self._am.inited}")

    def on_start(self) -> None:
        """策略启动：订阅 K 线推送。"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止：撤销所有待成交订单。"""
        if self._open_order_id:
            self.cancel(self._open_order_id)
        self.write_log("策略停止")

    def on_bar(self, bar: BarData) -> None:
        """K 线完成：更新指标并生成信号。"""
        if bar.inst_id != self.inst_id or bar.interval != self.interval:
            return

        # 保存上一期均线（用于检测金叉/死叉）
        prev_fast = self._prev_fast
        prev_slow = self._prev_slow

        # 更新 ArrayManager
        self._am.update_bar(bar)

        if not self._am.inited:
            return  # 数据不足

        # 计算当前均线
        fast_ma = self._am.sma(self.fast_period)
        slow_ma = self._am.sma(self.slow_period)

        # 缓存本期值供下次对比
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        if prev_fast is None or prev_slow is None:
            return  # 首次初始化，无法判断交叉

        # 交叉信号检测
        cross_over  = prev_fast <= prev_slow and fast_ma > slow_ma
        cross_under = prev_fast >= prev_slow and fast_ma < slow_ma

        self.write_log(
            f"Bar close={bar.close}  fast_ma={fast_ma:.4f}  slow_ma={slow_ma:.4f}  "
            f"cross_over={cross_over}  cross_under={cross_under}"
        )

        if cross_over:
            self._on_golden_cross(bar)
        elif cross_under:
            self._on_death_cross(bar)

    def on_order(self, order: OrderData) -> None:
        """订单状态更新。"""
        from core.enums import OrderStatus
        if order.status == OrderStatus.FILLED:
            self.write_log(
                f"订单成交 orderId={order.order_id} side={order.side.value} "
                f"price={order.filled_price} qty={order.filled_quantity}"
            )
            self._open_order_id = None
        elif order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self._open_order_id = None

    def on_trade(self, trade: TradeData) -> None:
        """成交回报：更新 pos。"""
        from core.enums import OrderSide
        if trade.side == OrderSide.BUY:
            self.pos += trade.quantity
        else:
            self.pos -= trade.quantity
        self.write_log(f"成交回报 price={trade.price} qty={trade.quantity} pos={self.pos}")

    def on_position(self, position: PositionData) -> None:
        """持仓更新。"""
        self.pos = position.quantity
        self.write_log(f"持仓更新: {position.quantity}")

    # ─────────────────────── 信号处理 ────────────────────────────

    def _on_golden_cross(self, bar: BarData) -> None:
        """金叉：快线上穿慢线 → 若无多头持仓则买入。"""
        if self.pos > 0:
            self.write_log("金叉信号，但已有多头仓位，跳过")
            return
        if self._open_order_id:
            self.write_log("金叉信号，但有未成交订单，跳过")
            return

        qty = self.calc_quantity(
            price=float(bar.close),
            pct=self.position_pct,
            lot_size=self.lot_size,
        )
        if qty <= Decimal("0"):
            self.write_log("金叉信号，但可用资金不足，跳过")
            return

        self.write_log(f"金叉信号！买入 {self.inst_id} qty={qty} (pct={self.position_pct:.0%})")
        order = self.buy(
            price=bar.close,
            quantity=qty,
            order_type=OrderType.MARKET,
        )
        if order:
            self._open_order_id = order.order_id

    def _on_death_cross(self, bar: BarData) -> None:
        """死叉：快线下穿慢线 → 若有多头持仓则平仓。"""
        if self.pos <= 0:
            self.write_log("死叉信号，无多头仓位，跳过")
            return
        if self._open_order_id:
            self.write_log("死叉信号，但有未成交订单，跳过")
            return

        self.write_log(f"死叉信号！平多 {self.inst_id} qty={self.pos}")
        order = self.sell(
            price=bar.close,
            quantity=self.pos,
            order_type=OrderType.MARKET,
        )
        if order:
            self._open_order_id = order.order_id
