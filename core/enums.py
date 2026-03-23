"""统一枚举定义 - 所有市场共用，各 gateway 负责映射到本交易所格式"""

from __future__ import annotations

from enum import Enum


class Exchange(Enum):
    """交易所/券商"""
    OKX = "OKX"
    BINANCE = "BINANCE"       # 预留
    IB = "IB"                 # Interactive Brokers
    CTP = "CTP"               # 国内期货


class MarketType(Enum):
    """市场类型"""
    SPOT = "SPOT"             # 现货
    SWAP = "SWAP"             # 永续合约
    FUTURES = "FUTURES"       # 交割合约/期货
    OPTION = "OPTION"         # 期权
    STOCK = "STOCK"           # 股票（预留给美股）
    ETF = "ETF"               # ETF（预留）


class OrderSide(Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(Enum):
    """持仓方向"""
    LONG = "LONG"
    SHORT = "SHORT"
    NET = "NET"               # 单向持仓模式下使用


class OrderType(Enum):
    """订单类型"""
    LIMIT = "LIMIT"                     # 限价单
    MARKET = "MARKET"                   # 市价单
    STOP_LIMIT = "STOP_LIMIT"           # 止损限价
    STOP_MARKET = "STOP_MARKET"         # 止损市价
    TAKE_PROFIT = "TAKE_PROFIT"         # 止盈
    TRAILING_STOP = "TRAILING_STOP"     # 移动止盈止损
    ICEBERG = "ICEBERG"                 # 冰山委托
    TWAP = "TWAP"                       # 时间加权平均价格委托
    TRIGGER = "TRIGGER"                 # 计划委托


class OrderStatus(Enum):
    """订单状态"""
    PENDING            = "PENDING"            # 待提交（本地队列）
    SUBMITTED          = "SUBMITTED"          # 已提交（交易所已接受，live）
    PARTIAL_FILLED     = "PARTIAL_FILLED"     # 部分成交（仍活跃）
    FILLED             = "FILLED"             # 完全成交
    CANCELLED          = "CANCELLED"          # 已全部撤单（零成交）
    PARTIALLY_CANCELLED = "PARTIALLY_CANCELLED"  # 部分成交后撤单剩余部分
    REJECTED           = "REJECTED"           # 被拒绝
    EXPIRED            = "EXPIRED"            # 已过期（GTD 超时等）


class MarginMode(Enum):
    """保证金模式"""
    CASH = "CASH"             # 现货（非杠杆）
    CROSS = "CROSS"           # 全仓保证金
    ISOLATED = "ISOLATED"     # 逐仓保证金


class PositionMode(Enum):
    """持仓模式"""
    NET = "NET"                   # 买卖模式（单向持仓）
    LONG_SHORT = "LONG_SHORT"     # 多空模式（双向持仓）


class Direction(Enum):
    """资金划转方向"""
    FUNDING_TO_TRADING = "FUNDING_TO_TRADING"   # 资金账户 → 交易账户
    TRADING_TO_FUNDING = "TRADING_TO_FUNDING"   # 交易账户 → 资金账户


class Interval(Enum):
    """K线周期（值与 OKX / 主流交易所字符串一致，可直接用于 API 调用）"""
    MINUTE_1  = "1m"
    MINUTE_2  = "2m"   # BarGenerator 支持，回测/实盘均使用
    MINUTE_3  = "3m"
    MINUTE_5  = "5m"
    MINUTE_10 = "10m"  # BarGenerator 支持，常见于传统量化策略
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1    = "1H"
    HOUR_2    = "2H"
    HOUR_4    = "4H"
    HOUR_6    = "6H"
    HOUR_12   = "12H"
    DAY_1     = "1D"
    WEEK_1    = "1W"
    MONTH_1   = "1M"


class TimeInForce(Enum):
    """
    订单有效期（Time-In-Force）。

    控制订单在未能立即完全成交时的处置方式，与 OrderType 正交。
    OKX、Binance 均原生支持，在 OrderRequest 的 extra 字段或
    gateway 转换层中传递。

    使用场景示例：
        IOC：高频策略追单，不愿意排队等候
        FOK：套利策略要求原子执行，部分成交无意义
        POST_ONLY：做市策略只挂单不吃单，保证只拿 maker 费率
    """
    GTC       = "GTC"        # Good Till Cancelled — 撤单前一直有效（默认）
    IOC       = "IOC"        # Immediate or Cancel — 立即成交，未成交部分立即撤销
    FOK       = "FOK"        # Fill or Kill         — 必须全部成交，否则全部撤销
    POST_ONLY = "POST_ONLY"  # 只做 Maker，若会吃单则拒绝（保证 maker 费率）


class AccountLevel(Enum):
    """账户模式（OKX 特有，通过 acctLv 区分）"""
    SPOT_ONLY = 1             # 简单交易模式（仅现货）
    SPOT_AND_FUTURES = 2      # 单币种保证金模式
    CROSS_MARGIN = 3          # 跨币种保证金模式
    PORTFOLIO_MARGIN = 4      # 组合保证金模式
