"""回测引擎核心（已修复版）

修复清单:
  [P1-4] on_init 期间不再暴露全量历史，改为 warmup_bars-1
  [P1-5] warmup 阶段 strategy.trading=False，防止产生订单；结束后清空残余挂单
  [P2-1] 配合 broker 的模拟时钟，每根 bar 设置 broker._current_timestamp
  [P2-3] 强制平仓前 strategy.trading=False，防止策略在回调中继续下单
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Type

from core.enums import Exchange, MarginMode, OrderSide, OrderType, PositionSide
from core.models import (
    BalanceData, BarData, OrderData, OrderRequest,
    PositionData, TickData, TradeData,
)
from backtest.broker import FeeSchedule, FeeTier, SimulatedBroker
from backtest.performance import PerformanceAnalyzer
from strategy_core.base_strategy import BaseStrategy

if TYPE_CHECKING:
    from data.data_feed import DataFeed

logger = logging.getLogger(__name__)


class _BacktestStrategyEngine:
    """
    模拟 StrategyEngine 接口，供 BaseStrategy 调用。

    将策略的 buy/sell/short/cover/cancel 请求路由到 SimulatedBroker。
    """

    def __init__(self, broker: SimulatedBroker, exchange: Exchange):
        self._broker = broker
        self._exchange = exchange
        self._historical_bars: dict[str, list[BarData]] = {}
        self._current_bar_index: int = -1

    def _set_current_index(self, index: int) -> None:
        """由 BacktestEngine 在每轮循环开始时调用，防止 get_klines 泄露未来"""
        self._current_bar_index = index

    def _buy(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        return self._submit(strategy, inst_id, OrderSide.BUY, price, qty, order_type)

    def _sell(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        return self._submit(strategy, inst_id, OrderSide.SELL, price, qty, order_type)

    def _short(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        return self._submit(strategy, inst_id, OrderSide.SELL, price, qty, order_type, PositionSide.SHORT)

    def _cover(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        return self._submit(strategy, inst_id, OrderSide.BUY, price, qty, order_type, PositionSide.SHORT)

    def _close_long(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        # FIX: [新增，与 strategy_engine 接口保持一致]
        # 回测中平多 = 卖出（模拟器不区分 posSide，方向正确即可）
        return self._submit(strategy, inst_id, OrderSide.SELL, price, qty, order_type, PositionSide.LONG)

    def _close_short(
        self, strategy: BaseStrategy, inst_id: str,
        price: Decimal | None, qty: Decimal, order_type: OrderType,
    ) -> OrderData | None:
        # FIX: [新增，与 strategy_engine 接口保持一致]
        # 回测中平空 = 买入
        return self._submit(strategy, inst_id, OrderSide.BUY, price, qty, order_type, PositionSide.SHORT)

    def _submit(
        self, strategy: BaseStrategy, inst_id: str,
        side: OrderSide, price: Decimal | None, qty: Decimal, order_type: OrderType,
        position_side: PositionSide | None = None,
    ) -> OrderData | None:
        request = OrderRequest(
            inst_id=inst_id,
            exchange=self._exchange,
            side=side,
            order_type=order_type,
            price=price if order_type != OrderType.MARKET else None,
            quantity=qty,
            margin_mode=MarginMode.CASH,
            position_side=position_side,
        )
        try:
            return self._broker.send_order(request)
        except Exception as e:
            logger.warning("下单失败: %s", e)
            return None

    def _cancel(
        self, strategy: BaseStrategy, order_id: str, inst_id: str,
    ) -> bool:
        return self._broker.cancel_order(order_id, inst_id)

    def _get_position(self, inst_id: str) -> PositionData | None:
        return self._broker.get_position(inst_id)

    def _get_balance(self) -> BalanceData | None:
        return self._broker.get_balance()

    def _get_klines(self, inst_id: str, interval: str, limit: int) -> list[BarData]:
        """只返回到当前 bar 为止的历史数据，绝不泄露未来"""
        all_bars = self._historical_bars.get(inst_id, [])
        available = all_bars[:self._current_bar_index + 1]
        return available[-limit:]

    def _inject_history(self, inst_id: str, bars: list[BarData]) -> None:
        """由 BacktestEngine 注入历史 K 线供 on_init 使用"""
        self._historical_bars[inst_id] = bars


class BacktestEngine:
    """
    K 线驱动的回测引擎。

    策略接口一致性：
        回测与实盘使用同一套 BaseStrategy 接口（on_bar / on_order / on_trade /
        buy / sell / short / cover 等）。回测时由 _BacktestStrategyEngine 模拟
        StrategyEngine 行为；实盘时由真实 StrategyEngine 执行。
        策略代码无需为回测和实盘写两套逻辑。

    数据加载：
        可直接传入 bars 列表，或通过 BacktestEngine.from_data_feed() 工厂方法
        让 DataFeed 自动处理本地 SQLite 缓存和 API 补拉。

    Args:
        strategy_class:    策略类（BaseStrategy 子类）
        strategy_config:   策略参数字典
        inst_id:           回测产品 ID
        bars:              历史 K 线列表，按时间升序
        initial_capital:   初始资金
        taker_fee:         市价单手续费率（默认 0.05%）
        maker_fee:         限价单手续费率（默认 0.02%）
        slippage_pct:      市价单滑点（相对偏移，默认 0）
        exchange:          交易所
        risk_free_rate:    年化无风险利率（用于夏普计算，默认 2%）
        warmup_bars:       预热 K 线数（前 N 根只更新指标，不撮合订单）
        generate_report:   是否在 run() 后自动生成 HTML 报告（默认 True）
        report_output_dir: HTML 报告输出目录（默认 ./output/）
    """

    def __init__(
        self,
        strategy_class: Type[BaseStrategy],
        strategy_config: dict,
        inst_id: str,
        bars: list[BarData],
        initial_capital: Decimal = Decimal("100000"),
        taker_fee: Decimal = Decimal("0.0005"),
        maker_fee: Decimal = Decimal("0.0002"),
        slippage_pct: Decimal = Decimal("0"),
        exchange: Exchange = Exchange.OKX,
        risk_free_rate: float = 0.02,
        warmup_bars: int = 0,
        generate_report: bool = True,
        report_output_dir: str = "./output/",
    ):
        self.inst_id = inst_id
        self.bars = bars
        self.initial_capital = initial_capital
        self.warmup_bars = warmup_bars
        self.exchange = exchange
        self._risk_free_rate = risk_free_rate
        self._generate_report = generate_report
        self._report_output_dir = report_output_dir

        # 推断 K 线周期（秒）
        if len(bars) >= 2:
            delta = bars[1].timestamp - bars[0].timestamp
            self._interval_seconds = int(delta.total_seconds())
        else:
            self._interval_seconds = 3600

        # Broker
        fee_schedule = FeeSchedule([
            FeeTier(Decimal("0"), taker_fee, maker_fee),
        ])
        self.broker = SimulatedBroker(
            initial_capital=initial_capital,
            fee_schedule=fee_schedule,
            slippage_pct=slippage_pct,
            exchange=exchange,
        )

        # 模拟 StrategyEngine
        self._fake_engine = _BacktestStrategyEngine(self.broker, exchange)

        # 注册 broker 回调 → 路由到策略
        self.broker.on_order = self._on_order
        self.broker.on_trade = self._on_trade

        # 实例化策略
        self.strategy: BaseStrategy = strategy_class(
            name="backtest",
            strategy_engine=self._fake_engine,
            inst_id=inst_id,
            config=strategy_config,
        )
        # [P1-5] 初始状态下 trading=False，等 warmup 结束后再开启
        self.strategy.trading = False
        self.strategy.active = True

        # 注入历史 K 线（供 on_init 的 get_klines 调用）
        self._fake_engine._inject_history(inst_id, bars)

        # 绩效分析器（run 后可访问）
        self.performance: PerformanceAnalyzer | None = None
        self._report_path: str | None = None

        # 统计
        self._bar_count = 0
        self._trade_count = 0

    # ─────────────────────── 工厂方法 ────────────────────────────

    @classmethod
    def from_data_feed(
        cls,
        strategy_class: Type[BaseStrategy],
        strategy_config: dict,
        inst_id: str,
        interval: str,
        start: datetime,
        end: datetime | None,
        data_feed: "DataFeed",
        **kwargs,
    ) -> "BacktestEngine":
        """
        通过 DataFeed 自动加载历史 K 线并构建回测引擎。

        data_feed.load_history() 优先读取本地 SQLite 缓存，仅在
        缺少数据时调用 Gateway API 补拉，避免每次回测都重复请求接口。

        Args:
            strategy_class:  策略类（BaseStrategy 子类）
            strategy_config: 策略参数字典
            inst_id:         产品 ID（如 "BTC-USDT-SWAP"）
            interval:        K 线周期（如 "1H"、"15m"）
            start:           开始时间（UTC）
            end:             结束时间（UTC），None 表示当前时间
            data_feed:       DataFeed 实例（已配置数据库，可选 Gateway）
            **kwargs:        其余参数直接传给 BacktestEngine.__init__()

        Returns:
            已加载数据的 BacktestEngine 实例

        Raises:
            ValueError: DataFeed 未能加载到任何数据时
        """
        bars = data_feed.load_history(inst_id, interval, start, end)
        if not bars:
            raise ValueError(
                f"DataFeed 未能加载数据: inst={inst_id} interval={interval} "
                f"[{start} ~ {end}]，请检查数据库或 Gateway 配置"
            )
        logger.info(
            "from_data_feed: 加载 %d 根 K 线 | inst=%s interval=%s [%s ~ %s]",
            len(bars), inst_id, interval,
            bars[0].timestamp.strftime("%Y-%m-%d"),
            bars[-1].timestamp.strftime("%Y-%m-%d"),
        )
        return cls(
            strategy_class=strategy_class,
            strategy_config=strategy_config,
            inst_id=inst_id,
            bars=bars,
            **kwargs,
        )

    # ─────────────────────── 主驱动循环 ──────────────────────────

    def run(self) -> dict:
        """
        执行回测，返回绩效指标字典。

        内部流程（每根 K 线）：
        1. broker.set_current_timestamp(bar.timestamp) → 设置模拟时钟
        2. broker.match_orders(bar) → 撮合上一轮 on_bar 产生的挂单
        3. strategy.on_bar(bar) → 策略生成信号 → broker 挂单（等下一根 bar 撮合）
        """
        logger.info(
            "开始回测 inst=%s bars=%d warmup=%d",
            self.inst_id, len(self.bars), self.warmup_bars,
        )

        # [P1-4] on_init 期间只暴露 warmup 期间的数据（而非全量历史）
        # FIX: 确保 on_init 期间 _current_bar_index = -1（空数据集），
        # 防止策略通过 self.am.close 或 self.get_klines(limit=大数) 访问全量历史。
        # 策略应在 on_start 或首根 on_bar 之后才加载完整历史。
        self._fake_engine._set_current_index(-1)

        try:
            self.strategy.on_init()
        except Exception as e:
            logger.warning("strategy.on_init 异常: %s", e)

        self.strategy.on_start()

        for i, bar in enumerate(self.bars):
            self._bar_count += 1
            in_warmup = i < self.warmup_bars

            # [P2-1] 设置模拟时钟
            self.broker.set_current_timestamp(bar.timestamp)

            # 更新当前 bar 索引（防止 get_klines 泄露未来）
            self._fake_engine._set_current_index(i)

            try:
                if in_warmup:
                    # 预热阶段：不撮合，但仍记录权益快照（含 intra-bar 极端情况）
                    self.broker._update_mark_prices(bar)
                    self.broker._snapshot_equity(bar.timestamp, bar)
                else:
                    # [P1-5] warmup 刚结束时，开启 trading 并清空残余挂单
                    if i == self.warmup_bars:
                        self.strategy.trading = True
                        self.broker.clear_pending_orders()

                    # 第1步：先撮合上一轮 on_bar 产生的挂单
                    self.broker.match_orders(bar)
            except Exception as e:
                logger.warning("撮合异常 bar=%s: %s", bar.timestamp, e)

            try:
                # 第2步：触发策略回调
                self.strategy.on_bar(bar)
            except Exception as e:
                logger.warning("on_bar 异常 bar=%s: %s", bar.timestamp, e)

        # [P2-3] 回测结束：禁止策略继续下单，然后强制平仓
        self.strategy.trading = False
        self._close_all_positions()

        self.strategy.on_stop()

        # 计算绩效
        self.performance = PerformanceAnalyzer(
            equity_curve=self.broker.equity_curve,
            trades=self.broker.get_trades(),
            initial_capital=self.initial_capital,
            risk_free_rate=self._risk_free_rate,
            interval_seconds=self._interval_seconds,
            filled_orders=self.broker.get_filled_orders(),
        )
        metrics = self.performance.compute()

        # 生成 HTML 报告
        if self._generate_report:
            try:
                from backtest.report import generate_report
                self._report_path = generate_report(
                    metrics=metrics,
                    equity_curve=self.broker.equity_curve,
                    trades=self.broker.get_trades(),
                    filled_orders=self.broker.get_filled_orders(),
                    bars=self.bars,
                    title=f"{self.strategy.__class__.__name__} | {self.inst_id}",
                    output_dir=self._report_output_dir,
                )
                logger.info("报告已生成: %s", self._report_path)
            except Exception as e:
                logger.warning("报告生成失败: %s", e, exc_info=True)
                self._report_path = None

        logger.info(
            "回测完成 总K线=%d 交易次数=%d 总收益率=%.2f%%",
            self._bar_count,
            self.broker.trade_count,
            metrics.get("total_return_pct", 0),
        )
        return metrics

    # ─────────────────────── 事件路由 ────────────────────────────

    def _on_order(self, order: OrderData) -> None:
        """Broker 订单事件 → 策略 on_order"""
        try:
            self.strategy.on_order(order)
        except Exception as e:
            logger.warning("strategy.on_order 异常: %s", e)

    def _on_trade(self, trade: TradeData) -> None:
        """Broker 成交事件 → 策略 on_trade + on_position（持仓同步）"""
        self._trade_count += 1
        try:
            self.strategy.on_trade(trade)
        except Exception as e:
            logger.warning("strategy.on_trade 异常: %s", e)

        try:
            position = self.broker.get_position(trade.inst_id)
            if position is None:
                position = PositionData(
                    inst_id=trade.inst_id,
                    exchange=Exchange.OKX,
                    position_side=PositionSide.NET,
                    quantity=Decimal("0"),
                    avg_price=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    unrealized_pnl_ratio=Decimal("0"),
                    realized_pnl=Decimal("0"),
                    leverage=1,
                    liquidation_price=Decimal("0"),
                    margin=Decimal("0"),
                    margin_ratio=Decimal("1"),
                    margin_mode=MarginMode.CASH,
                    mark_price=Decimal("0"),
                    update_time=datetime.now(),
                )
            self.strategy.on_position(position)
        except Exception as e:
            logger.warning("strategy.on_position 异常: %s", e)

    # ─────────────────────── 强制平仓 ────────────────────────────

    def _close_all_positions(self) -> None:
        """回测结束时以最后一根 K 线的 close 价强制平仓"""
        if not self.bars:
            return
        last_bar = self.bars[-1]
        last_price = last_bar.close

        positions = self.broker.get_all_positions()
        for pos in positions:
            if pos.quantity == Decimal("0"):
                continue
            self.broker.force_close_position(
                inst_id=pos.inst_id,
                close_price=last_price,
                timestamp=last_bar.timestamp,
            )
            logger.info(
                "强制平仓 %s qty=%s @ %s", pos.inst_id, pos.quantity, last_price
            )

        # 强制平仓后更新权益快照（[P2-2] 同一时间戳会覆盖）
        self.broker._update_mark_prices(last_bar)
        self.broker._snapshot_equity(last_bar.timestamp)

    # ─────────────────────── 便捷方法 ────────────────────────────

    def open_report(self) -> None:
        """用默认浏览器打开回测 HTML 报告"""
        if self._report_path and os.path.exists(self._report_path):
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(self._report_path)}")
        else:
            logger.warning("报告文件不存在或尚未生成，请先调用 run()")