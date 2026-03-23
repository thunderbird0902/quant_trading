"""统一数据模型 - 所有市场共用，gateway 负责将原始数据转换为这些模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .enums import (
    Exchange, MarketType, OrderSide, PositionSide,
    OrderType, OrderStatus, MarginMode, Direction,
)


@dataclass
class Instrument:
    """交易产品信息（统一）"""
    inst_id: str                          # 产品 ID（如 BTC-USDT, AAPL, rb2501）
    exchange: Exchange
    market_type: MarketType
    base_ccy: str                         # 基础货币/标的（BTC, AAPL, 螺纹钢）
    quote_ccy: str                        # 计价货币（USDT, USD, CNY）
    tick_size: Decimal                    # 价格最小变动
    lot_size: Decimal                     # 数量最小变动
    min_size: Decimal                     # 最小下单量
    max_limit_size: Decimal               # 限价单最大下单量
    max_market_size: Decimal              # 市价单最大下单量
    contract_value: Decimal               # 合约面值（现货为 0）
    contract_multiplier: Decimal          # 合约乘数
    contract_value_ccy: str               # 面值计价币种
    state: str                            # 产品状态（live, suspend 等）
    extra: dict = field(default_factory=dict)  # 各市场特有字段

    def __post_init__(self):
        """确保所有数值字段为 Decimal"""
        for attr in ("tick_size", "lot_size", "min_size",
                     "max_limit_size", "max_market_size",
                     "contract_value", "contract_multiplier"):
            v = getattr(self, attr)
            if not isinstance(v, Decimal):
                object.__setattr__(self, attr, Decimal(str(v)))
        # 校验关键尺寸为正值
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive: {self.tick_size}")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be positive: {self.lot_size}")
        if self.min_size <= 0:
            raise ValueError(f"min_size must be positive: {self.min_size}")


@dataclass
class TickData:
    """实时行情快照"""
    inst_id: str
    exchange: Exchange
    last_price: Decimal         # 最新成交价
    bid_price: Decimal          # 买一价
    ask_price: Decimal          # 卖一价
    bid_size: Decimal           # 买一量
    ask_size: Decimal           # 卖一量
    high_24h: Decimal           # 24h 最高价
    low_24h: Decimal            # 24h 最低价
    volume_24h: Decimal         # 24h 成交量（base）
    volume_ccy_24h: Decimal     # 24h 成交额（quote）
    timestamp: datetime
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.last_price < 0:
            raise ValueError(f"last_price cannot be negative: {self.last_price}")
        if self.bid_price < 0:
            raise ValueError(f"bid_price cannot be negative: {self.bid_price}")
        if self.ask_price < 0:
            raise ValueError(f"ask_price cannot be negative: {self.ask_price}")


@dataclass
class BarData:
    """K线数据"""
    inst_id: str
    exchange: Exchange
    interval: str               # 1m, 5m, 1H, 1D ...
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal             # 成交量（base）
    volume_ccy: Decimal         # 成交额（quote）
    timestamp: datetime         # 该 K 线开始时间

    def __post_init__(self):
        if any(v < 0 for v in (self.open, self.high, self.low, self.close)):
            raise ValueError("OHLC prices cannot be negative")
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low}")
        if self.volume < 0:
            raise ValueError(f"volume cannot be negative: {self.volume}")


@dataclass
class OrderBook:
    """订单簿（深度数据）"""
    inst_id: str
    exchange: Exchange
    asks: list[tuple[Decimal, Decimal]]   # [(价格, 数量), ...] 卖方，价格升序
    bids: list[tuple[Decimal, Decimal]]   # [(价格, 数量), ...] 买方，价格降序
    timestamp: datetime

    def __repr__(self) -> str:
        ask_top = self.asks[0] if self.asks else None
        bid_top = self.bids[0] if self.bids else None
        return (
            f"OrderBook(inst_id={self.inst_id!r}, exchange={self.exchange.value}, "
            f"ask_top={ask_top}, bid_top={bid_top}, "
            f"depth={len(self.asks)}/{len(self.bids)}, ts={self.timestamp})"
        )


@dataclass
class OrderRequest:
    """下单请求（统一，gateway 负责转换为交易所格式）"""
    inst_id: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    margin_mode: MarginMode
    price: Decimal | None = None              # 市价单为 None
    position_side: PositionSide | None = None  # 现货为 None
    client_order_id: str | None = None
    leverage: int | None = None               # 杠杆倍数
    take_profit_price: Decimal | None = None  # 止盈触发价
    stop_loss_price: Decimal | None = None    # 止损触发价
    # 策略委托专用
    trigger_price: Decimal | None = None      # 计划委托触发价
    callback_ratio: Decimal | None = None     # 移动止损回调比例
    sz_limit: Decimal | None = None           # 冰山/TWAP 单笔限量
    time_interval: str | None = None          # TWAP 时间间隔
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive: {self.quantity}")
        if self.price is not None and self.price <= 0:
            raise ValueError(f"price must be positive: {self.price}")

    def __repr__(self) -> str:
        return (
            f"OrderRequest(inst_id={self.inst_id!r}, side={self.side.value}, "
            f"type={self.order_type.value}, qty={self.quantity}, price={self.price})"
        )


@dataclass
class OrderData:
    """订单信息（统一）"""
    order_id: str                       # 交易所订单 ID
    client_order_id: str
    inst_id: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    price: Decimal                      # 委托价格
    quantity: Decimal                   # 委托数量
    filled_quantity: Decimal            # 已成交数量
    filled_price: Decimal               # 成交均价
    status: OrderStatus
    fee: Decimal                        # 手续费
    pnl: Decimal                        # 盈亏（合约平仓时）
    create_time: datetime
    update_time: datetime
    position_side: PositionSide | None = None
    extra: dict = field(default_factory=dict)

    @property
    def unfilled_quantity(self) -> Decimal:
        """未成交数量"""
        return self.quantity - self.filled_quantity

    @property
    def is_active(self) -> bool:
        """订单是否仍活跃（可撤销）"""
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL_FILLED,
        )

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive: {self.quantity}")
        if self.price < 0:
            raise ValueError(f"price cannot be negative: {self.price}")
        if self.filled_quantity < 0:
            raise ValueError(f"filled_quantity cannot be negative: {self.filled_quantity}")

    def __repr__(self) -> str:
        return (
            f"OrderData(order_id={self.order_id!r}, inst_id={self.inst_id!r}, "
            f"side={self.side.value}, status={self.status.value}, "
            f"qty={self.quantity}, filled={self.filled_quantity}, price={self.price})"
        )


@dataclass
class AlgoOrderData:
    """策略委托订单（止盈止损、计划委托、冰山、TWAP 等）"""
    algo_id: str
    inst_id: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    status: OrderStatus
    create_time: datetime
    position_side: PositionSide | None = None
    trigger_price: Decimal | None = None       # 计划委托触发价
    order_price: Decimal | None = None         # 委托价格
    tp_trigger_price: Decimal | None = None    # 止盈触发价
    tp_order_price: Decimal | None = None      # 止盈委托价（-1 = 市价）
    sl_trigger_price: Decimal | None = None    # 止损触发价
    sl_order_price: Decimal | None = None      # 止损委托价（-1 = 市价）
    callback_ratio: Decimal | None = None      # 移动止损回调比例
    active_price: Decimal | None = None        # 移动止损激活价格
    extra: dict = field(default_factory=dict)


@dataclass
class PositionData:
    """持仓信息（统一）"""
    inst_id: str
    exchange: Exchange
    position_side: PositionSide
    quantity: Decimal                    # 持仓数量（合约为张数）
    avg_price: Decimal                   # 开仓均价
    unrealized_pnl: Decimal              # 未实现盈亏
    unrealized_pnl_ratio: Decimal        # 未实现盈亏率
    realized_pnl: Decimal                # 已实现盈亏
    leverage: int                        # 杠杆倍数
    liquidation_price: Decimal           # 预估强平价
    margin: Decimal                      # 占用保证金
    margin_ratio: Decimal                # 保证金率
    margin_mode: MarginMode
    mark_price: Decimal                  # 标记价格
    update_time: datetime
    extra: dict = field(default_factory=dict)

    @property
    def notional_value(self) -> Decimal:
        """名义价值 = 持仓量 × 标记价格"""
        return self.quantity * self.mark_price


@dataclass
class CurrencyBalance:
    """单币种余额明细"""
    currency: str
    available: Decimal           # 可用
    frozen: Decimal              # 冻结（挂单占用）
    equity: Decimal              # 币种权益
    equity_usd: Decimal          # 美元计价权益


@dataclass
class BalanceData:
    """账户余额（统一）"""
    exchange: Exchange
    total_equity: Decimal        # 总权益（美元计）
    available_balance: Decimal   # 可用余额（美元计）
    frozen_balance: Decimal      # 冻结余额
    unrealized_pnl: Decimal      # 总未实现盈亏
    details: list[CurrencyBalance]
    update_time: datetime

    def __repr__(self) -> str:
        currencies = [d.currency for d in self.details]
        return (
            f"BalanceData(exchange={self.exchange.value}, "
            f"total_equity={self.total_equity}, "
            f"available={self.available_balance}, "
            f"currencies={currencies})"
        )


@dataclass
class FundingRateData:
    """资金费率（加密货币永续合约特有）"""
    inst_id: str
    exchange: Exchange
    funding_rate: Decimal          # 当前费率
    next_funding_rate: Decimal     # 预测下期费率
    funding_time: datetime         # 下次收取时间


@dataclass
class TradeData:
    """成交明细"""
    trade_id: str
    order_id: str
    inst_id: str
    exchange: Exchange
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_ccy: str
    timestamp: datetime
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.price <= 0:
            raise ValueError(f"trade price must be positive: {self.price}")
        if self.quantity <= 0:
            raise ValueError(f"trade quantity must be positive: {self.quantity}")


@dataclass
class FeeRate:
    """手续费费率"""
    exchange: Exchange
    inst_type: MarketType
    maker: Decimal               # Maker 费率
    taker: Decimal               # Taker 费率
    level: str                   # 用户等级（VIP0 等）


@dataclass
class TransferRequest:
    """资金划转请求"""
    currency: str
    amount: Decimal
    direction: Direction
    extra: dict = field(default_factory=dict)


@dataclass
class MarkPriceData:
    """标记价格"""
    inst_id: str
    exchange: Exchange
    mark_price: Decimal
    timestamp: datetime


@dataclass
class IndexTickerData:
    """指数行情"""
    inst_id: str
    exchange: Exchange
    index_price: Decimal
    high_24h: Decimal
    low_24h: Decimal
    open_24h: Decimal
    timestamp: datetime
