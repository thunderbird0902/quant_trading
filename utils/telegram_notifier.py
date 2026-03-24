"""Telegram 机器人通知器 - 实时推送交易状态、订单、持仓、风险事件"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

logger = logging.getLogger("utils.telegram")

try:
    import requests
except ImportError:
    requests = None


class NotificationLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class TradeNotification:
    inst_id: str
    side: str
    filled_price: Decimal
    filled_quantity: Decimal
    order_id: str
    pnl: Decimal | None
    fee: Decimal
    timestamp: datetime


class TelegramNotifier:
    """
    Telegram Bot 通知器。

    功能：
    - 实时推送开仓/平仓交易
    - 持仓变化通知
    - 账户权益快照（定时）
    - 风控告警/停止
    - 系统状态变化

    使用方式：
        notifier = TelegramNotifier(token="xxx", chat_id="xxx")
        notifier.send_message("策略已启动")
        notifier.notify_trade(trade_data)
    """

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        enabled: bool = True,
        notify_trade: bool = True,
        notify_position: bool = True,
        notify_risk: bool = True,
        notify_equity_interval: int = 300,
    ):
        """
        Args:
            token:        Telegram Bot Token（从 @BotFather 获取）
            chat_id:      接收通知的 Chat ID（从 @userinfobot 获取）
            enabled:      是否启用（False 时静默跳过所有操作）
            notify_trade:      推送交易成交
            notify_position:    推送持仓变化
            notify_risk:        推送风控事件
            notify_equity_interval: 定时推送权益间隔（秒），0=禁用
        """
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)
        self.notify_trade = notify_trade
        self.notify_position = notify_position
        self.notify_risk = notify_risk
        self.notify_equity_interval = notify_equity_interval

        self._last_equity_time: float = 0
        self._lock = threading.Lock()

        if not self.enabled:
            logger.warning("Telegram 通知器未启用（未配置 token 或 chat_id）")
        elif requests is None:
            logger.warning("Telegram 通知器需要 requests 库，请运行 pip install requests")
            self.enabled = False
        else:
            logger.info("Telegram 通知器已初始化")

    # ─────────────────────── 公共接口 ────────────────────────────

    def send_message(
        self,
        text: str,
        level: NotificationLevel = NotificationLevel.INFO,
        parse_mode: str = "Markdown",
    ) -> bool:
        """发送文本消息。"""
        if not self.enabled:
            return False

        if level in (NotificationLevel.WARNING, NotificationLevel.ERROR, NotificationLevel.CRITICAL):
            emoji = {"WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}[level.value]
            text = f"{emoji} {text}"

        return self._send(text, parse_mode)

    def notify_trade_opened(
        self,
        inst_id: str,
        side: str,
        price: Decimal,
        qty: Decimal,
        order_id: str,
        entry_price: Decimal | None = None,
    ) -> bool:
        """推送开仓通知。"""
        if not self.enabled or not self.notify_trade:
            return False

        direction = "🟢 做多" if side.upper() in ("BUY", "LONG") else "🔴 做空"
        entry_info = f"\n入场价: `{entry_price}`" if entry_price else ""

        text = (
            f"📈 *开仓通知*\n"
            f"品种: `{inst_id}`\n"
            f"{direction}\n"
            f"数量: `{qty}`\n"
            f"价格: `{price}`\n"
            f"订单: `{order_id}`{entry_info}\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text)

    def notify_trade_closed(
        self,
        inst_id: str,
        side: str,
        price: Decimal,
        qty: Decimal,
        order_id: str,
        pnl: Decimal,
        pnl_pct: float | None = None,
        realized_pnl: Decimal | None = None,
    ) -> bool:
        """推送平仓通知。"""
        if not self.enabled or not self.notify_trade:
            return False

        direction = "🔴 平多" if side.upper() in ("SELL", "LONG") else "🟢 平空"
        pnl_str = f"`{float(pnl):+.2f}`"
        if pnl >= 0:
            pnl_display = f"💰 盈利: {pnl_str}"
        else:
            pnl_display = f"💸 亏损: {pnl_str}"

        pnl_pct_info = f" ({pnl_pct:+.2%})" if pnl_pct is not None else ""
        realized_info = f"\n已实现盈亏: `{realized_pnl}`" if realized_pnl is not None else ""

        text = (
            f"📉 *平仓通知*\n"
            f"品种: `{inst_id}`\n"
            f"{direction}\n"
            f"数量: `{qty}`\n"
            f"价格: `{price}`\n"
            f"订单: `{order_id}`\n"
            f"{pnl_display}{pnl_pct_info}{realized_info}\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text, level=NotificationLevel.INFO if pnl >= 0 else NotificationLevel.WARNING)

    def notify_position_update(
        self,
        inst_id: str,
        pos_qty: Decimal,
        avg_price: Decimal,
        unrealized_pnl: Decimal,
        mark_price: Decimal | None = None,
    ) -> bool:
        """推送持仓变化通知。"""
        if not self.enabled or not self.notify_position:
            return False

        mark_info = f"\n标记价: `{mark_price}`" if mark_price else ""
        unrealized_str = f"`{float(unrealized_pnl):+.2f}`"
        if unrealized_pnl >= 0:
            upnl_display = f"🟢 浮动盈亏: +{unrealized_str}"
        else:
            upnl_display = f"🔴 浮动盈亏: {unrealized_str}"

        text = (
            f"📊 *持仓更新*\n"
            f"品种: `{inst_id}`\n"
            f"持仓量: `{pos_qty}`\n"
            f"均价: `{avg_price}`\n"
            f"{upnl_display}{mark_info}\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text)

    def notify_risk_alert(
        self,
        rule: str,
        reason: str,
        action: str = "alert",
    ) -> bool:
        """推送风控告警。"""
        if not self.enabled or not self.notify_risk:
            return False

        action_display = {"alert": "⚠️ 告警", "rejected": "🚫 拒绝", "stopped": "🛑 已停止"}.get(action, action)

        text = (
            f"🚨 *风控{action_display}*\n"
            f"规则: `{rule}`\n"
            f"原因: {reason}\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text, level=NotificationLevel.WARNING)

    def notify_risk_breach(
        self,
        reason: str,
        equity: float,
        positions_count: int,
    ) -> bool:
        """推送风控突破（Emergency Stop）。"""
        if not self.enabled or not self.notify_risk:
            return False

        text = (
            f"🛑 *Emergency Stop 触发*\n"
            f"原因: {reason}\n"
            f"当前权益: `{equity:.2f}`\n"
            f"持仓数: `{positions_count}`\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC')"
        )
        return self.send_message(text, level=NotificationLevel.CRITICAL)

    def notify_equity(
        self,
        total_equity: Decimal,
        daily_pnl: Decimal,
        unrealized_pnl: Decimal,
        positions: list[dict],
    ) -> bool:
        """推送权益快照（可定时调用）。"""
        if not self.enabled:
            return False

        daily_str = f"`{float(daily_pnl):+.2f}`"
        unrealized_str = f"`{float(unrealized_pnl):+.2f}`"

        pos_lines = []
        for pos in positions:
            pnl = pos.get("unrealized_pnl", Decimal("0"))
            pnl_str = f"{float(pnl):+.2f}" if isinstance(pnl, Decimal) else f"{pnl:+.2f}"
            pos_lines.append(
                f"  {pos['inst_id']}: qty={pos['qty']} upnl={pnl_str}"
            )
        pos_text = "\n".join(pos_lines) if pos_lines else "  无持仓"

        text = (
            f"💼 *账户权益快照*\n"
            f"总权益: `{float(total_equity):,.2f}`\n"
            f"今日盈亏: {daily_str}\n"
            f"浮动盈亏: {unrealized_str}\n"
            f"---\n"
            f"{pos_text}\n"
            f"---\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text)

    def notify_strategy_status(
        self,
        strategy_name: str,
        status: str,
        inst_id: str | None = None,
        pos: Decimal | None = None,
        equity: Decimal | None = None,
    ) -> bool:
        """推送策略状态变化。"""
        if not self.enabled:
            return False

        inst_info = f"\n品种: `{inst_id}`" if inst_id else ""
        pos_info = f"\n持仓: `{pos}`" if pos is not None else ""
        equity_info = f"\n权益: `{float(equity):,.2f}`" if equity is not None else ""

        text = (
            f"▶️ *策略状态*\n"
            f"策略: `{strategy_name}`\n"
            f"状态: `{status}`{inst_info}{pos_info}{equity_info}\n"
            f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send_message(text)

    # ─────────────────────── 内部方法 ────────────────────────────

    def _send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """调用 Telegram Bot API 发送消息。"""
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            if result.get("ok"):
                return True
            logger.warning("Telegram 发送失败: %s", result.get("description"))
            return False
        except Exception as e:
            logger.warning("Telegram 请求异常: %s", e)
            return False

    def should_notify_equity(self) -> bool:
        """判断是否应该推送定时权益（受时间间隔控制）。"""
        if not self.enabled or self.notify_equity_interval <= 0:
            return False
        import time
        now = time.time()
        if now - self._last_equity_time >= self.notify_equity_interval:
            self._last_equity_time = now
            return True
        return False
