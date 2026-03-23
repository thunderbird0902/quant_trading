"""Gateway 抽象基类 - 定义所有市场必须实现的接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from core.enums import Exchange, MarginMode, MarketType, OrderType, PositionMode, PositionSide
from core.event_bus import EventBus
from core.models import (
    AlgoOrderData, BalanceData, BarData, FeeRate,
    FundingRateData, Instrument, MarkPriceData, OrderBook,
    OrderData, OrderRequest, PositionData, TickData,
    TradeData, TransferRequest, CurrencyBalance,
)


class BaseGateway(ABC):
    """
    所有交易所/券商 Gateway 的抽象基类。

    新增市场只需继承此类并实现所有 @abstractmethod 方法。
    可选功能（合约、策略委托等）有默认实现，不支持时抛出 NotImplementedError。

    子类必须设置类属性：
        exchange: Exchange  ← 标识所属交易所
    """

    exchange: Exchange  # 子类必须覆盖

    def __init__(self, event_bus: EventBus, config: dict):
        """
        Args:
            event_bus: 全局事件总线（Gateway 通过此对象推送行情/交易事件）
            config:    Gateway 专属配置（来自 YAML + 环境变量）
        """
        self.event_bus = event_bus
        self.config = config
        self._connected = False

    # ──────────────────── 连接管理 ──────────────────────────────

    @abstractmethod
    def connect(self) -> None:
        """连接交易所（建立会话、登录等）。"""

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接，释放资源。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """返回当前连接状态。"""

    # ──────────────────── 行情数据 ──────────────────────────────

    @abstractmethod
    def get_instruments(self, market_type: MarketType) -> list[Instrument]:
        """获取指定市场类型的所有可交易产品列表。"""

    @abstractmethod
    def get_ticker(self, inst_id: str) -> TickData:
        """获取单一产品最新行情快照。"""

    @abstractmethod
    def get_tickers(self, market_type: MarketType) -> list[TickData]:
        """获取指定市场类型的所有行情。"""

    @abstractmethod
    def get_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        """获取 K 线数据（最近 limit 根）。"""

    @abstractmethod
    def get_history_klines(
        self, inst_id: str, interval: str, start: datetime, end: datetime
    ) -> list[BarData]:
        """获取历史 K 线（按时间范围）。"""

    @abstractmethod
    def get_orderbook(self, inst_id: str, depth: int = 20) -> OrderBook:
        """获取订单簿深度数据。"""

    @abstractmethod
    def get_recent_trades(self, inst_id: str, limit: int = 100) -> list[TradeData]:
        """获取最近成交记录。"""

    # ──────────────────── WebSocket 订阅 ────────────────────────

    @abstractmethod
    def subscribe_ticker(self, inst_id: str) -> None:
        """订阅行情推送，收到数据时通过 event_bus 发布 TICK 事件。"""

    @abstractmethod
    def subscribe_orderbook(self, inst_id: str, depth: int = 5) -> None:
        """订阅深度推送，收到数据时通过 event_bus 发布 DEPTH 事件。"""

    @abstractmethod
    def subscribe_kline(self, inst_id: str, interval: str) -> None:
        """订阅 K 线推送，收到数据时通过 event_bus 发布 BAR 事件。"""

    # ──────────────────── 账户 ──────────────────────────────────

    @abstractmethod
    def get_balance(self) -> BalanceData:
        """获取账户余额。"""

    @abstractmethod
    def get_positions(self, inst_id: str | None = None) -> list[PositionData]:
        """
        获取持仓列表。

        Args:
            inst_id: 指定产品，None 则返回全部持仓
        """

    @abstractmethod
    def get_account_config(self) -> dict:
        """
        获取账户配置（账户模式、持仓模式等）。

        Returns:
            原始配置字典（各市场格式不同）
        """

    @abstractmethod
    def get_fee_rate(self, inst_id: str) -> FeeRate:
        """获取指定产品的手续费费率。"""

    # ──────────────────── 交易 ──────────────────────────────────

    @abstractmethod
    def send_order(self, request: OrderRequest) -> OrderData:
        """
        提交订单。

        Returns:
            已提交的 OrderData（status = SUBMITTED）
        """

    @abstractmethod
    def cancel_order(self, order_id: str, inst_id: str) -> bool:
        """撤销订单。Returns True 表示撤单请求已成功发出。"""

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        inst_id: str,
        new_price: Decimal | None,
        new_quantity: Decimal | None,
    ) -> bool:
        """修改订单价格/数量。"""

    @abstractmethod
    def batch_send_orders(self, requests: list[OrderRequest]) -> list[OrderData]:
        """批量下单（最多 20 个）。"""

    @abstractmethod
    def batch_cancel_orders(self, orders: list[tuple[str, str]]) -> list[bool]:
        """
        批量撤单。

        Args:
            orders: [(order_id, inst_id), ...]
        """

    @abstractmethod
    def get_order(self, order_id: str, inst_id: str) -> OrderData:
        """查询单笔订单详情（支持 order_id 或 client_order_id）。"""

    @abstractmethod
    def get_open_orders(self, inst_id: str | None = None) -> list[OrderData]:
        """获取未完成订单列表。"""

    @abstractmethod
    def get_order_history(self, market_type: MarketType, days: int = 7) -> list[OrderData]:
        """获取历史订单（近 days 天）。"""

    @abstractmethod
    def get_trade_history(self, market_type: MarketType, days: int = 7) -> list[TradeData]:
        """获取成交明细（近 days 天）。"""

    # ──────────────────── 合约专属（可选重写）───────────────────

    def set_leverage(
        self,
        inst_id: str,
        leverage: int,
        margin_mode: MarginMode,
        position_side: PositionSide | None = None,
    ) -> bool:
        """设置杠杆倍数（合约市场）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持杠杆设置")

    def get_leverage(self, inst_id: str, margin_mode: MarginMode) -> int:
        """查询当前杠杆倍数（合约市场）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持杠杆查询")

    def set_position_mode(self, mode: PositionMode) -> bool:
        """切换持仓模式（买卖/多空）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持持仓模式切换")

    def get_funding_rate(self, inst_id: str) -> FundingRateData:
        """获取资金费率（永续合约）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持资金费率查询")

    def get_mark_price(self, inst_id: str) -> MarkPriceData:
        """获取标记价格（合约市场）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持标记价格查询")

    # ──────────────────── 策略委托（可选重写）───────────────────

    def send_algo_order(self, request: OrderRequest) -> AlgoOrderData:
        """提交策略委托（止盈止损、计划委托等）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持策略委托")

    def cancel_algo_order(self, algo_id: str, inst_id: str) -> bool:
        """撤销策略委托。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持策略委托")

    def get_algo_orders(self, order_type: OrderType | None = None) -> list[AlgoOrderData]:
        """获取策略委托列表。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持策略委托")

    # ──────────────────── 资金（可选重写）───────────────────────

    def transfer(self, request: TransferRequest) -> bool:
        """资金划转（交易账户 ↔ 资金账户）。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持资金划转")

    def get_funding_balance(self) -> list[CurrencyBalance]:
        """查询资金账户余额。"""
        raise NotImplementedError(f"{self.exchange.value} 不支持资金账户查询")

    # ──────────────────── 工具方法 ───────────────────────────────

    def __repr__(self) -> str:
        status = "connected" if self.is_connected() else "disconnected"
        return f"{self.__class__.__name__}(exchange={self.exchange.value}, status={status})"
