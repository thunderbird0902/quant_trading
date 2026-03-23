"""OKX 数据转换工具 - OKX 原始格式 ↔ 统一数据模型"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from decimal import Decimal
from threading import Lock

from core.enums import (
    Exchange, MarginMode, MarketType, OrderSide, OrderStatus,
    OrderType, PositionSide,
)
from core.exceptions import APIError, AuthenticationError, NetworkError, RateLimitError
from core.models import (
    AlgoOrderData, BalanceData, BarData, CurrencyBalance,
    FeeRate, FundingRateData, IndexTickerData, Instrument,
    MarkPriceData, OrderBook, OrderData, PositionData, TickData,
    TradeData,
)
from utils.helpers import safe_decimal, ts_to_datetime

logger = logging.getLogger(__name__)


# ─────────────────────── 限频工具 ────────────────────────────

class _Throttle:
    """
    OKX REST API 端点级限频器（滑动窗口，线程安全）。

    超限时阻塞等待，直到窗口滑出足够空间，保证请求最终成功。
    各端点独立实例：
      - 交易类（下单/撤单）：max_calls=20, period=2.0
      - 行情类（行情/K线）：max_calls=20, period=2.0
      - 账户类（余额/持仓）：max_calls=10, period=2.0
      - 历史数据类：        max_calls=10, period=2.0
    """

    def __init__(self, max_calls: int, period: float):
        self._max = max_calls
        self._period = period
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        """请求一次 API 配额，超限时阻塞等待（释放锁后睡眠，避免持锁阻塞）。"""
        while True:
            wait_time: float | None = None
            with self._lock:
                now = time.monotonic()
                while self._calls and self._calls[0] <= now - self._period:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                oldest = self._calls[0]
                wait_time = self._period - (now - oldest) + 0.01
                logger.debug(
                    "OKX 限频等待 %.2fs (%d/%d reqs/%.0fs)",
                    wait_time, len(self._calls), self._max, self._period,
                )
            time.sleep(wait_time)

# ─────────────────────── 枚举映射 ────────────────────────────

# OKX instType → MarketType
_OKX_INST_TYPE_MAP: dict[str, MarketType] = {
    "SPOT": MarketType.SPOT,
    "SWAP": MarketType.SWAP,
    "FUTURES": MarketType.FUTURES,
    "OPTION": MarketType.OPTION,
    "MARGIN": MarketType.SPOT,   # 杠杆现货归为 SPOT
}

# MarketType → OKX instType
_MARKET_TYPE_TO_OKX: dict[MarketType, str] = {
    MarketType.SPOT: "SPOT",
    MarketType.SWAP: "SWAP",
    MarketType.FUTURES: "FUTURES",
    MarketType.OPTION: "OPTION",
}

# OKX side → OrderSide
_OKX_SIDE_MAP: dict[str, OrderSide] = {
    "buy": OrderSide.BUY,
    "sell": OrderSide.SELL,
}

# OKX posSide → PositionSide
_OKX_POS_SIDE_MAP: dict[str, PositionSide] = {
    "long": PositionSide.LONG,
    "short": PositionSide.SHORT,
    "net": PositionSide.NET,
}

# OKX ordType → OrderType
_OKX_ORD_TYPE_MAP: dict[str, OrderType] = {
    "limit": OrderType.LIMIT,
    "market": OrderType.MARKET,
    "post_only": OrderType.LIMIT,
    "fok": OrderType.LIMIT,
    "ioc": OrderType.LIMIT,
    "optimal_limit_ioc": OrderType.LIMIT,
    "conditional": OrderType.STOP_LIMIT,
    "trigger": OrderType.TRIGGER,
    "move_order_stop": OrderType.TRAILING_STOP,
    "iceberg": OrderType.ICEBERG,
    "twap": OrderType.TWAP,
    "chase_limit": OrderType.LIMIT,
}

# OKX state → OrderStatus
_OKX_STATE_MAP: dict[str, OrderStatus] = {
    "live": OrderStatus.SUBMITTED,
    "partially_filled": OrderStatus.PARTIAL_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "mmp_canceled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}

# OKX algo state → OrderStatus
_OKX_ALGO_STATE_MAP: dict[str, OrderStatus] = {
    "live": OrderStatus.SUBMITTED,
    "pause": OrderStatus.SUBMITTED,
    "partially_effective": OrderStatus.PARTIAL_FILLED,
    "effective": OrderStatus.SUBMITTED,
    "canceled": OrderStatus.CANCELLED,
    "order_failed": OrderStatus.REJECTED,
    "partially_filled": OrderStatus.PARTIAL_FILLED,
    "filled": OrderStatus.FILLED,
    "failed": OrderStatus.REJECTED,
}

# OKX mgnMode → MarginMode
_OKX_MGN_MODE_MAP: dict[str, MarginMode] = {
    "cross": MarginMode.CROSS,
    "isolated": MarginMode.ISOLATED,
    "cash": MarginMode.CASH,
}

# MarginMode → OKX tdMode
_MARGIN_MODE_TO_OKX: dict[MarginMode, str] = {
    MarginMode.CROSS: "cross",
    MarginMode.ISOLATED: "isolated",
    MarginMode.CASH: "cash",
}

# PositionSide → OKX posSide
_POS_SIDE_TO_OKX: dict[PositionSide, str] = {
    PositionSide.LONG: "long",
    PositionSide.SHORT: "short",
    PositionSide.NET: "net",
}

# OrderType → OKX ordType
_ORD_TYPE_TO_OKX: dict[OrderType, str] = {
    OrderType.LIMIT: "limit",
    OrderType.MARKET: "market",
    OrderType.STOP_LIMIT: "conditional",
    OrderType.STOP_MARKET: "conditional",
    OrderType.TAKE_PROFIT: "conditional",
    OrderType.TRAILING_STOP: "move_order_stop",
    OrderType.ICEBERG: "iceberg",
    OrderType.TWAP: "twap",
    OrderType.TRIGGER: "trigger",
}


# ─────────────────────── 通用转换 ────────────────────────────

def market_type_to_okx(market_type: MarketType) -> str:
    """统一 MarketType → OKX instType 字符串。"""
    return _MARKET_TYPE_TO_OKX.get(market_type, "SPOT")


def okx_to_market_type(inst_type: str) -> MarketType:
    """OKX instType → 统一 MarketType。"""
    return _OKX_INST_TYPE_MAP.get(inst_type.upper(), MarketType.SPOT)


def inst_type_from_id(inst_id: str) -> str:
    """从 instId 推断 OKX instType。

    规则：
    - 末尾为 "SWAP"               → "SWAP"   (如 BTC-USDT-SWAP)
    - 末尾为 8 位纯数字日期        → "FUTURES" (如 BTC-USDT-240329)
    - 其他                         → "SPOT"   (如 BTC-USDT)
    """
    parts = inst_id.upper().split("-")
    if not parts:
        return "SPOT"
    last = parts[-1]
    if last == "SWAP":
        return "SWAP"
    if last.isdigit() and len(last) == 6:
        return "FUTURES"
    return "SPOT"


def margin_mode_to_okx(mode: MarginMode) -> str:
    return _MARGIN_MODE_TO_OKX[mode]


def pos_side_to_okx(side: PositionSide | None) -> str:
    if side is None:
        return ""
    return _POS_SIDE_TO_OKX[side]


def order_type_to_okx(order_type: OrderType) -> str:
    return _ORD_TYPE_TO_OKX.get(order_type, "limit")


# ─────────────────────── 原始数据 → 统一模型 ─────────────────

def parse_instrument(raw: dict) -> Instrument:
    """OKX GET /api/v5/public/instruments 单条记录 → Instrument。"""
    return Instrument(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        market_type=okx_to_market_type(raw.get("instType", "SPOT")),
        base_ccy=raw.get("baseCcy", raw.get("uly", "")),
        quote_ccy=raw.get("quoteCcy", "USDT"),
        tick_size=safe_decimal(raw.get("tickSz", "0")),
        lot_size=safe_decimal(raw.get("lotSz", "0")),
        min_size=safe_decimal(raw.get("minSz", "0")),
        max_limit_size=safe_decimal(raw.get("maxLmtSz", "0")),
        max_market_size=safe_decimal(raw.get("maxMktSz", "0")),
        contract_value=safe_decimal(raw.get("ctVal", "0")),
        contract_multiplier=safe_decimal(raw.get("ctMult", "1")),
        contract_value_ccy=raw.get("ctValCcy", ""),
        state=raw.get("state", "live"),
        extra={
            "lever": raw.get("lever", ""),
            "settleCcy": raw.get("settleCcy", ""),
            "ctType": raw.get("ctType", ""),
            "alias": raw.get("alias", ""),
            "optType": raw.get("optType", ""),
            "expTime": raw.get("expTime", ""),
        },
    )


def parse_ticker(raw: dict) -> TickData:
    """OKX GET /api/v5/market/ticker 单条记录 → TickData。"""
    return TickData(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        last_price=safe_decimal(raw.get("last")),
        bid_price=safe_decimal(raw.get("bidPx")),
        ask_price=safe_decimal(raw.get("askPx")),
        bid_size=safe_decimal(raw.get("bidSz")),
        ask_size=safe_decimal(raw.get("askSz")),
        high_24h=safe_decimal(raw.get("high24h")),
        low_24h=safe_decimal(raw.get("low24h")),
        volume_24h=safe_decimal(raw.get("vol24h")),
        volume_ccy_24h=safe_decimal(raw.get("volCcy24h")),
        timestamp=ts_to_datetime(raw.get("ts", 0)),
        extra={
            "open24h": raw.get("open24h", ""),
            "sodUtc0": raw.get("sodUtc0", ""),
            "sodUtc8": raw.get("sodUtc8", ""),
        },
    )


def parse_kline(raw: list, inst_id: str, interval: str) -> BarData:
    """
    OKX K 线数组 → BarData。

    OKX 格式：[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    """
    return BarData(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        interval=interval,
        timestamp=ts_to_datetime(raw[0]),
        open=safe_decimal(raw[1]),
        high=safe_decimal(raw[2]),
        low=safe_decimal(raw[3]),
        close=safe_decimal(raw[4]),
        volume=safe_decimal(raw[5]),
        volume_ccy=safe_decimal(raw[6] if len(raw) > 6 else "0"),
    )


def parse_orderbook(raw: dict, inst_id: str) -> OrderBook:
    """OKX GET /api/v5/market/books → OrderBook。"""
    def _to_tuples(rows: list) -> list[tuple[Decimal, Decimal]]:
        return [(safe_decimal(r[0]), safe_decimal(r[1])) for r in rows]

    return OrderBook(
        inst_id=inst_id,
        exchange=Exchange.OKX,
        asks=_to_tuples(raw.get("asks", [])),
        bids=_to_tuples(raw.get("bids", [])),
        timestamp=ts_to_datetime(raw.get("ts", 0)),
    )


def parse_order(raw: dict) -> OrderData:
    """OKX 订单记录 → OrderData（适用于 REST 和 WebSocket 推送）。"""
    return OrderData(
        order_id=raw.get("ordId", ""),
        client_order_id=raw.get("clOrdId", ""),
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        side=_OKX_SIDE_MAP.get(raw.get("side", "buy"), OrderSide.BUY),
        position_side=_OKX_POS_SIDE_MAP.get(raw.get("posSide", "net"), PositionSide.NET),
        order_type=_OKX_ORD_TYPE_MAP.get(raw.get("ordType", "limit"), OrderType.LIMIT),
        price=safe_decimal(raw.get("px")),
        quantity=safe_decimal(raw.get("sz")),
        filled_quantity=safe_decimal(raw.get("fillSz")),
        filled_price=safe_decimal(raw.get("avgPx")),
        status=_OKX_STATE_MAP.get(raw.get("state", "live"), OrderStatus.SUBMITTED),
        fee=safe_decimal(raw.get("fee")),
        pnl=safe_decimal(raw.get("pnl")),
        create_time=ts_to_datetime(raw.get("cTime", 0)),
        update_time=ts_to_datetime(raw.get("uTime", 0)),
        extra={
            "tdMode": raw.get("tdMode", ""),
            "feeCcy": raw.get("feeCcy", ""),
            "category": raw.get("category", ""),
        },
    )


def parse_algo_order(raw: dict) -> AlgoOrderData:
    """OKX 策略委托记录 → AlgoOrderData。"""
    return AlgoOrderData(
        algo_id=raw.get("algoId", ""),
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        side=_OKX_SIDE_MAP.get(raw.get("side", "buy"), OrderSide.BUY),
        position_side=_OKX_POS_SIDE_MAP.get(raw.get("posSide", "net"), PositionSide.NET),
        order_type=_OKX_ORD_TYPE_MAP.get(raw.get("ordType", "conditional"), OrderType.STOP_LIMIT),
        quantity=safe_decimal(raw.get("sz")),
        status=_OKX_ALGO_STATE_MAP.get(raw.get("state", "live"), OrderStatus.SUBMITTED),
        create_time=ts_to_datetime(raw.get("cTime", 0)),
        trigger_price=safe_decimal(raw.get("triggerPx")) if raw.get("triggerPx") else None,
        order_price=safe_decimal(raw.get("orderPx")) if raw.get("orderPx") else None,
        tp_trigger_price=safe_decimal(raw.get("tpTriggerPx")) if raw.get("tpTriggerPx") else None,
        tp_order_price=safe_decimal(raw.get("tpOrdPx")) if raw.get("tpOrdPx") else None,
        sl_trigger_price=safe_decimal(raw.get("slTriggerPx")) if raw.get("slTriggerPx") else None,
        sl_order_price=safe_decimal(raw.get("slOrdPx")) if raw.get("slOrdPx") else None,
        callback_ratio=safe_decimal(raw.get("callbackRatio")) if raw.get("callbackRatio") else None,
        active_price=safe_decimal(raw.get("activePx")) if raw.get("activePx") else None,
        extra={"algoClOrdId": raw.get("algoClOrdId", "")},
    )


def parse_position(raw: dict) -> PositionData:
    """OKX 持仓记录 → PositionData。"""
    margin_mode = _OKX_MGN_MODE_MAP.get(raw.get("mgnMode", "cross"), MarginMode.CROSS)
    pos_side_str = raw.get("posSide", "net")
    pos_side = _OKX_POS_SIDE_MAP.get(pos_side_str, PositionSide.NET)

    return PositionData(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        position_side=pos_side,
        quantity=safe_decimal(raw.get("pos")),
        avg_price=safe_decimal(raw.get("avgPx")),
        unrealized_pnl=safe_decimal(raw.get("upl")),
        unrealized_pnl_ratio=safe_decimal(raw.get("uplRatio")),
        realized_pnl=safe_decimal(raw.get("realizedPnl")),
        leverage=int(safe_decimal(raw.get("lever", "1"))),
        liquidation_price=safe_decimal(raw.get("liqPx")),
        margin=safe_decimal(raw.get("margin")),
        margin_ratio=safe_decimal(raw.get("mgnRatio")),
        margin_mode=margin_mode,
        mark_price=safe_decimal(raw.get("markPx")),
        update_time=ts_to_datetime(raw.get("uTime", 0)),
        extra={
            "instType": raw.get("instType", ""),
            "ccy": raw.get("ccy", ""),
            "notionalUsd": raw.get("notionalUsd", ""),
        },
    )


def parse_balance(raw: dict) -> BalanceData:
    """OKX GET /api/v5/account/balance → BalanceData。"""
    details: list[CurrencyBalance] = []
    for detail in raw.get("details", []):
        details.append(CurrencyBalance(
            currency=detail.get("ccy", ""),
            available=safe_decimal(detail.get("availBal")),
            frozen=safe_decimal(detail.get("frozenBal")),
            equity=safe_decimal(detail.get("eq")),
            equity_usd=safe_decimal(detail.get("eqUsd")),
        ))

    total_eq = safe_decimal(raw.get("totalEq"))
    imr = safe_decimal(raw.get("imr"))         # 占用保证金
    avail_eq = safe_decimal(raw.get("adjEq") or raw.get("totalEq"))

    return BalanceData(
        exchange=Exchange.OKX,
        total_equity=total_eq,
        available_balance=avail_eq,
        frozen_balance=imr,
        unrealized_pnl=safe_decimal(raw.get("upl")),
        details=details,
        update_time=ts_to_datetime(raw.get("uTime", 0)),
    )


def parse_trade(raw: dict) -> TradeData:
    """OKX 成交记录 → TradeData。"""
    return TradeData(
        trade_id=raw.get("tradeId", raw.get("billId", "")),
        order_id=raw.get("ordId", ""),
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        side=_OKX_SIDE_MAP.get(raw.get("side", "buy"), OrderSide.BUY),
        price=safe_decimal(raw.get("fillPx", raw.get("px"))),
        quantity=safe_decimal(raw.get("fillSz", raw.get("sz"))),
        fee=safe_decimal(raw.get("fee")),
        fee_ccy=raw.get("feeCcy", ""),
        timestamp=ts_to_datetime(raw.get("ts", raw.get("fillTime", 0))),
        extra={"execType": raw.get("execType", "")},
    )


def parse_funding_rate(raw: dict) -> FundingRateData:
    """OKX 资金费率记录 → FundingRateData。"""
    return FundingRateData(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        funding_rate=safe_decimal(raw.get("fundingRate")),
        next_funding_rate=safe_decimal(raw.get("nextFundingRate")),
        funding_time=ts_to_datetime(raw.get("fundingTime", 0)),
    )


def parse_mark_price(raw: dict) -> MarkPriceData:
    """OKX 标记价格记录 → MarkPriceData。"""
    return MarkPriceData(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        mark_price=safe_decimal(raw.get("markPx")),
        timestamp=ts_to_datetime(raw.get("ts", 0)),
    )


def parse_fee_rate(raw: dict, market_type: MarketType) -> FeeRate:
    """OKX 手续费记录 → FeeRate。"""
    return FeeRate(
        exchange=Exchange.OKX,
        inst_type=market_type,
        maker=safe_decimal(raw.get("maker")),
        taker=safe_decimal(raw.get("taker")),
        level=raw.get("level", "Lv1"),
    )


def parse_index_ticker(raw: dict) -> IndexTickerData:
    """OKX 指数行情 → IndexTickerData。"""
    return IndexTickerData(
        inst_id=raw["instId"],
        exchange=Exchange.OKX,
        index_price=safe_decimal(raw.get("idxPx")),
        high_24h=safe_decimal(raw.get("high24h")),
        low_24h=safe_decimal(raw.get("low24h")),
        open_24h=safe_decimal(raw.get("open24h")),
        timestamp=ts_to_datetime(raw.get("ts", 0)),
    )


# ─────────────────────── API 响应检查 ────────────────────────

# OKX 错误码 → 异常类型映射
# 参考：https://www.okx.com/docs-v5/zh/#error-code
_OKX_RATE_LIMIT_CODES = frozenset({
    "50011",  # Too many requests
    "50012",  # Request frequency too high
    "50013",  # System busy, request frequency too high
    "50014",  # Parameter {param} cannot be empty
})
_OKX_AUTH_CODES = frozenset({
    "50100", "50101", "50102", "50103", "50104",
    "50105", "50106", "50107", "50108", "50109",
    "50110", "50111", "50112", "50113", "50114",
    "50116", "50117", "50119",
})
_OKX_NETWORK_CODES = frozenset({
    "50001",  # Incorrect response body format
    "50002",  # System busy, please try again later
    "50009",  # Service temporarily unavailable
})


def check_response(resp: dict, operation: str = "") -> list:
    """
    检查 OKX REST API 响应，code != "0" 时抛出具体异常。

    错误码映射：
    - 限频码（50011~50014）→ RateLimitError（可被 retry 重试）
    - 鉴权码（501xx）       → AuthenticationError（不重试，需检查 API Key）
    - 网络/服务码（50001/2/9）→ NetworkError（可重试）
    - 其他                  → APIError（携带 code 和原始响应）

    Args:
        resp:      API 返回的完整 JSON 字典
        operation: 操作描述，用于错误日志

    Returns:
        resp["data"] 列表

    Raises:
        RateLimitError, AuthenticationError, NetworkError, APIError
    """
    code = str(resp.get("code", ""))
    if code != "0":
        msg = resp.get("msg", "未知错误")
        detail = f"{operation}: [{code}] {msg}"

        if code in _OKX_RATE_LIMIT_CODES:
            logger.warning("OKX 限频 | %s", detail)
            raise RateLimitError(detail)

        if code in _OKX_AUTH_CODES:
            logger.error("OKX 鉴权失败 | %s", detail)
            raise AuthenticationError(detail)

        if code in _OKX_NETWORK_CODES:
            logger.warning("OKX 网络/服务异常 | %s", detail)
            raise NetworkError(detail)

        logger.error("OKX API 错误 | %s", detail)
        raise APIError(message=f"{operation}: {msg}", code=code, raw=resp)

    return resp.get("data", [])


def check_batch_response(resp: dict, operation: str = "") -> list[dict]:
    """
    检查批量操作响应，返回每条结果。
    部分成功不抛异常，由调用方检查每条 sCode。
    """
    data = check_response(resp, operation)
    return data
