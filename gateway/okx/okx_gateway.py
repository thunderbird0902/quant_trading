"""OKX Gateway 主类 - 组装所有子模块，实现 BaseGateway 抽象接口"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from core.enums import Exchange, MarginMode, MarketType, OrderType, PositionMode, PositionSide
from core.event_bus import EventBus, EventType
from core.models import (
    AlgoOrderData, BalanceData, BarData, CurrencyBalance,
    FeeRate, FundingRateData, Instrument, MarkPriceData,
    OrderBook, OrderData, OrderRequest, PositionData,
    TickData, TradeData, TransferRequest,
)
from gateway.base_gateway import BaseGateway

from .okx_account import OKXAccount
from .okx_algo_trader import OKXAlgoTrader
from .okx_market_data import OKXMarketData
from .okx_trader import OKXTrader
from .okx_websocket import OKXWebSocket

logger = logging.getLogger("trading.okx")


class OKXGateway(BaseGateway):
    """
    OKX 交易所 Gateway。

    组装 OKXMarketData / OKXAccount / OKXTrader / OKXAlgoTrader / OKXWebSocket，
    向上层提供统一的 BaseGateway 接口。

    配置项（config 字典）：
        api_key     : OKX API Key
        secret_key  : OKX Secret Key
        passphrase  : OKX Passphrase
        flag        : "0"=实盘, "1"=模拟盘（默认 "1"）
        proxy_host  : HTTP 代理 host（可选）
        proxy_port  : HTTP 代理 port（可选）
    """

    exchange = Exchange.OKX

    def __init__(self, event_bus: EventBus, config: dict):
        super().__init__(event_bus, config)

        api_key = config.get("api_key", "")
        secret_key = config.get("secret_key", "")
        passphrase = config.get("passphrase", "")
        flag = config.get("flag", "1")

        self._market_data = OKXMarketData(api_key, secret_key, passphrase, flag)
        self._account = OKXAccount(api_key, secret_key, passphrase, flag)
        self._trader = OKXTrader(api_key, secret_key, passphrase, flag)
        self._algo_trader = OKXAlgoTrader(api_key, secret_key, passphrase, flag)
        self._websocket = OKXWebSocket(event_bus, api_key, secret_key, passphrase, flag)

        logger.info(
            "OKXGateway 初始化完成 flag=%s (模式: %s)",
            flag, "模拟盘" if flag == "1" else "实盘"
        )

    # ──────────────────── 连接管理 ──────────────────────────────

    def connect(self) -> None:
        """连接 OKX（验证 REST API 可用性）。"""
        try:
            # 用获取账户配置验证连通性
            config = self._account.get_account_config()
            self._connected = True
            logger.info("OKX 连接成功，账户模式: acctLv=%s", config.get("acctLv"))
            self.event_bus.publish(EventType.GATEWAY_CONNECTED, self.exchange, source="okx")
        except Exception as e:
            self._connected = False
            logger.error("OKX 连接失败: %s", e)
            self.event_bus.publish(EventType.GATEWAY_DISCONNECTED, self.exchange, source="okx")
            raise

    def disconnect(self) -> None:
        """断开连接。"""
        self._websocket.stop()
        self._connected = False
        logger.info("OKX 已断开")
        self.event_bus.publish(EventType.GATEWAY_DISCONNECTED, self.exchange, source="okx")

    def is_connected(self) -> bool:
        return self._connected

    # ──────────────────── 行情数据 ──────────────────────────────

    def get_instruments(self, market_type: MarketType) -> list[Instrument]:
        return self._market_data.get_instruments(market_type)

    def get_ticker(self, inst_id: str) -> TickData:
        return self._market_data.get_ticker(inst_id)

    def get_tickers(self, market_type: MarketType) -> list[TickData]:
        return self._market_data.get_tickers(market_type)

    def get_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        return self._market_data.get_klines(inst_id, interval, limit)

    def get_history_klines(
        self, inst_id: str, interval: str, start: datetime, end: datetime
    ) -> list[BarData]:
        return self._market_data.get_history_klines(inst_id, interval, start, end)

    def get_orderbook(self, inst_id: str, depth: int = 20) -> OrderBook:
        return self._market_data.get_orderbook(inst_id, depth)

    def get_recent_trades(self, inst_id: str, limit: int = 100) -> list[TradeData]:
        return self._market_data.get_recent_trades(inst_id, limit)

    def get_funding_rate(self, inst_id: str) -> FundingRateData:
        return self._market_data.get_funding_rate(inst_id)

    def get_mark_price(self, inst_id: str) -> MarkPriceData:
        return self._market_data.get_mark_price(inst_id)

    # ──────────────────── WebSocket 订阅 ────────────────────────

    def subscribe_ticker(self, inst_id: str) -> None:
        self._websocket.subscribe_ticker(inst_id)

    def subscribe_orderbook(self, inst_id: str, depth: int = 5) -> None:
        self._websocket.subscribe_orderbook(inst_id, depth)

    def subscribe_kline(self, inst_id: str, interval: str) -> None:
        self._websocket.subscribe_kline(inst_id, interval)

    def subscribe_trades(self, inst_id: str) -> None:
        self._websocket.subscribe_trades(inst_id)

    def start_websocket(self) -> None:
        """
        启动 WebSocket（非阻塞，需在异步环境调用 start_websocket_async）。

        建议在独立线程调用：
            import threading
            t = threading.Thread(target=gateway.start_websocket, daemon=True)
            t.start()
        """
        self._websocket.start()

    async def start_websocket_async(self) -> None:
        """在已有事件循环中异步启动 WebSocket。"""
        await self._websocket.start_async()

    # ──────────────────── 账户 ──────────────────────────────────

    def get_balance(self) -> BalanceData:
        return self._account.get_balance()

    def get_positions(self, inst_id: str | None = None) -> list[PositionData]:
        return self._account.get_positions(inst_id)

    def get_account_config(self) -> dict:
        return self._account.get_account_config()

    def get_fee_rate(self, inst_id: str) -> FeeRate:
        # 根据 inst_id 推断 market_type
        market_type = self._infer_market_type(inst_id)
        return self._account.get_fee_rate(inst_id, market_type)

    def set_leverage(
        self,
        inst_id: str,
        leverage: int,
        margin_mode: MarginMode,
        position_side: PositionSide | None = None,
    ) -> bool:
        return self._account.set_leverage(inst_id, leverage, margin_mode, position_side)

    def get_leverage(self, inst_id: str, margin_mode: MarginMode) -> int:
        data = self._account.get_leverage(inst_id, margin_mode)
        if data:
            return int(data[0].get("lever", 1))
        return 1

    def set_position_mode(self, mode: PositionMode) -> bool:
        return self._account.set_position_mode(mode)

    def get_funding_balance(self) -> list[CurrencyBalance]:
        return self._account.get_funding_balance()

    # ──────────────────── 交易 ──────────────────────────────────

    def send_order(self, request: OrderRequest) -> OrderData:
        return self._trader.send_order(request)

    def cancel_order(self, order_id: str, inst_id: str) -> bool:
        return self._trader.cancel_order(order_id, inst_id)

    def modify_order(
        self,
        order_id: str,
        inst_id: str,
        new_price: Decimal | None = None,
        new_quantity: Decimal | None = None,
    ) -> bool:
        return self._trader.modify_order(order_id, inst_id, new_price, new_quantity)

    def batch_send_orders(self, requests: list[OrderRequest]) -> list[OrderData]:
        return self._trader.batch_send_orders(requests)

    def batch_cancel_orders(self, orders: list[tuple[str, str]]) -> list[bool]:
        return self._trader.batch_cancel_orders(orders)

    def get_order(self, order_id: str, inst_id: str) -> OrderData:
        return self._trader.get_order(order_id, inst_id)

    def get_open_orders(self, inst_id: str | None = None) -> list[OrderData]:
        return self._trader.get_open_orders(inst_id)

    def get_order_history(self, market_type: MarketType, days: int = 7) -> list[OrderData]:
        return self._trader.get_order_history(market_type, days)

    def get_trade_history(self, market_type: MarketType, days: int = 7) -> list[TradeData]:
        return self._trader.get_trade_history(market_type, days)

    def cancel_all_after(self, timeout_seconds: int) -> bool:
        """设置倒计时全撤（Dead Man's Switch）。"""
        return self._trader.cancel_all_after(timeout_seconds)

    # ──────────────────── 策略委托 ──────────────────────────────

    def send_algo_order(self, request: OrderRequest) -> AlgoOrderData:
        """
        路由到对应策略委托类型。

        根据 request.order_type 自动选择：
        - STOP_LIMIT / TAKE_PROFIT → send_tp_sl_order
        - TRIGGER → send_trigger_order
        - TRAILING_STOP → send_trailing_stop_order
        - ICEBERG → send_iceberg_order
        - TWAP → send_twap_order
        """
        ot = request.order_type
        qty = str(request.quantity)
        inst_id = request.inst_id

        if ot in (OrderType.STOP_LIMIT, OrderType.STOP_MARKET,
                   OrderType.TAKE_PROFIT):
            tp = str(request.take_profit_price) if request.take_profit_price else ""
            sl = str(request.stop_loss_price) if request.stop_loss_price else ""
            return self._algo_trader.send_tp_sl_order(
                inst_id=inst_id,
                side=request.side,
                quantity=qty,
                margin_mode=request.margin_mode,
                position_side=request.position_side,
                tp_trigger_price=tp,
                sl_trigger_price=sl,
            )

        elif ot == OrderType.TRIGGER:
            return self._algo_trader.send_trigger_order(
                inst_id=inst_id,
                side=request.side,
                quantity=qty,
                margin_mode=request.margin_mode,
                trigger_price=str(request.trigger_price or ""),
                order_price=str(request.price) if request.price else "-1",
                position_side=request.position_side,
            )

        elif ot == OrderType.TRAILING_STOP:
            callback = str(request.callback_ratio) if request.callback_ratio else ""
            return self._algo_trader.send_trailing_stop_order(
                inst_id=inst_id,
                side=request.side,
                quantity=qty,
                margin_mode=request.margin_mode,
                callback_ratio=callback,
                position_side=request.position_side,
            )

        elif ot == OrderType.ICEBERG:
            return self._algo_trader.send_iceberg_order(
                inst_id=inst_id,
                side=request.side,
                quantity=qty,
                price_limit=str(request.price or ""),
                margin_mode=request.margin_mode,
                sz_limit=str(request.sz_limit or qty),
                px_var=request.extra.get("pxVar", "0.01"),
            )

        elif ot == OrderType.TWAP:
            return self._algo_trader.send_twap_order(
                inst_id=inst_id,
                side=request.side,
                quantity=qty,
                price_limit=str(request.price or ""),
                margin_mode=request.margin_mode,
                sz_limit=str(request.sz_limit or qty),
                time_interval=request.time_interval or "60",
                px_var=request.extra.get("pxVar", "0.01"),
            )

        raise ValueError(f"不支持的策略委托类型: {ot}")

    def cancel_algo_order(self, algo_id: str, inst_id: str) -> bool:
        return self._algo_trader.cancel_algo_order(algo_id, inst_id)

    def get_algo_orders(self, order_type: OrderType | None = None) -> list[AlgoOrderData]:
        okx_type = None
        if order_type:
            from .okx_utils import order_type_to_okx
            okx_type = order_type_to_okx(order_type)
        return self._algo_trader.get_algo_orders(okx_type)

    # ──────────────────── 私有工具 ───────────────────────────────

    def _infer_market_type(self, inst_id: str) -> MarketType:
        """根据 inst_id 格式推断市场类型（粗略判断）。"""
        parts = inst_id.split("-")
        if len(parts) == 2:
            return MarketType.SPOT
        if len(parts) >= 3:
            suffix = parts[-1]
            if suffix == "SWAP":
                return MarketType.SWAP
            if suffix.isdigit():
                return MarketType.FUTURES
            if suffix in ("C", "P"):
                return MarketType.OPTION
        return MarketType.SPOT
