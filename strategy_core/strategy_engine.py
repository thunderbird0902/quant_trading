"""策略引擎 - 加载、启停、管理策略，向策略注入交易能力"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Type

from core.enums import Exchange, MarginMode, OrderSide, OrderType, PositionSide
from core.event_bus import EventBus, EventType, Event
from core.models import (
    BalanceData, BarData, OrderData, OrderRequest,
    PositionData, TickData, TradeData,
)

if TYPE_CHECKING:
    from core.engine import MainEngine
    from strategy_core.base_strategy import BaseStrategy

logger = logging.getLogger("strategy_core")


class StrategyEngine:
    """
    策略引擎。

    职责：
    1. 加载/卸载策略，管理策略生命周期
    2. 将事件总线上的行情/交易事件分发给对应策略
    3. 向策略提供 buy/sell/short/cover 等交易操作
    4. 隔离策略与底层 Gateway，策略不直接操作交易所接口
    """

    def __init__(self, main_engine: "MainEngine"):
        self._main_engine = main_engine
        self.event_bus: EventBus = main_engine.event_bus

        # 名称 → 策略实例
        self._strategies: dict[str, "BaseStrategy"] = {}

        # 订阅映射：strategy_name → set of inst_id
        self._subscriptions: dict[str, set[str]] = {}

        # 持仓/余额缓存
        self._positions: dict[str, PositionData] = {}  # inst_id → 最新持仓
        self._balance: BalanceData | None = None

        logger.info("策略引擎初始化完成")

    # ─────────────────────── 生命周期 ────────────────────────────

    def start(self) -> None:
        """启动引擎：订阅事件总线，加载所有策略。"""
        self.event_bus.subscribe(EventType.TICK, self._on_tick)
        self.event_bus.subscribe(EventType.BAR, self._on_bar)
        self.event_bus.subscribe(EventType.ORDER_UPDATED, self._on_order)
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_order)
        self.event_bus.subscribe(EventType.ORDER_PARTIAL, self._on_order)
        self.event_bus.subscribe(EventType.ORDER_CANCELLED, self._on_order)
        self.event_bus.subscribe(EventType.TRADE, self._on_trade)
        self.event_bus.subscribe(EventType.POSITION_UPDATED, self._on_position)
        self.event_bus.subscribe(EventType.BALANCE_UPDATED, self._on_balance)
        self.event_bus.subscribe(EventType.RISK_BREACH, self._on_risk_breach)
        logger.info("策略引擎已启动")

    def stop(self) -> None:
        """停止所有策略并注销事件监听。"""
        for name in list(self._strategies.keys()):
            self.stop_strategy(name)
        logger.info("策略引擎已停止")

    # ─────────────────────── 策略管理 ────────────────────────────

    def add_strategy(
        self,
        strategy_class: Type["BaseStrategy"],
        name: str,
        inst_id: str,
        exchange: Exchange,
        config: dict | None = None,
    ) -> "BaseStrategy":
        """
        注册并初始化策略。

        Args:
            strategy_class: 策略类（BaseStrategy 子类）
            name:           策略实例名（唯一）
            inst_id:        主交易产品
            exchange:       交易所
            config:         策略参数

        Returns:
            策略实例
        """
        if name in self._strategies:
            raise ValueError(f"策略 {name!r} 已存在")

        strategy = strategy_class(
            name=name,
            strategy_engine=self,
            inst_id=inst_id,
            config=config or {},
        )
        strategy._exchange = exchange
        # FIX: [margin_mode 原先硬编码 CASH，合约交易会被 OKX 拒绝]
        # 从策略 config 读取，默认 CROSS（适用于 OKX 合约，现货用 CASH）
        margin_mode_str = (config or {}).get("margin_mode", "CROSS").upper()
        try:
            strategy._margin_mode = MarginMode(margin_mode_str)
        except ValueError:
            logger.warning("策略 %s 未知 margin_mode=%s，使用 CROSS", name, margin_mode_str)
            strategy._margin_mode = MarginMode.CROSS
        self._strategies[name] = strategy
        # 初始化该策略的订阅集合（默认订阅主产品）
        self._subscriptions[name] = {inst_id}
        logger.info("策略已注册: %s (%s %s)", name, strategy_class.__name__, inst_id)
        return strategy

    def start_strategy(self, name: str) -> None:
        """初始化并启动指定策略。"""
        strategy = self._get_strategy(name)
        if strategy.active:
            logger.warning("策略 %s 已运行", name)
            return
        try:
            strategy.on_init()
        except Exception:
            logger.exception("策略 %s on_init 异常，启动中止", name)
            return
        try:
            strategy.on_start()
        except Exception:
            logger.exception("策略 %s on_start 异常，但策略将继续标记为已启动", name)
        strategy.active = True
        logger.info("策略已启动: %s", name)

    def stop_strategy(self, name: str) -> None:
        """停止指定策略。"""
        strategy = self._get_strategy(name)
        if not strategy.active:
            return
        try:
            strategy.on_stop()
        except Exception:
            logger.exception("策略 %s on_stop 异常", name)
        finally:
            strategy.active = False
        logger.info("策略已停止: %s", name)

    def subscribe_extra(self, strategy_name: str, inst_id: str) -> None:
        """
        为策略订阅额外的行情品种（多品种策略使用）。

        订阅后，该 inst_id 的 Tick/Bar 事件也会路由到该策略。
        注意：交易操作仍需在策略代码中明确指定 inst_id 参数。

        Args:
            strategy_name: 策略实例名
            inst_id:       额外订阅的产品 ID
        """
        self._get_strategy(strategy_name)  # 确认策略存在
        self._subscriptions.setdefault(strategy_name, set()).add(inst_id)
        logger.info("策略 %s 已订阅额外品种: %s", strategy_name, inst_id)

    def stop_all_strategies(self) -> None:
        """停止所有策略。"""
        for name in list(self._strategies.keys()):
            self.stop_strategy(name)

    def get_strategy(self, name: str) -> "BaseStrategy":
        """获取策略实例。"""
        return self._get_strategy(name)

    def list_strategies(self) -> list[str]:
        """列出所有策略名称。"""
        return list(self._strategies.keys())

    # ─────────────────────── 事件分发 ────────────────────────────

    def _on_tick(self, event: Event) -> None:
        tick: TickData = event.data
        for strategy in self._strategies.values():
            if strategy.active and tick.inst_id in self._subscriptions.get(strategy.name, set()):
                try:
                    strategy.on_tick(tick)
                except Exception:
                    logger.exception("策略 %s on_tick 异常", strategy.name)

    def _on_bar(self, event: Event) -> None:
        bar: BarData = event.data
        for strategy in self._strategies.values():
            if strategy.active and bar.inst_id in self._subscriptions.get(strategy.name, set()):
                try:
                    strategy.on_bar(bar)
                except Exception:
                    logger.exception("策略 %s on_bar 异常", strategy.name)

    def _on_order(self, event: Event) -> None:
        order: OrderData = event.data
        for strategy in self._strategies.values():
            if strategy.active and order.inst_id in self._subscriptions.get(strategy.name, set()):
                try:
                    strategy.on_order(order)
                except Exception:
                    logger.exception("策略 %s on_order 异常", strategy.name)

    def _on_trade(self, event: Event) -> None:
        trade: TradeData = event.data
        for strategy in self._strategies.values():
            if strategy.active and trade.inst_id in self._subscriptions.get(strategy.name, set()):
                try:
                    strategy.on_trade(trade)
                except Exception:
                    logger.exception("策略 %s on_trade 异常", strategy.name)

    def _on_position(self, event: Event) -> None:
        pos: PositionData = event.data
        # FIX: [hedge 模式同一品种同时有 LONG/SHORT 两个仓位，用 inst_id:posside 做 key
        # 否则第二个 POSITION_UPDATED 会覆盖第一个，策略只能看到最后一次更新的方向]
        key = f"{pos.inst_id}:{pos.position_side.value}"
        if pos.quantity == Decimal("0"):
            self._positions.pop(key, None)
        else:
            self._positions[key] = pos
        for strategy in self._strategies.values():
            if strategy.active and pos.inst_id in self._subscriptions.get(strategy.name, set()):
                try:
                    strategy.on_position(pos)
                except Exception:
                    logger.exception("策略 %s on_position 异常", strategy.name)

    def _on_balance(self, event: Event) -> None:
        self._balance = event.data

    def _on_risk_breach(self, event: Event) -> None:
        """风控触发：暂停所有策略下单；若系统性停止则撤销所有挂单。"""
        data = event.data or {}
        logger.error("风控触发！暂停所有策略下单: %s", data)
        for strategy in self._strategies.values():
            strategy.trading = False
        # 仅在 loss_limit 系统性停止时撤销所有挂单
        if data.get("is_halted", False):
            self._cancel_all_open_orders()

    def _cancel_all_open_orders(self) -> None:
        """撤销所有交易所的未成交挂单（风控触发时调用）。"""
        for exchange, gw in self._main_engine._gateways.items():
            if not gw.is_connected():
                continue
            try:
                open_orders = gw.get_open_orders()
                for order in open_orders:
                    try:
                        gw.cancel_order(order.order_id, order.inst_id)
                        logger.info("风控撤单: orderId=%s inst=%s", order.order_id, order.inst_id)
                    except Exception:
                        logger.exception("风控撤单失败: orderId=%s", order.order_id)
            except Exception:
                logger.exception("获取 %s 挂单失败", exchange.value)

    # ─────────────────────── 策略交易操作（内部注入）────────────────

    def _buy(
        self,
        strategy: "BaseStrategy",
        inst_id: str,
        price: Decimal,
        quantity: Decimal,
        order_type: OrderType,
    ) -> OrderData | None:
        """买入（现货 / 合约多头开仓）。"""
        request = self._make_request(strategy, inst_id, OrderSide.BUY, price, quantity, order_type)
        return self._send(request)

    def _sell(
        self,
        strategy: "BaseStrategy",
        inst_id: str,
        price: Decimal,
        quantity: Decimal,
        order_type: OrderType,
    ) -> OrderData | None:
        """卖出（现货 / 合约多头平仓）。"""
        request = self._make_request(strategy, inst_id, OrderSide.SELL, price, quantity, order_type)
        return self._send(request)

    def _short(self, strategy, inst_id, price, quantity, order_type):
        """做空（合约空头开仓）。"""
        request = self._make_request(
            strategy, inst_id, OrderSide.SELL, price, quantity, order_type,
            position_side=PositionSide.SHORT,
        )
        return self._send(request)

    def _cover(self, strategy, inst_id, price, quantity, order_type):
        """平空（合约空头平仓）。"""
        request = self._make_request(
            strategy, inst_id, OrderSide.BUY, price, quantity, order_type,
            position_side=PositionSide.SHORT,
        )
        return self._send(request)

    def _close_long(self, strategy, inst_id, price, quantity, order_type):
        """
        FIX: [新增] 平多仓（OKX hedge 模式：side=sell, posSide=long）。
        原 sell() 不设 posSide，在双向持仓模式下会被 OKX 拒绝。
        """
        request = self._make_request(
            strategy, inst_id, OrderSide.SELL, price, quantity, order_type,
            position_side=PositionSide.LONG,
        )
        return self._send(request)

    def _close_short(self, strategy, inst_id, price, quantity, order_type):
        """
        FIX: [新增] 平空仓（OKX hedge 模式：side=buy, posSide=short）。
        与 cover() 等价，提供语义更明确的别名。
        """
        request = self._make_request(
            strategy, inst_id, OrderSide.BUY, price, quantity, order_type,
            position_side=PositionSide.SHORT,
        )
        return self._send(request)

    def _cancel(self, strategy, order_id: str, inst_id: str) -> bool:
        exchange = getattr(strategy, "_exchange", Exchange.OKX)
        return self._main_engine.cancel_order(exchange, order_id, inst_id)

    def _get_position(self, inst_id: str) -> PositionData | None:
        # FIX: [_positions key 已改为 inst_id:posside，兼容 hedge 模式]
        # 优先返回 LONG 仓位，其次 NET，最后 SHORT（向后兼容 get_position(inst_id)）
        for side in (PositionSide.LONG, PositionSide.NET, PositionSide.SHORT):
            key = f"{inst_id}:{side.value}"
            pos = self._positions.get(key)
            if pos is not None and pos.quantity != Decimal("0"):
                return pos
        return None

    def _get_balance(self) -> BalanceData | None:
        return self._balance

    def _get_klines(self, inst_id: str, interval: str, limit: int) -> list[BarData]:
        # 推断 exchange（简化：使用第一个已连接的 gateway）
        for ex, gw in self._main_engine._gateways.items():
            if gw.is_connected():
                return gw.get_klines(inst_id, interval, limit)
        return []

    # ─────────────────────── 私有工具 ────────────────────────────

    def _make_request(
        self,
        strategy: "BaseStrategy",
        inst_id: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal,
        order_type: OrderType,
        position_side: PositionSide | None = None,
    ) -> OrderRequest:
        exchange = getattr(strategy, "_exchange", Exchange.OKX)
        # FIX: [margin_mode 原先硬编码 CASH，合约必须用 CROSS/ISOLATED，否则 OKX 报错]
        margin_mode = getattr(strategy, "_margin_mode", MarginMode.CROSS)
        return OrderRequest(
            inst_id=inst_id,
            exchange=exchange,
            side=side,
            order_type=order_type,
            price=price if order_type != OrderType.MARKET else None,
            quantity=quantity,
            margin_mode=margin_mode,
            position_side=position_side,
            extra={"strategy": strategy.name},
        )

    def _send(self, request: OrderRequest) -> OrderData | None:
        try:
            return self._main_engine.send_order(request)
        except Exception as e:
            logger.warning("策略下单失败: %s", e)
            return None

    def _get_strategy(self, name: str) -> "BaseStrategy":
        strategy = self._strategies.get(name)
        if strategy is None:
            raise KeyError(f"策略 {name!r} 未注册")
        return strategy
