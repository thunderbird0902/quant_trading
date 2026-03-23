"""OKX WebSocket 模块 - 公共频道 + 私有频道实时推送"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import websockets
from websockets.exceptions import ConnectionClosed

from core.enums import Exchange
from core.event_bus import EventBus, EventType
from core.models import BalanceData, BarData, OrderBook, OrderData, PositionData, TickData, TradeData

from .okx_utils import (
    parse_balance,
    parse_kline,
    parse_order,
    parse_orderbook,
    parse_position,
    parse_ticker,
    parse_trade,
    parse_funding_rate,
    parse_mark_price,
)

logger = logging.getLogger("market.okx.ws")

# OKX WebSocket 端点
WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"
WS_PUBLIC_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
WS_PRIVATE_URL = "wss://ws.okx.com:8443/ws/v5/private"
WS_PRIVATE_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"

# 心跳间隔（OKX 要求 ≤30s 发一次 ping）
HEARTBEAT_INTERVAL = 25
# 重连最大等待（指数退避）
MAX_RECONNECT_DELAY = 60


class OKXWebSocket:
    """
    OKX WebSocket 管理器。

    功能：
    - 公共频道：tickers、candle、trades、books、funding-rate、mark-price 等
    - 私有频道：account、positions、orders、fills、orders-algo
    - 自动重连（指数退避，断线后恢复所有订阅）
    - 心跳维护（每 25 秒发 ping）
    - 通过 EventBus 发布行情/交易事件
    """

    def __init__(
        self,
        event_bus: EventBus,
        api_key: str,
        secret_key: str,
        passphrase: str,
        flag: str = "1",
    ):
        self.event_bus = event_bus
        self._api_key = api_key
        self._secret_key = secret_key
        self._passphrase = passphrase
        self._flag = flag

        # 是否使用模拟盘
        self._is_demo = flag == "1"

        # 当前已订阅的频道（用于重连后恢复）
        self._public_subscriptions: list[dict] = []
        self._private_subscriptions: list[dict] = []

        # WebSocket 连接对象
        self._public_ws = None
        self._private_ws = None

        # 运行状态
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ─────────────────────── 公开接口 ────────────────────────────

    def subscribe_ticker(self, inst_id: str) -> None:
        """订阅行情（tickers 频道）。"""
        self._add_public_sub({"channel": "tickers", "instId": inst_id})

    def subscribe_orderbook(self, inst_id: str, depth: int = 5) -> None:
        """订阅深度（books5 或 books 频道）。"""
        channel = "books5" if depth <= 5 else "books"
        self._add_public_sub({"channel": channel, "instId": inst_id})

    def subscribe_kline(self, inst_id: str, interval: str) -> None:
        """订阅 K 线（candle{interval} 频道）。"""
        self._add_public_sub({"channel": f"candle{interval}", "instId": inst_id})

    def subscribe_trades(self, inst_id: str) -> None:
        """订阅成交（trades 频道）。"""
        self._add_public_sub({"channel": "trades", "instId": inst_id})

    def subscribe_funding_rate(self, inst_id: str) -> None:
        """订阅资金费率（永续合约）。"""
        self._add_public_sub({"channel": "funding-rate", "instId": inst_id})

    def subscribe_mark_price(self, inst_id: str) -> None:
        """订阅标记价格。"""
        self._add_public_sub({"channel": "mark-price", "instId": inst_id})

    def subscribe_price_limit(self, inst_id: str) -> None:
        """订阅限价（涨跌停）。"""
        self._add_public_sub({"channel": "price-limit", "instId": inst_id})

    def subscribe_index_tickers(self, inst_id: str) -> None:
        """订阅指数行情。"""
        self._add_public_sub({"channel": "index-tickers", "instId": inst_id})

    def subscribe_account(self) -> None:
        """订阅账户余额变化（私有频道）。"""
        self._add_private_sub({"channel": "account"})

    def subscribe_positions(self, inst_type: str = "ANY") -> None:
        """订阅持仓变化（私有频道）。"""
        self._add_private_sub({"channel": "positions", "instType": inst_type})

    def subscribe_orders(self, inst_type: str = "ANY") -> None:
        """订阅订单状态（私有频道）。"""
        self._add_private_sub({"channel": "orders", "instType": inst_type})

    def subscribe_fills(self) -> None:
        """订阅成交（私有频道）。"""
        self._add_private_sub({"channel": "fills"})

    def subscribe_algo_orders(self, inst_type: str = "ANY") -> None:
        """订阅策略委托状态（私有频道）。"""
        self._add_private_sub({"channel": "orders-algo", "instType": inst_type})

    # ─────────────────────── 生命周期 ────────────────────────────

    def start(self) -> None:
        """在当前线程启动 WebSocket（阻塞，建议在独立线程或 asyncio 任务中调用）。"""
        self._running = True
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            logger.info("WebSocket 收到中断信号，退出")

    async def start_async(self) -> None:
        """在已有事件循环中异步启动。"""
        self._running = True
        await self._run()

    def stop(self) -> None:
        """停止 WebSocket。"""
        self._running = False
        for task in self._tasks:
            task.cancel()

    # ─────────────────────── 内部：主循环 ────────────────────────

    async def _run(self) -> None:
        """并发运行公共频道和私有频道。"""
        tasks = [self._run_channel(public=True)]
        if self._private_subscriptions or self._api_key:
            tasks.append(self._run_channel(public=False))
        await asyncio.gather(*tasks)

    async def _run_channel(self, public: bool) -> None:
        """
        单个频道（public/private）的连接 + 重连循环。
        """
        delay = 1.0
        url = self._get_ws_url(public)

        while self._running:
            try:
                logger.info("WebSocket 连接 %s ...", url)
                async with websockets.connect(
                    url,
                    ping_interval=None,   # 手动维护心跳
                    ping_timeout=30,
                    close_timeout=5,
                ) as ws:
                    if public:
                        self._public_ws = ws
                    else:
                        self._private_ws = ws
                        await self._login(ws)

                    # 重新发送所有订阅
                    subs = self._public_subscriptions if public else self._private_subscriptions
                    if subs:
                        await ws.send(json.dumps({"op": "subscribe", "args": subs}))
                        logger.info("已恢复 %d 个%s频道订阅", len(subs), "公共" if public else "私有")

                    # 并发：消息接收 + 心跳
                    await asyncio.gather(
                        self._recv_loop(ws),
                        self._heartbeat_loop(ws),
                    )
                delay = 1.0   # 成功连接后重置退避

            except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "WebSocket %s 连接断开: %s，%.1fs 后重试",
                    "公共" if public else "私有", exc, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

            except Exception:
                logger.exception("WebSocket 未知异常，%.1fs 后重试", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    # ─────────────────────── 内部：消息处理 ──────────────────────

    async def _recv_loop(self, ws) -> None:
        """消息接收循环。"""
        async for raw in ws:
            if raw == "pong":
                continue
            try:
                msg = json.loads(raw)
                await self._handle_message(msg)
            except json.JSONDecodeError:
                logger.debug("WebSocket 收到非 JSON 消息: %s", raw[:100])
            except Exception:
                logger.exception("WebSocket 消息处理异常")

    async def _heartbeat_loop(self, ws) -> None:
        """心跳维护（每 25 秒发 ping）。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await ws.send("ping")
            except Exception:
                break

    async def _handle_message(self, msg: dict) -> None:
        """分发 WebSocket 消息到对应处理器。"""
        # 登录/订阅确认
        if "event" in msg:
            event = msg["event"]
            if event == "login":
                logger.info("WebSocket 私有频道登录成功")
            elif event == "subscribe":
                logger.debug("订阅成功: %s", msg.get("arg"))
            elif event == "error":
                logger.error("WebSocket 错误: [%s] %s", msg.get("code"), msg.get("msg"))
            return

        # 数据推送
        if "data" not in msg:
            return

        arg = msg.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        data_list = msg.get("data", [])

        for item in data_list:
            try:
                self._route_channel(channel, inst_id, item)
            except Exception:
                logger.exception("频道数据解析失败 channel=%s", channel)

    def _route_channel(self, channel: str, inst_id: str, data: dict) -> None:
        """根据频道名路由到对应解析器。"""
        if channel == "tickers":
            tick = parse_ticker(data)
            self.event_bus.publish(EventType.TICK, tick, source="okx_ws")

        elif channel.startswith("candle"):
            interval = channel[len("candle"):]
            bar = parse_kline(data if isinstance(data, list) else list(data.values()),
                               inst_id, interval)
            self.event_bus.publish(EventType.BAR, bar, source="okx_ws")

        elif channel in ("trades", "trades-all"):
            trade = parse_trade(data)
            self.event_bus.publish(EventType.TRADE, trade, source="okx_ws")

        elif channel in ("books5", "books", "bbo-tbt", "books-l2-tbt", "books50-l2-tbt"):
            book = parse_orderbook(data, inst_id)
            self.event_bus.publish(EventType.DEPTH, book, source="okx_ws")

        elif channel == "funding-rate":
            fr = parse_funding_rate(data)
            self.event_bus.publish(EventType.TICK, fr, source="okx_ws")

        elif channel == "mark-price":
            mp = parse_mark_price(data)
            self.event_bus.publish(EventType.TICK, mp, source="okx_ws")

        elif channel == "account":
            balance = parse_balance(data)
            self.event_bus.publish(EventType.BALANCE_UPDATED, balance, source="okx_ws")

        elif channel == "positions":
            position = parse_position(data)
            self.event_bus.publish(EventType.POSITION_UPDATED, position, source="okx_ws")

        elif channel == "orders":
            order = parse_order(data)
            # 细化订单事件类型
            from core.enums import OrderStatus
            status_event_map = {
                OrderStatus.FILLED: EventType.ORDER_FILLED,
                OrderStatus.PARTIAL_FILLED: EventType.ORDER_PARTIAL,
                OrderStatus.CANCELLED: EventType.ORDER_CANCELLED,
                OrderStatus.REJECTED: EventType.ORDER_REJECTED,
                OrderStatus.SUBMITTED: EventType.ORDER_SUBMITTED,
            }
            event_type = status_event_map.get(order.status, EventType.ORDER_UPDATED)
            self.event_bus.publish(event_type, order, source="okx_ws")

        elif channel == "fills":
            trade = parse_trade(data)
            self.event_bus.publish(EventType.TRADE, trade, source="okx_ws")

        elif channel == "orders-algo":
            from .okx_utils import parse_algo_order
            algo = parse_algo_order(data)
            self.event_bus.publish(EventType.ALGO_ORDER_UPDATED, algo, source="okx_ws")

    # ─────────────────────── 内部：登录 ──────────────────────────

    async def _login(self, ws) -> None:
        """对私有频道进行 API Key 登录。"""
        import base64
        import hashlib
        import hmac

        timestamp = str(int(time.time()))
        sign_str = timestamp + "GET" + "/users/self/verify"
        sign = base64.b64encode(
            hmac.new(
                self._secret_key.encode("utf-8"),
                sign_str.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        login_msg = {
            "op": "login",
            "args": [{
                "apiKey": self._api_key,
                "passphrase": self._passphrase,
                "timestamp": timestamp,
                "sign": sign,
            }],
        }
        await ws.send(json.dumps(login_msg))
        # 等待登录确认
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            result = json.loads(resp)
            if result.get("event") != "login":
                logger.warning("WebSocket 登录响应异常: %s", result)
        except asyncio.TimeoutError:
            logger.error("WebSocket 私有频道登录超时")

    # ─────────────────────── 内部：订阅管理 ──────────────────────

    def _add_public_sub(self, arg: dict) -> None:
        """添加公共频道订阅（去重）。"""
        if arg not in self._public_subscriptions:
            self._public_subscriptions.append(arg)
            if self._public_ws and not self._public_ws.closed:
                asyncio.create_task(
                    self._public_ws.send(json.dumps({"op": "subscribe", "args": [arg]}))
                )

    def _add_private_sub(self, arg: dict) -> None:
        """添加私有频道订阅（去重）。"""
        if arg not in self._private_subscriptions:
            self._private_subscriptions.append(arg)
            if self._private_ws and not self._private_ws.closed:
                asyncio.create_task(
                    self._private_ws.send(json.dumps({"op": "subscribe", "args": [arg]}))
                )

    def _get_ws_url(self, public: bool) -> str:
        if public:
            return WS_PUBLIC_DEMO_URL if self._is_demo else WS_PUBLIC_URL
        return WS_PRIVATE_DEMO_URL if self._is_demo else WS_PRIVATE_URL
