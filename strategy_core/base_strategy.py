"""策略抽象基类 - 所有策略继承此类"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_DOWN
from typing import TYPE_CHECKING

from core.enums import OrderType, PositionSide
from core.models import (
    BalanceData, BarData, OrderData, OrderRequest,
    PositionData, TickData, TradeData,
)

if TYPE_CHECKING:
    from strategy_core.strategy_engine import StrategyEngine

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    策略抽象基类。

    所有量化策略继承此类，实现 on_* 系列方法。
    策略通过 buy/sell/short/cover/cancel 等方法进行交易操作，
    这些方法由 StrategyEngine 注入实现。

    策略不直接操作 Gateway，通过 StrategyEngine → MainEngine 路由。
    """

    def __init__(
        self,
        name: str,
        strategy_engine: "StrategyEngine",
        inst_id: str,
        config: dict | None = None,
    ):
        """
        Args:
            name:            策略实例名（唯一标识）
            strategy_engine: 策略引擎（由引擎注入）
            inst_id:         主交易产品 ID
            config:          策略专属参数
        """
        self.name = name
        self._engine = strategy_engine
        self.inst_id = inst_id
        self.config = config or {}

        self.active = False         # 策略运行中
        self.trading = True         # 是否允许下单（风控可设为 False）
        self.pos: Decimal = Decimal("0")   # 当前仓位（正=多头，负=空头）
        self.logger = logging.getLogger(f"strategy.{name}")

    # ─────────────────────── 抽象事件回调（子类必须实现）──────────

    @abstractmethod
    def on_init(self) -> None:
        """
        策略初始化。
        通常在此处：加载历史数据、初始化指标、订阅行情。
        """

    @abstractmethod
    def on_bar(self, bar: BarData) -> None:
        """收到 K 线（每根 K 线完成时触发）。核心信号逻辑写在这里。"""

    # ─────────────────────── 可选回调（子类按需覆写）─────────────

    def on_start(self) -> None:
        """策略启动（on_init 完成后调用）。"""

    def on_stop(self) -> None:
        """策略停止（引擎关闭或手动停止时调用）。"""

    def on_tick(self, tick: TickData) -> None:
        """收到行情快照（每次 Tick 变动触发）。默认不处理。"""

    def on_order(self, order: OrderData) -> None:
        """订单状态变化（提交、成交、撤销等）。默认不处理。"""

    def on_trade(self, trade: TradeData) -> None:
        """成交回报（成交时触发）。默认不处理。"""

    def on_position(self, position: PositionData) -> None:
        """持仓更新。默认不处理。"""

    # ─────────────────────── 交易操作（由引擎注入）────────────────

    def buy(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """
        买入开仓（现货买入 / 合约多头开仓）。

        Args:
            price:      委托价格，None 表示市价单
            quantity:   委托数量
            order_type: 订单类型
            inst_id:    产品 ID，None 时使用 self.inst_id

        Returns:
            OrderData 或 None（风控拒绝时）
        """
        if not self.trading:
            self.logger.warning("buy 被阻止：trading=False")
            return None
        return self._engine._buy(self, inst_id or self.inst_id, price, quantity, order_type)

    def sell(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """卖出平仓（现货卖出 / 合约多头平仓）。"""
        if not self.trading:
            self.logger.warning("sell 被阻止：trading=False")
            return None
        return self._engine._sell(self, inst_id or self.inst_id, price, quantity, order_type)

    def short(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """做空（合约空头开仓）。"""
        if not self.trading:
            return None
        return self._engine._short(self, inst_id or self.inst_id, price, quantity, order_type)

    def cover(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """平空（合约空头平仓）。"""
        if not self.trading:
            return None
        return self._engine._cover(self, inst_id or self.inst_id, price, quantity, order_type)

    def close_long(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """
        FIX: [新增] 平多仓（合约双向持仓模式 / OKX hedge mode）。

        对应 OKX 参数：side=sell, posSide=long。
        使用场景：OKX 合约双向持仓模式下平多仓。
        单向持仓模式用 sell() 即可。
        """
        if not self.trading:
            self.logger.warning("close_long 被阻止：trading=False")
            return None
        return self._engine._close_long(
            self, inst_id or self.inst_id, price, quantity, order_type
        )

    def close_short(
        self,
        price: Decimal | None,
        quantity: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        inst_id: str | None = None,
    ) -> OrderData | None:
        """
        FIX: [新增] 平空仓（合约双向持仓模式 / OKX hedge mode）。

        对应 OKX 参数：side=buy, posSide=short。
        使用场景：OKX 合约双向持仓模式下平空仓，与 cover() 等价但语义更明确。
        """
        if not self.trading:
            self.logger.warning("close_short 被阻止：trading=False")
            return None
        return self._engine._close_short(
            self, inst_id or self.inst_id, price, quantity, order_type
        )

    def cancel(self, order_id: str, inst_id: str | None = None) -> bool:
        """撤销订单。"""
        return self._engine._cancel(self, order_id, inst_id or self.inst_id)

    # ─────────────────────── 数据查询（由引擎注入）────────────────

    def get_position(self, inst_id: str | None = None) -> PositionData | None:
        """查询持仓。"""
        return self._engine._get_position(inst_id or self.inst_id)

    def get_balance(self) -> BalanceData | None:
        """查询账户余额。"""
        return self._engine._get_balance()

    def get_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        """获取历史 K 线。"""
        return self._engine._get_klines(inst_id, interval, limit)

    # ─────────────────────── 内部工具 ────────────────────────────

    def calc_quantity(
        self,
        price: float,
        pct: float = 0.95,
        lot_size: float = 0.0,
        contract_value: float = 0.0,
    ) -> Decimal:
        """
        按可用资金比例计算下单数量。

        Args:
            price:          当前价格（用于折算数量）
            pct:            使用可用资金的比例，默认 0.95（留 5% 做手续费缓冲）
            lot_size:       最小下单精度（如 0.001 BTC），0 表示不对齐
            contract_value: 每张合约面值（合约品种填写，现货留 0）

        Returns:
            对齐精度后的 Decimal 数量，资金不足时返回 Decimal("0")
        """
        balance = self.get_balance()
        if balance is None or price <= 0:
            return Decimal("0")

        available = float(balance.available_balance)

        if contract_value > 0:
            # 合约：张数 = 资金 / (价格 × 每张合约面值)
            raw = available * pct / (price * contract_value)
        else:
            # 现货：数量 = 资金 / 价格
            raw = available * pct / price

        if lot_size > 0:
            qty = Decimal(str(raw)).quantize(
                Decimal(str(lot_size)), rounding=ROUND_DOWN
            )
        else:
            qty = Decimal(str(raw))

        return qty if qty > Decimal("0") else Decimal("0")

    def write_log(self, msg: str) -> None:
        """写入策略日志。"""
        self.logger.info("[%s] %s", self.name, msg)

    def __repr__(self) -> str:
        return f"Strategy(name={self.name!r}, inst_id={self.inst_id!r}, active={self.active})"
