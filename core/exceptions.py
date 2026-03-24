"""统一异常体系 - 所有模块使用此处定义的异常"""

from __future__ import annotations


class QuantTradingError(Exception):
    """量化交易系统基础异常"""
    pass


# ───────────────────────── 网关异常 ─────────────────────────

class GatewayError(QuantTradingError):
    """网关基础异常"""
    pass


class GatewayConnectionError(GatewayError):
    """网关连接失败"""
    pass


class AuthenticationError(GatewayError):
    """认证失败（API Key 错误等）"""
    pass


class NetworkError(GatewayError):
    """网络错误（超时、断线等）"""
    pass


class APIError(GatewayError):
    """交易所 API 返回错误"""

    def __init__(self, message: str, code: str = "", raw: dict | None = None):
        super().__init__(message)
        self.code = code            # 交易所错误码
        self.raw = raw or {}        # 原始响应

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {super().__str__()}"
        return super().__str__()


class RateLimitError(GatewayError):
    """触发频率限制"""
    pass


class InstrumentNotFoundError(GatewayError):
    """找不到交易产品"""

    def __init__(self, inst_id: str):
        super().__init__(f"找不到交易产品: {inst_id}")
        self.inst_id = inst_id


class UnsupportedOperationError(GatewayError):
    """该市场不支持此操作"""
    pass


# ───────────────────────── 交易异常 ─────────────────────────

class TradingError(QuantTradingError):
    """交易基础异常"""
    pass


class OrderError(TradingError):
    """订单相关异常"""
    pass


class OrderNotFoundError(OrderError):
    """订单不存在"""

    def __init__(self, order_id: str):
        super().__init__(f"订单不存在: {order_id}")
        self.order_id = order_id


class InsufficientFundsError(TradingError):
    """资金不足"""
    pass


class InvalidPriceError(TradingError):
    """无效价格（精度错误、超出范围等）"""
    pass


class InvalidQuantityError(TradingError):
    """无效数量（精度错误、低于最小量等）"""
    pass


# ───────────────────────── 风控异常 ─────────────────────────

class RiskError(QuantTradingError):
    """风控基础异常"""
    pass


class PositionLimitError(RiskError):
    """超过仓位限制"""
    pass


class DailyLossLimitError(RiskError):
    """超过每日亏损限额"""
    pass


class SingleLossLimitError(RiskError):
    """超过单笔亏损限额"""
    pass


class ConsecutiveLossError(RiskError):
    """超过连续亏损次数限制"""
    pass


class OrderValidationError(RiskError):
    """订单参数校验失败"""
    pass


# ───────────────────────── 数据异常 ─────────────────────────

class DataError(QuantTradingError):
    """数据层基础异常"""
    pass


class DatabaseError(DataError):
    """数据库操作异常"""
    pass


class DataNotFoundError(DataError):
    """数据不存在"""
    pass


class DataValidationError(DataError):
    """
    数据内容/格式校验失败。

    适用场景：从交易所或行情源收到格式异常的数据（如字段缺失、
    类型错误、价格为负、OHLC 逻辑矛盾等）。
    """

    def __init__(self, message: str, field: str = "", raw: dict | None = None):
        super().__init__(message)
        self.field = field      # 出错的字段名（可选）
        self.raw = raw or {}    # 原始数据（便于排查）

    def __str__(self) -> str:
        if self.field:
            return f"[field={self.field}] {super().__str__()}"
        return super().__str__()


# ───────────────────────── 配置异常 ─────────────────────────

class ConfigError(QuantTradingError):
    """配置异常"""
    pass


class MissingConfigError(ConfigError):
    """缺少必要配置项"""

    def __init__(self, key: str):
        super().__init__(f"缺少必要配置项: {key}")
        self.key = key


# ───────────────────────── 策略异常 ─────────────────────────

class StrategyError(QuantTradingError):
    """
    策略层基础异常。

    适用场景：策略逻辑错误、策略状态异常（如重复启动）、
    策略参数无效等，与风控拦截（RiskError）和交易执行
    （TradingError）分开，便于分层排查。
    """
    pass


class StrategyNotFoundError(StrategyError):
    """指定策略不存在"""

    def __init__(self, strategy_id: str):
        super().__init__(f"策略不存在: {strategy_id}")
        self.strategy_id = strategy_id
