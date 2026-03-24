"""主引擎 - 系统入口，负责初始化和生命周期管理"""

from __future__ import annotations

import logging
import signal
import sys
from decimal import Decimal
from typing import TYPE_CHECKING

from .enums import Exchange, MarketType
from .event_bus import EventBus, EventType
from .exceptions import ConfigError, GatewayError
from .logger import setup_logging
from .models import (
    BalanceData, BarData, FeeRate, FundingRateData,
    Instrument, OrderBook, OrderData, OrderRequest,
    PositionData, TickData, TradeData, TransferRequest,
)

if TYPE_CHECKING:
    from gateway.base_gateway import BaseGateway
    from risk.risk_engine import RiskEngine
    from strategy_core.strategy_engine import StrategyEngine
    from utils.telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class MainEngine:
    """
    量化交易主引擎。

    职责：
    1. 加载配置并初始化日志
    2. 管理 Gateway 生命周期（注册、连接、断开）
    3. 初始化风控引擎、策略引擎
    4. 提供统一的跨市场 API（行情、交易、账户）
    5. 跨市场数据汇总（总权益、全市场持仓）
    """

    def __init__(self, config: dict | None = None):
        """
        Args:
            config: 从 settings.yaml 加载的配置字典。
                    None 时使用默认配置。
        """
        self._config = config or {}
        self._gateways: dict[Exchange, "BaseGateway"] = {}
        self._risk_engine: "RiskEngine | None" = None
        self._strategy_engine: "StrategyEngine | None" = None
        self._running: bool = False

        # 初始化事件总线
        self.event_bus = EventBus()

        # 初始化日志
        log_cfg = self._config.get("system", {})
        setup_logging(
            log_level=log_cfg.get("log_level", "INFO"),
        )

        # Telegram 通知器
        self._telegram_notifier: "TelegramNotifier | None" = None
        self._init_telegram(self._config.get("telegram"))

        logger.info("MainEngine 初始化完成")

    def _init_telegram(self, telegram_cfg: dict | None) -> None:
        """初始化 Telegram 通知器。"""
        if not telegram_cfg or not telegram_cfg.get("enabled"):
            return
        try:
            from utils.telegram_notifier import TelegramNotifier
            self._telegram_notifier = TelegramNotifier(
                token=telegram_cfg.get("bot_token"),
                chat_id=telegram_cfg.get("chat_id"),
                enabled=telegram_cfg.get("enabled", True),
                notify_trade=telegram_cfg.get("notify_trade", True),
                notify_position=telegram_cfg.get("notify_position", True),
                notify_risk=telegram_cfg.get("notify_risk", True),
                notify_equity_interval=telegram_cfg.get("equity_interval", 300),
            )
            if self._telegram_notifier.enabled:
                self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
                self.event_bus.subscribe(EventType.POSITION_UPDATED, self._on_position_updated)
                self.event_bus.subscribe(EventType.RISK_BREACH, self._on_risk_breach)
                self.event_bus.subscribe(EventType.RISK_ALERT, self._on_risk_alert)
                self._telegram_notifier.send_message("🚀 *系统启动* - Telegram 通知已连接")
        except Exception:
            logger.exception("Telegram 通知器初始化失败")

    # ─────────────────────── Gateway 管理 ────────────────────────

    def add_gateway(self, gateway: "BaseGateway") -> None:
        """
        注册 Gateway。

        Args:
            gateway: 已实例化的 gateway 对象（内部持有 event_bus 引用）
        """
        exchange = gateway.exchange
        if exchange in self._gateways:
            logger.warning("Gateway %s 已注册，将覆盖旧实例", exchange.value)
        self._gateways[exchange] = gateway
        logger.info("已注册 Gateway: %s", exchange.value)

    def get_gateway(self, exchange: Exchange) -> "BaseGateway":
        """获取指定市场的 Gateway。"""
        gw = self._gateways.get(exchange)
        if gw is None:
            raise ConfigError(f"未注册 Gateway: {exchange.value}")
        return gw

    def connect(self, exchange: Exchange) -> None:
        """连接指定市场。"""
        gw = self.get_gateway(exchange)
        gw.connect()
        logger.info("Gateway %s 连接成功", exchange.value)

    def connect_all(self) -> None:
        """连接所有已注册的 Gateway。"""
        for exchange, gw in self._gateways.items():
            try:
                gw.connect()
                logger.info("Gateway %s 连接成功", exchange.value)
            except Exception:
                logger.exception("Gateway %s 连接失败", exchange.value)

    def disconnect(self, exchange: Exchange) -> None:
        """断开指定市场。"""
        gw = self.get_gateway(exchange)
        gw.disconnect()
        logger.info("Gateway %s 已断开", exchange.value)

    def disconnect_all(self) -> None:
        """断开所有 Gateway。"""
        for exchange, gw in list(self._gateways.items()):
            try:
                gw.disconnect()
            except Exception:
                logger.exception("Gateway %s 断开失败", exchange.value)

    # ─────────────────────── 行情接口 ────────────────────────────

    def get_instruments(self, exchange: Exchange, market_type: MarketType) -> list[Instrument]:
        """获取产品列表。"""
        return self.get_gateway(exchange).get_instruments(market_type)

    def get_ticker(self, exchange: Exchange, inst_id: str) -> TickData:
        """获取单一产品行情。"""
        return self.get_gateway(exchange).get_ticker(inst_id)

    def get_klines(
        self,
        exchange: Exchange,
        inst_id: str,
        interval: str,
        limit: int = 100,
    ) -> list[BarData]:
        """获取 K 线数据。"""
        return self.get_gateway(exchange).get_klines(inst_id, interval, limit)

    def get_orderbook(self, exchange: Exchange, inst_id: str, depth: int = 20) -> OrderBook:
        """获取订单簿深度。"""
        return self.get_gateway(exchange).get_orderbook(inst_id, depth)

    def subscribe(self, exchange: Exchange, inst_id: str) -> None:
        """订阅行情推送（Tick）。"""
        self.get_gateway(exchange).subscribe_ticker(inst_id)

    # ─────────────────────── 账户接口 ────────────────────────────

    def get_balance(self, exchange: Exchange) -> BalanceData:
        """获取账户余额。"""
        return self.get_gateway(exchange).get_balance()

    def get_positions(
        self,
        exchange: Exchange,
        inst_id: str | None = None,
    ) -> list[PositionData]:
        """获取持仓列表。"""
        return self.get_gateway(exchange).get_positions(inst_id)

    def get_fee_rate(self, exchange: Exchange, inst_id: str) -> FeeRate:
        """获取手续费费率。"""
        return self.get_gateway(exchange).get_fee_rate(inst_id)

    # ─────────────────────── 交易接口 ────────────────────────────

    def send_order(self, request: OrderRequest) -> OrderData:
        """
        发送订单（经过风控检查后转发给对应 Gateway）。

        Returns:
            已提交的 OrderData
        """
        gw = self.get_gateway(request.exchange)
        if not gw.is_connected():
            raise GatewayError(
                f"Gateway {request.exchange.value} 未连接，无法下单"
            )

        if self._risk_engine and self._risk_engine.enabled:
            self._risk_engine.check_order(request)  # 不通过则抛出异常
        else:
            # FIX: [无风控时发出明确警告——生产环境必须启用风控引擎]
            logger.warning(
                "FIX-WARN: 订单跳过风控检查 | inst=%s side=%s qty=%s price=%s "
                "(risk_engine=%s)",
                request.inst_id, request.side.value,
                request.quantity, request.price,
                "disabled" if (self._risk_engine and not self._risk_engine.enabled)
                else "not_set",
            )

        order = gw.send_order(request)
        self.event_bus.publish(EventType.ORDER_SUBMITTED, order, source="main_engine")
        return order

    def cancel_order(self, exchange: Exchange, order_id: str, inst_id: str) -> bool:
        """撤销订单。"""
        return self.get_gateway(exchange).cancel_order(order_id, inst_id)

    def modify_order(
        self,
        exchange: Exchange,
        order_id: str,
        inst_id: str,
        new_price: Decimal | None = None,
        new_quantity: Decimal | None = None,
    ) -> bool:
        """修改订单价格/数量。"""
        return self.get_gateway(exchange).modify_order(
            order_id, inst_id, new_price, new_quantity
        )

    def get_order(self, exchange: Exchange, order_id: str, inst_id: str) -> OrderData:
        """查询单笔订单。"""
        return self.get_gateway(exchange).get_order(order_id, inst_id)

    def get_open_orders(self, exchange: Exchange, inst_id: str | None = None) -> list[OrderData]:
        """查询未完成订单。"""
        return self.get_gateway(exchange).get_open_orders(inst_id)

    def transfer(self, exchange: Exchange, request: TransferRequest) -> bool:
        """资金划转。"""
        return self.get_gateway(exchange).transfer(request)

    # ─────────────────────── 跨市场汇总 ──────────────────────────

    def get_all_positions(self) -> dict[Exchange, list[PositionData]]:
        """
        获取所有已连接市场的持仓，按 Exchange 分组。

        Returns:
            {Exchange.OKX: [...], Exchange.IB: [...], ...}
        """
        result: dict[Exchange, list[PositionData]] = {}
        for exchange, gw in self._gateways.items():
            if gw.is_connected():
                try:
                    result[exchange] = gw.get_positions(None)
                except Exception:
                    logger.exception("获取 %s 持仓失败", exchange.value)
                    result[exchange] = []
        return result

    def get_total_equity(self) -> Decimal:
        """
        获取所有市场总权益（USD 计价）。

        Returns:
            各市场 total_equity 求和
        """
        total = Decimal("0")
        for exchange, gw in self._gateways.items():
            if gw.is_connected():
                try:
                    balance = gw.get_balance()
                    total += balance.total_equity
                except Exception:
                    logger.exception("获取 %s 余额失败", exchange.value)
        return total

    # ─────────────────────── 引擎模块 ────────────────────────────

    def set_risk_engine(self, risk_engine: "RiskEngine") -> None:
        """注入风控引擎。"""
        self._risk_engine = risk_engine
        logger.info("风控引擎已注入")

    def set_strategy_engine(self, strategy_engine: "StrategyEngine") -> None:
        """注入策略引擎。"""
        self._strategy_engine = strategy_engine
        logger.info("策略引擎已注入")

    # ─────────────────────── Telegram 事件处理 ──────────────────

    def _on_order_filled(self, event: "Event") -> None:
        """ORDER_FILLED 事件 → Telegram 推送成交信息。"""
        if not self._telegram_notifier or not self._telegram_notifier.enabled:
            return
        order: OrderData = event.data
        if order.filled_quantity <= 0:
            return

        from core.enums import OrderSide
        side_str = order.side.value.upper()
        is_close = order.pnl != 0

        if is_close:
            self._telegram_notifier.notify_trade_closed(
                inst_id=order.inst_id,
                side=side_str,
                price=order.filled_price,
                qty=order.filled_quantity,
                order_id=order.order_id,
                pnl=order.pnl,
            )
        else:
            self._telegram_notifier.notify_trade_opened(
                inst_id=order.inst_id,
                side=side_str,
                price=order.filled_price,
                qty=order.filled_quantity,
                order_id=order.order_id,
            )

    def _on_position_updated(self, event: "Event") -> None:
        """POSITION_UPDATED 事件 → Telegram 推送持仓变化。"""
        if not self._telegram_notifier or not self._telegram_notifier.enabled:
            return
        pos: PositionData = event.data
        self._telegram_notifier.notify_position_update(
            inst_id=pos.inst_id,
            pos_qty=pos.quantity,
            avg_price=pos.avg_price,
            unrealized_pnl=pos.unrealized_pnl,
            mark_price=pos.mark_price if hasattr(pos, "mark_price") and pos.mark_price else None,
        )

    def _on_risk_breach(self, event: "Event") -> None:
        """RISK_BREACH 事件 → Telegram 推送紧急停止。"""
        if not self._telegram_notifier or not self._telegram_notifier.enabled:
            return
        data = event.data or {}
        from utils.telegram_notifier import NotificationLevel
        level = NotificationLevel.CRITICAL if data.get("emergency") else NotificationLevel.ERROR
        self._telegram_notifier.send_message(
            f"🛑 *风控突破*\n原因: {data.get('reason', 'Unknown')}\n"
            f"权益: {data.get('equity', 'N/A')}\n"
            f"持仓数: {data.get('positions_count', 0)}",
            level=level,
        )

    def _on_risk_alert(self, event: "Event") -> None:
        """RISK_ALERT 事件 → Telegram 推送告警。"""
        if not self._telegram_notifier or not self._telegram_notifier.enabled:
            return
        data = event.data or {}
        self._telegram_notifier.notify_risk_alert(
            rule=data.get("rule", "Unknown"),
            reason=data.get("reason", ""),
            action="alert",
        )

    # ─────────────────────── 生命周期 ────────────────────────────

    # ─────────────────────── 自检 ────────────────────────────────

    def _preflight_check(self) -> None:
        """
        启动自检：配置完整性 → 至少一个 Gateway 已注册。

        任何检查失败直接抛出 ConfigError，拒绝启动。
        """
        errors: list[str] = []

        if not self._gateways:
            errors.append("未注册任何 Gateway，请先调用 add_gateway()")

        if errors:
            msg = "启动自检失败：\n  " + "\n  ".join(errors)
            logger.error(msg)
            raise ConfigError(msg)

        logger.info("启动自检通过 | gateways=%s",
                    [e.value for e in self._gateways])

    def _post_connect_check(self) -> None:
        """
        连接后检查：验证每个 Gateway 是否成功连接。

        记录未连接的 Gateway 但不中断启动（部分降级运行）。
        """
        disconnected = [
            e.value for e, gw in self._gateways.items()
            if not gw.is_connected()
        ]
        if disconnected:
            logger.warning("以下 Gateway 连接未就绪，功能受限: %s", disconnected)

    # ─────────────────────── 信号处理 ────────────────────────────

    def _register_signal_handlers(self) -> None:
        """注册 SIGINT / SIGTERM，触发优雅退出。"""
        def _handle_signal(signum, _frame):
            sig_name = signal.Signals(signum).name
            logger.warning("收到信号 %s，开始优雅退出...", sig_name)
            self.stop()
            sys.exit(0)

        # Windows 不支持 SIGTERM，忽略注册失败
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (OSError, ValueError):
                pass

    # ─────────────────────── 生命周期 ────────────────────────────

    def start(self) -> None:
        """启动引擎（连接所有 Gateway）。"""
        if self._running:
            logger.warning("MainEngine 已在运行，忽略重复启动")
            return

        logger.info("MainEngine 启动中...")

        # 1. 启动自检
        self._preflight_check()

        # 2. 注册信号处理
        self._register_signal_handlers()

        # 3. 连接所有 Gateway
        self.connect_all()

        # 4. 连接后验证
        self._post_connect_check()

        # 5. 启动风控 / 策略引擎
        if self._risk_engine:
            self._risk_engine.start()
        if self._strategy_engine:
            self._strategy_engine.start()

        # FIX: [重启后 risk_engine 和 strategy_engine 仓位/余额状态为空，需从交易所同步]
        self._sync_state_from_gateways()

        # FIX: [启动时打印关键配置，方便确认是否误用实盘/回测参数]
        self._print_startup_config()

        self._running = True
        logger.info("MainEngine 启动完成")

    def _sync_state_from_gateways(self) -> None:
        """
        FIX: 从已连接的 Gateway 查询当前仓位和余额，通过事件总线广播。

        解决问题：进程重启后 risk_engine._positions / strategy_engine._positions 为空，
        导致风控检查错误（低估已有仓位）、策略看不到持仓。
        通过 POSITION_UPDATED + BALANCE_UPDATED 事件统一驱动状态更新。
        """
        for exchange, gw in self._gateways.items():
            if not gw.is_connected():
                continue
            try:
                positions = gw.get_positions(None)
                for pos in positions:
                    self.event_bus.publish(
                        EventType.POSITION_UPDATED, pos, source="startup_sync"
                    )
                balance = gw.get_balance()
                self.event_bus.publish(
                    EventType.BALANCE_UPDATED, balance, source="startup_sync"
                )
                logger.info(
                    "启动状态同步完成 | exchange=%s positions=%d equity=%.2f",
                    exchange.value, len(positions), float(balance.total_equity),
                )
            except Exception:
                logger.exception("启动状态同步失败 | exchange=%s", exchange.value)

    def _print_startup_config(self) -> None:
        """
        FIX: 启动时打印关键配置，让运维人员确认参数，防止回测/实盘配置混用。
        """
        sys_cfg = self._config.get("system", {})
        risk_cfg = self._config.get("risk", {})
        gw_cfg = self._config.get("gateways", {})
        okx_flag = gw_cfg.get("okx", {}).get("flag", "?")
        mode_label = "【模拟盘】" if okx_flag == "1" else "【实  盘】"

        lines = [
            "=" * 55,
            f"  MainEngine 启动确认  {mode_label}",
            "=" * 55,
            f"  运行模式   : {sys_cfg.get('mode', 'N/A')}",
            f"  日志级别   : {sys_cfg.get('log_level', 'INFO')}",
            f"  OKX flag   : {okx_flag}  (0=实盘  1=模拟盘)",
            f"  风控启用   : {risk_cfg.get('enabled', True)}",
            f"  日亏损上限 : {float(risk_cfg.get('max_daily_loss_pct', 0.05)) * 100:.1f}%",
            f"  单品种仓位 : {float(risk_cfg.get('max_position_pct', 0.3)) * 100:.1f}%",
            f"  最大回撤   : {float(risk_cfg.get('max_drawdown_pct', 0.0)) * 100:.1f}%",
            f"  Gateways   : {[e.value for e in self._gateways]}",
            "=" * 55,
        ]
        for line in lines:
            logger.info(line)

    def stop(self) -> None:
        """
        优雅停止引擎。

        顺序：
        1. 停止策略引擎（停止生成新信号）
        2. 撤销所有未完成订单
        3. 断开所有 Gateway
        4. 停止风控引擎
        """
        if not self._running:
            logger.warning("MainEngine 未在运行，忽略停止")
            return

        logger.info("MainEngine 停止中...")

        # 1. 停止策略引擎
        if self._strategy_engine:
            try:
                self._strategy_engine.stop()
                logger.info("策略引擎已停止")
            except Exception:
                logger.exception("策略引擎停止时异常")

        # 2. 撤销所有 Gateway 中的未完成订单
        for exchange, gw in list(self._gateways.items()):
            if not gw.is_connected():
                continue
            try:
                open_orders = gw.get_open_orders(None)
                for order in open_orders:
                    try:
                        gw.cancel_order(order.order_id, order.inst_id)
                        logger.info(
                            "撤销未完成订单 | exchange=%s order_id=%s inst_id=%s",
                            exchange.value, order.order_id, order.inst_id,
                        )
                    except Exception:
                        logger.exception(
                            "撤单失败 | exchange=%s order_id=%s",
                            exchange.value, order.order_id,
                        )
            except Exception:
                logger.exception("获取 %s 未完成订单失败", exchange.value)

        # 3. 断开所有 Gateway
        self.disconnect_all()

        # 4. 停止风控引擎
        if self._risk_engine:
            try:
                self._risk_engine.stop()
                logger.info("风控引擎已停止")
            except Exception:
                logger.exception("风控引擎停止时异常")

        self._running = False
        logger.info("MainEngine 已停止")

    def __enter__(self) -> "MainEngine":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
