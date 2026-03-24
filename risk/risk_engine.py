"""风控引擎 - 统一管理所有风控规则，接入事件总线"""

from __future__ import annotations

import logging
from decimal import Decimal
from threading import Lock
from typing import TYPE_CHECKING

from core.enums import Exchange, OrderSide, OrderType, PositionSide
from core.event_bus import EventBus, EventType, Event
from core.exceptions import RiskError
from core.models import BalanceData, OrderRequest, PositionData, TradeData
from .order_validator import OrderValidator
from .position_limit import PositionLimitChecker
from .loss_limit import LossLimitChecker
from .rate_limiter import OKXRateLimiter, OrderRateLimiter

if TYPE_CHECKING:
    from gateway.base_gateway import BaseGateway
    from strategy_core.strategy_engine import StrategyEngine

logger = logging.getLogger("risk")


class RiskEngine:
    """
    风控引擎。

    职责：
    1. 订阅 EventBus 上的持仓/余额/成交事件，实时维护风控状态
    2. 对每笔下单请求执行完整的风控检查链
    3. 触发风控时发布 RISK_ALERT / RISK_BREACH 事件
    4. 提供 emergency_stop() Kill Switch，紧急停止所有交易

    风控检查链（成本从低到高，任一失败则拒绝）：
        ① rate_limiter    — 业务下单频率限制（成本最低，最先过滤）
        ② order_validator — 精度、最小量、价格偏离、方向合理性
        ③ position_limit  — 品种上限、总仓位比例
        ④ loss_limit      — 单笔亏损、每日亏损、连续亏损
    """

    def __init__(self, event_bus: EventBus, config: dict | None = None):
        """
        Args:
            event_bus: 全局事件总线
            config:    风控配置（来自 settings.yaml risk 节）
        """
        self.event_bus = event_bus
        self._config = config or {}
        self.enabled = self._config.get("enabled", True)

        risk_cfg = self._config.get("risk", self._config)

        # 订单校验器（hedge_mode 默认 True，与 OKX 双仓模式对齐）
        self.order_validator = OrderValidator(
            price_deviation_limit=risk_cfg.get("price_deviation_limit", 0.1),
            hedge_mode=risk_cfg.get("hedge_mode", True),
        )

        # 仓位限制检查器
        self.position_limit = PositionLimitChecker(
            max_position_pct=risk_cfg.get("max_position_pct", 0.3),
            max_total_position_pct=risk_cfg.get("max_total_position_pct", 0.9),
            per_symbol_limits=risk_cfg.get("per_symbol_limits", {}),
        )

        # 亏损限额检查器
        self.loss_limit = LossLimitChecker(
            max_daily_loss_pct=risk_cfg.get("max_daily_loss_pct", 0.05),
            max_single_loss_pct=risk_cfg.get("max_single_loss_pct", 0.02),
            max_consecutive_losses=risk_cfg.get("max_consecutive_losses", 5),
        )

        # 业务层下单频率限制（风控用，非阻塞）
        self._order_rate_limiter = OrderRateLimiter(
            max_orders=risk_cfg.get("max_orders_per_second", 10),
            period=1.0,
        )

        # OKX HTTP API 频控（Gateway 层使用，此处保留引用供外部调用）
        self.rate_limiters: dict[Exchange, OKXRateLimiter] = {
            Exchange.OKX: OKXRateLimiter(),
        }

        # Gateway 引用（emergency_stop 使用）
        self._gateways: dict[Exchange, "BaseGateway"] = {}

        # 策略引擎引用（emergency_stop 使用）
        self._strategy_engine: "StrategyEngine | None" = None

        # 最大回撤保护：记录历史最高净值
        self._peak_equity: Decimal = Decimal("0")
        self._max_drawdown_pct: Decimal = Decimal(
            str(risk_cfg.get("max_drawdown_pct", 0.0))
        )  # 0.0 表示不启用

        # 缓存当前价格（inst_id → price）
        self._current_prices: dict[str, Decimal] = {}

        # FIX: [多策略并发下单时，两个线程同时通过仓位检查可能合计超限]
        # check_order 需加锁保证原子性
        self._check_lock = Lock()

        logger.info(
            "风控引擎初始化完成 enabled=%s max_daily_loss=%.1f%% "
            "max_position=%.1f%% max_drawdown=%.1f%%",
            self.enabled,
            float(risk_cfg.get("max_daily_loss_pct", 0.05)) * 100,
            float(risk_cfg.get("max_position_pct", 0.3)) * 100,
            float(risk_cfg.get("max_drawdown_pct", 0.0)) * 100,
        )

    # ─────────────────────── 生命周期 ────────────────────────────

    def start(self) -> None:
        """
        启动风控引擎：订阅事件总线。
        """
        self.event_bus.subscribe(EventType.TICK, self._on_tick)
        self.event_bus.subscribe(EventType.BALANCE_UPDATED, self._on_balance)
        self.event_bus.subscribe(EventType.POSITION_UPDATED, self._on_position)
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_trade)
        logger.info("风控引擎已启动，事件监听已注册")

    def stop(self) -> None:
        """停止风控引擎：注销事件监听。"""
        self.event_bus.unsubscribe(EventType.TICK, self._on_tick)
        self.event_bus.unsubscribe(EventType.BALANCE_UPDATED, self._on_balance)
        self.event_bus.unsubscribe(EventType.POSITION_UPDATED, self._on_position)
        self.event_bus.unsubscribe(EventType.ORDER_FILLED, self._on_trade)
        logger.info("风控引擎已停止")

    def register_gateway(self, gateway: "BaseGateway") -> None:
        """注入 Gateway 引用，供 emergency_stop() 撤单和平仓使用。"""
        self._gateways[gateway.exchange] = gateway
        logger.debug("风控引擎已注册 Gateway: %s", gateway.exchange.value)

    def set_strategy_engine(self, strategy_engine: "StrategyEngine") -> None:
        """注入策略引擎引用，供 emergency_stop() 暂停策略使用。"""
        self._strategy_engine = strategy_engine

    # ─────────────────────── 核心：检查下单请求 ────────────────────

    def check_order(
        self,
        request: OrderRequest,
        instrument=None,
    ) -> None:
        """
        对下单请求执行完整风控检查链。

        检查顺序（成本从低到高）：
        ① rate_limiter → ② order_validator → ③ position_limit → ④ loss_limit

        Args:
            request:    下单请求
            instrument: 产品信息（用于精度校验），None 时跳过精度检查

        Raises:
            RiskError 子类: 任何风控检查失败时抛出对应异常
        """
        if not self.enabled:
            return

        # FIX: [并发安全] 两个策略同时调用 check_order 可能各自通过 position_limit
        # 但合计超限——加锁保证整个检查链原子执行
        with self._check_lock:
            current_price = self._current_prices.get(request.inst_id)
            current_positions = list(self.position_limit._positions.values())

            try:
                # ① 业务下单频率限制（最轻量，最先检查）
                self._order_rate_limiter.check(request)

                # ② 订单参数校验（精度、数量、价格偏离、方向合理性）
                if instrument:
                    self.order_validator.validate(
                        request, instrument, current_price,
                        positions=current_positions,
                    )

                # ③ 仓位限制检查
                self.position_limit.check(request, current_price)

                # ④ 亏损限额检查（成本最高，最后检查）
                self.loss_limit.check(request, current_price)

            except RiskError as e:
                rule_name = type(e).__name__
                logger.warning(
                    "风控拒绝下单 | rule=%s reason=%s | "
                    "inst=%s side=%s qty=%s price=%s",
                    rule_name, e,
                    request.inst_id,
                    request.side.value,
                    request.quantity,
                    request.price,
                )
                self.event_bus.publish(
                    EventType.RISK_BREACH,
                    {
                        "rule": rule_name,
                        "reason": str(e),
                        "request": request,
                        "action": "rejected",
                    },
                    source="risk_engine",
                )
                raise

    def check_and_adjust(
        self,
        request: OrderRequest,
        instrument=None,
    ) -> OrderRequest:
        """
        自动调整精度并执行风控检查。

        与 check_order() 不同：先对 price/quantity 做精度对齐，再检查。

        Returns:
            精度调整后的 OrderRequest
        """
        if instrument:
            request = self.order_validator.auto_adjust(request, instrument)
        self.check_order(request, instrument)
        return request

    # ─────────────────────── Kill Switch ─────────────────────────

    def emergency_stop(self, reason: str = "") -> None:
        """
        紧急停止所有交易活动（Kill Switch）。

        执行顺序：
        a. 撤销所有 Gateway 的未完成订单（撤单失败不阻断后续流程）
        b. 市价平掉所有持仓（平仓前重新查询最新持仓量）
        c. 暂停策略引擎
        d. 标记风控为停止状态，发布 RISK_BREACH 事件

        注意：此方法幂等，重复调用安全（已 halted 时跳过 a/b/c）。
        """
        import signal

        if self.loss_limit.is_halted:
            logger.warning("emergency_stop 已执行过，跳过重复调用")
            return

        equity = float(self.loss_limit._total_equity)
        positions_snapshot = list(self.position_limit._positions.values())

        logger.critical(
            "EMERGENCY STOP 触发 | reason=%s | equity=%.2f | "
            "open_positions=%d | peak_equity=%.2f",
            reason, equity, len(positions_snapshot), float(self._peak_equity),
        )

        _timeout = 10

        def _timeout_handler(signum, frame):
            raise TimeoutError("emergency_stop 超时退出")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_timeout)

        try:
            # ── a. 撤销所有未完成订单（失败不阻断）──────────────────
            for exchange, gw in self._gateways.items():
                if not gw.is_connected():
                    continue
                try:
                    open_orders = gw.get_open_orders(None)
                    for order in open_orders:
                        try:
                            gw.cancel_order(order.order_id, order.inst_id)
                            logger.warning(
                                "EMERGENCY STOP: 撤销订单 | exchange=%s "
                                "order_id=%s inst=%s",
                                exchange.value, order.order_id, order.inst_id,
                            )
                        except Exception:
                            logger.warning(
                                "EMERGENCY STOP: 撤单失败（不阻断）| exchange=%s "
                                "order_id=%s reason=%s",
                                exchange.value, order.order_id, exc_info=True,
                            )
                except Exception:
                    logger.warning(
                        "EMERGENCY STOP: 获取未完成订单失败（不阻断）| exchange=%s",
                        exchange.value, exc_info=True,
                    )

            # ── b. 市价平掉所有持仓（平仓前重新查询）────────────────
            for pos in positions_snapshot:
                if pos.quantity <= 0:
                    continue
                gw = self._gateways.get(pos.exchange)
                if not gw or not gw.is_connected():
                    logger.warning(
                        "EMERGENCY STOP: 无法平仓（Gateway 未连接）| inst=%s", pos.inst_id
                    )
                    continue
                try:
                    current_positions = gw.get_positions(pos.inst_id)
                    current_pos = next(
                        (p for p in current_positions if p.inst_id == pos.inst_id),
                        None,
                    )
                    if not current_pos or current_pos.quantity <= 0:
                        logger.warning(
                            "EMERGENCY STOP: 持仓已清零，跳过平仓 | inst=%s", pos.inst_id
                        )
                        continue
                    close_side = (
                        OrderSide.SELL
                        if current_pos.position_side == PositionSide.LONG
                        else OrderSide.BUY
                    )
                    close_req = OrderRequest(
                        inst_id=pos.inst_id,
                        exchange=pos.exchange,
                        side=close_side,
                        order_type=OrderType.MARKET,
                        quantity=current_pos.quantity,
                        margin_mode=current_pos.margin_mode,
                        position_side=current_pos.position_side,
                    )
                    gw.send_order(close_req)
                    logger.warning(
                        "EMERGENCY STOP: 市价平仓 | inst=%s side=%s qty=%s",
                        pos.inst_id, close_side.value, current_pos.quantity,
                    )
                except Exception:
                    logger.exception(
                        "EMERGENCY STOP: 市价平仓失败 | inst=%s", pos.inst_id
                    )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        # ── c. 暂停策略引擎 ───────────────────────────────────────
        if self._strategy_engine:
            try:
                self._strategy_engine.stop()
                logger.warning("EMERGENCY STOP: 策略引擎已暂停")
            except Exception:
                logger.exception("EMERGENCY STOP: 暂停策略引擎失败")

        # ── d. 标记停止，发布事件 ──────────────────────────────────
        self.loss_limit._is_halted = True
        self.event_bus.publish(
            EventType.RISK_BREACH,
            {
                "emergency": True,
                "reason": reason,
                "action": "emergency_stop",
                "equity": equity,
                "peak_equity": float(self._peak_equity),
                "positions_count": len(positions_snapshot),
            },
            source="risk_engine",
        )
        logger.critical("EMERGENCY STOP 执行完毕")

    # ─────────────────────── 频率限制 ────────────────────────────

    def acquire_trade_quota(self, exchange: Exchange) -> None:
        """获取交易频率配额（下单/撤单前调用）。"""
        limiter = self.rate_limiters.get(exchange)
        if limiter:
            limiter.check_trade()

    def acquire_market_quota(self, exchange: Exchange) -> None:
        """获取行情频率配额。"""
        limiter = self.rate_limiters.get(exchange)
        if limiter:
            limiter.check_market()

    # ─────────────────────── 事件处理 ────────────────────────────

    def _on_tick(self, event: Event) -> None:
        """更新最新价缓存。"""
        tick = event.data
        if hasattr(tick, "inst_id") and hasattr(tick, "last_price"):
            self._current_prices[tick.inst_id] = tick.last_price

    def _on_balance(self, event: Event) -> None:
        """余额更新：刷新权益，并检查最大回撤是否触发 emergency_stop。"""
        balance: BalanceData = event.data
        equity = balance.total_equity

        self.position_limit.update_equity(equity)
        self.loss_limit.update_equity(equity)

        # 更新历史最高净值
        if equity > self._peak_equity:
            self._peak_equity = equity

        # 最大回撤保护（max_drawdown_pct > 0 时启用）
        if self._max_drawdown_pct > 0 and self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown >= self._max_drawdown_pct:
                logger.critical(
                    "账户最大回撤超限 | peak=%.2f current=%.2f "
                    "drawdown=%.2f%% threshold=%.2f%%",
                    float(self._peak_equity), float(equity),
                    float(drawdown * 100),
                    float(self._max_drawdown_pct * 100),
                )
                self.emergency_stop(
                    reason=(
                        f"账户回撤 {float(drawdown):.2%} 超过阈值 "
                        f"{float(self._max_drawdown_pct):.2%}"
                    )
                )

    def _on_position(self, event: Event) -> None:
        """持仓更新：刷新持仓快照，同步更新浮动盈亏监控。"""
        position: PositionData = event.data
        self.position_limit.update_position(position)
        self.loss_limit.update_unrealized_pnl(position)

    def _on_trade(self, event: Event) -> None:
        """成交回报：更新盈亏统计。"""
        from core.models import OrderData
        order: OrderData = event.data
        pnl = order.pnl

        # 构造简化的 TradeData 用于 loss_limit 更新
        from core.models import TradeData as TD
        from utils.helpers import now_utc
        trade = TD(
            trade_id="",
            order_id=order.order_id,
            inst_id=order.inst_id,
            exchange=order.exchange,
            side=order.side,
            price=order.filled_price,
            quantity=order.filled_quantity,
            fee=order.fee,
            fee_ccy="",
            timestamp=now_utc(),
        )
        self.loss_limit.on_trade(trade, pnl)

        # 检查亏损是否触发停止
        if self.loss_limit.is_halted:
            self.event_bus.publish(
                EventType.RISK_BREACH,
                {**self.loss_limit.get_status(), "is_halted": True},
                source="risk_engine",
            )
        elif pnl < 0:
            self.event_bus.publish(
                EventType.RISK_ALERT,
                self.loss_limit.get_status(),
                source="risk_engine",
            )

    # ─────────────────────── 状态查询 ────────────────────────────

    def get_status(self) -> dict:
        """返回风控引擎运行状态摘要。"""
        drawdown = Decimal("0")
        if self._peak_equity > 0:
            equity = self.loss_limit._total_equity
            drawdown = (self._peak_equity - equity) / self._peak_equity

        return {
            "enabled": self.enabled,
            "loss_limit": self.loss_limit.get_status(),
            "current_prices_count": len(self._current_prices),
            "peak_equity": float(self._peak_equity),
            "current_drawdown_pct": float(drawdown),
            "max_drawdown_pct": float(self._max_drawdown_pct),
            "gateways": [e.value for e in self._gateways],
        }
