"""事件总线 - 发布/订阅模式，解耦各模块"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventType(Enum):
    """系统事件类型"""
    # ── 行情事件 ──────────────────────────────────────────────────
    TICK = "TICK"               # 行情快照更新 (TickData)
    BAR = "BAR"                 # K 线更新 (BarData)
    DEPTH = "DEPTH"             # 深度/订单簿更新 (OrderBook)
    TRADE = "TRADE"             # 市场成交 (TradeData)

    # ── 交易事件 ──────────────────────────────────────────────────
    ORDER_SUBMITTED = "ORDER_SUBMITTED"       # 订单已提交 (OrderData)
    ORDER_FILLED = "ORDER_FILLED"             # 订单完全成交 (OrderData)
    ORDER_PARTIAL = "ORDER_PARTIAL"           # 订单部分成交 (OrderData)
    ORDER_CANCELLED = "ORDER_CANCELLED"       # 订单已撤销 (OrderData)
    ORDER_REJECTED = "ORDER_REJECTED"         # 订单被拒绝 (OrderData)
    ORDER_UPDATED = "ORDER_UPDATED"           # 订单状态变化（通用）(OrderData)

    # ── 策略委托事件 ──────────────────────────────────────────────
    ALGO_ORDER_UPDATED = "ALGO_ORDER_UPDATED" # 策略委托状态变化 (AlgoOrderData)

    # ── 持仓/账户事件 ─────────────────────────────────────────────
    POSITION_UPDATED = "POSITION_UPDATED"     # 持仓变化 (PositionData)
    BALANCE_UPDATED = "BALANCE_UPDATED"       # 账户余额变化 (BalanceData)

    # ── 风控事件 ──────────────────────────────────────────────────
    RISK_ALERT = "RISK_ALERT"                 # 风控告警（日志/通知，不强制停止）
    RISK_BREACH = "RISK_BREACH"               # 风控突破（需立即处理）

    # ── 系统事件 ──────────────────────────────────────────────────
    GATEWAY_CONNECTED = "GATEWAY_CONNECTED"       # Gateway 已连接
    GATEWAY_DISCONNECTED = "GATEWAY_DISCONNECTED" # Gateway 已断连
    ERROR = "ERROR"                               # 系统错误
    LOG = "LOG"                                   # 日志事件


class Event:
    """事件对象"""

    __slots__ = ("type", "data", "source")

    def __init__(self, type_: EventType, data: Any = None, source: str = ""):
        self.type = type_
        self.data = data
        self.source = source   # 发布者标识（如 "okx_gateway"）

    def __repr__(self) -> str:
        return f"Event(type={self.type.value}, source={self.source!r})"


# 回调类型：同步函数或 async 函数
HandlerType = Callable[[Event], Any]


class EventBus:
    """
    同步 + 异步兼容的事件总线。

    同步回调直接在 publish() 中调用；
    异步回调通过 asyncio.get_event_loop().create_task() 调度。
    """

    def __init__(self):
        # {EventType: [handler, ...]}
        self._handlers: dict[EventType, list[HandlerType]] = defaultdict(list)
        self._global_handlers: list[HandlerType] = []  # 订阅所有事件的回调

    # ─────────────────────────── 订阅 ────────────────────────────

    def subscribe(self, event_type: EventType, handler: HandlerType) -> None:
        """订阅指定类型事件。同一 handler 不会重复注册。"""
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def subscribe_all(self, handler: HandlerType) -> None:
        """订阅所有事件类型。"""
        if handler not in self._global_handlers:
            self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: HandlerType) -> None:
        """取消订阅。"""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def unsubscribe_all(self, handler: HandlerType) -> None:
        """取消全局订阅。"""
        if handler in self._global_handlers:
            self._global_handlers.remove(handler)

    # ─────────────────────────── 发布 ────────────────────────────

    def publish(self, event_type: EventType, data: Any = None, source: str = "") -> None:
        """
        发布事件。

        同步回调：直接调用。
        异步回调：尝试在运行中的事件循环里创建任务；否则回退到同步调用。
        """
        event = Event(event_type, data, source)

        all_handlers = list(self._handlers.get(event_type, [])) + self._global_handlers
        for handler in all_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    self._dispatch_async(handler, event)
                else:
                    handler(event)
            except Exception:
                logger.exception(
                    "事件处理器异常 | event=%s handler=%s",
                    event_type.value, handler.__qualname__,
                )

    def _dispatch_async(self, handler: HandlerType, event: Event) -> None:
        """将异步 handler 调度到当前事件循环，如无则新建。"""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(handler(event))
            # 异步任务内部抛出的异常不会被外层 try-except 捕获，
            # 通过 done_callback 补获并记录，防止异常静默丢失。
            task.add_done_callback(
                lambda t: logger.exception(
                    "异步事件处理器异常 | event=%s handler=%s",
                    event.type.value, handler.__qualname__,
                )
                if not t.cancelled() and t.exception() is not None
                else None
            )
        except RuntimeError:
            # 当前线程没有运行的事件循环
            asyncio.run(handler(event))

    # ─────────────────────────── 工具 ────────────────────────────

    def handler_count(self, event_type: EventType) -> int:
        """返回指定事件类型的订阅数量（不含全局订阅）。"""
        return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        """清空所有订阅（主要用于测试）。"""
        self._handlers.clear()
        self._global_handlers.clear()
