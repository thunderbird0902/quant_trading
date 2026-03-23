"""
strategy_core/bar_generator.py
================================
BarGenerator — Tick → K 线 流式聚合器。

设计目标
--------
1. 接收实时 Tick，聚合为 1 分钟 K 线，再聚合为更大周期（N 分、小时、日）。
2. 支持断线重连场景：成交量按差值计算（volume delta），避免重复累加。
3. 纯事件驱动：每根 K 线完成后通过回调通知策略，与框架解耦。

支持周期
--------
- 分钟级：1m / 2m / 3m / 5m / 10m / 15m / 30m
- 小时级：1H / 2H / 4H / 6H / 12H
- 日线：  1D

使用示例
--------
    from strategy_core.bar_generator import BarGenerator
    from strategy_core.array_manager import ArrayManager

    class MyStrategy(BaseStrategy):
        def __init__(self, ...):
            ...
            # 1 分钟完成回调 → 5 分钟聚合
            self.bg = BarGenerator(
                on_bar=self._on_1m_bar,
                interval=5,
                on_window_bar=self._on_5m_bar,
                interval_unit="m",
            )
            self.am = ArrayManager(size=100)

        def on_tick(self, tick: TickData) -> None:
            self.bg.update_tick(tick)

        def _on_1m_bar(self, bar: BarData) -> None:
            # 1 分钟 bar 完成时自动触发，同时驱动窗口聚合
            pass

        def _on_5m_bar(self, bar: BarData) -> None:
            self.am.update_bar(bar)
            if not self.am.inited:
                return
            ...  # 你的策略逻辑
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from core.enums import Exchange
from core.models import BarData, TickData

logger = logging.getLogger(__name__)

# 支持的小时周期
_HOUR_MULTIPLES = {1, 2, 4, 6, 12}
# 支持的分钟倍数（> 1 分钟）
_MINUTE_MULTIPLES = {2, 3, 5, 10, 15, 30}


class BarGenerator:
    """
    Tick → 1 分钟 K 线，再聚合到更大窗口（N 分、小时、日线）。

    Parameters
    ----------
    on_bar : Callable[[BarData], None]
        **1 分钟** K 线完成时的回调（必填）。
    interval : int
        窗口聚合倍数（配合 interval_unit 使用）。默认 1（不再做二次聚合）。
    on_window_bar : Callable[[BarData], None] | None
        窗口 K 线完成时的回调。仅当 interval > 1 或 interval_unit != "m" 时有效。
    interval_unit : str
        窗口单位，可选 "m"（分）/ "H"（小时）/ "D"（日线）。默认 "m"。
    """

    def __init__(
        self,
        on_bar: Callable[[BarData], None],
        interval: int = 1,
        on_window_bar: Callable[[BarData], None] | None = None,
        interval_unit: str = "m",
    ) -> None:
        self.on_bar        = on_bar
        self.interval      = interval
        self.on_window_bar = on_window_bar
        self.interval_unit = interval_unit.upper() if interval_unit != "m" else "m"

        # 当前 1 分钟 bar（未完成）
        self._bar: BarData | None = None
        # 窗口聚合 bar（未完成）
        self._window_bar: BarData | None = None

        # 用于 volume delta 计算（处理断线重连）
        self._last_volume: Decimal = Decimal("0")

        # 验证参数
        self._validate()

    def _validate(self) -> None:
        unit = self.interval_unit
        n    = self.interval
        if unit == "m":
            if n > 1 and n not in _MINUTE_MULTIPLES:
                logger.warning(
                    "interval=%d m 不是标准分钟倍数 %s，仍会聚合但请确认周期正确",
                    n, sorted(_MINUTE_MULTIPLES),
                )
        elif unit == "H":
            if n not in _HOUR_MULTIPLES:
                logger.warning(
                    "interval=%d H 不是标准小时倍数 %s，仍会聚合", n, sorted(_HOUR_MULTIPLES)
                )
        elif unit == "D":
            if n != 1:
                logger.warning("日线聚合仅支持 interval=1，当前 %d 将被忽略", n)

    # ──────────────────────────────── 主入口 ─────────────────────────────────

    def update_tick(self, tick: TickData) -> None:
        """接收一个 Tick 并更新当前 1 分钟 bar。"""
        ts   = tick.timestamp
        # 取整到分钟（K 线开始时间）
        bar_dt = ts.replace(second=0, microsecond=0)

        if self._bar is None:
            # 第一个 Tick，建新 bar
            self._bar = self._new_bar_from_tick(tick, bar_dt)
            self._last_volume = tick.volume_24h
            return

        # 同一分钟内：更新 high/low/close/volume
        if bar_dt == self._bar.timestamp:
            self._update_bar_tick(tick)
        else:
            # 跨分钟：上一根 bar 完成，推送后开新 bar
            self._finish_1m_bar()
            self._bar = self._new_bar_from_tick(tick, bar_dt)
            self._last_volume = tick.volume_24h

    def update_bar(self, bar: BarData) -> None:
        """
        直接喂入已完成的 1 分钟 K 线（用于回放或 websocket 推送场景）。

        此时不再做 1 分钟聚合，直接驱动窗口聚合并触发 on_bar 回调。
        """
        # 触发 on_bar（1 分钟回调）
        self.on_bar(bar)
        # 驱动窗口聚合
        if self.on_window_bar and (self.interval > 1 or self.interval_unit != "m"):
            self._update_window_bar(bar)

    # ──────────────────────────────── 内部 ──────────────────────────────────

    def _new_bar_from_tick(self, tick: TickData, bar_dt: datetime) -> BarData:
        price = tick.last_price
        return BarData(
            inst_id    = tick.inst_id,
            exchange   = tick.exchange,
            interval   = "1m",
            open       = price,
            high       = price,
            low        = price,
            close      = price,
            volume     = Decimal("0"),
            volume_ccy = Decimal("0"),
            timestamp  = bar_dt,
        )

    def _update_bar_tick(self, tick: TickData) -> None:
        """同一分钟内用 Tick 更新当前 bar。"""
        bar = self._bar
        if bar is None:
            return
        price = tick.last_price
        bar.high  = max(bar.high,  price)
        bar.low   = min(bar.low,   price)
        bar.close = price

        # Volume delta：当前快照量 - 上一次记录量，避免重连重置导致重复累加
        vol_delta = tick.volume_24h - self._last_volume
        if vol_delta > Decimal("0"):
            bar.volume += vol_delta
        self._last_volume = tick.volume_24h

    def _finish_1m_bar(self) -> None:
        """将当前未完成 bar 标记为已完成并推送。"""
        if self._bar is None:
            return
        bar = self._bar
        # 触发 on_bar 回调
        self.on_bar(bar)
        # 驱动窗口聚合
        if self.on_window_bar and (self.interval > 1 or self.interval_unit != "m"):
            self._update_window_bar(bar)
        self._bar = None

    def _update_window_bar(self, bar: BarData) -> None:
        """将 1 分钟 bar 聚合到窗口 bar，满足条件后推送并重置。"""
        if self._window_bar is None:
            interval_str = (
                f"{self.interval}{self.interval_unit}"
                if self.interval_unit in ("m", "H")
                else "1D"
            )
            self._window_bar = BarData(
                inst_id    = bar.inst_id,
                exchange   = bar.exchange,
                interval   = interval_str,
                open       = bar.open,
                high       = bar.high,
                low        = bar.low,
                close      = bar.close,
                volume     = bar.volume,
                volume_ccy = bar.volume_ccy,
                timestamp  = bar.timestamp,
            )
        else:
            wb = self._window_bar
            wb.high   = max(wb.high,   bar.high)
            wb.low    = min(wb.low,    bar.low)
            wb.close  = bar.close
            wb.volume     += bar.volume
            wb.volume_ccy += bar.volume_ccy

        # 判断窗口是否完成
        if self._is_window_finished(bar):
            self.on_window_bar(self._window_bar)  # type: ignore[misc]
            self._window_bar = None

    def _is_window_finished(self, bar: BarData) -> bool:
        """根据 interval_unit 和 interval 判断窗口 bar 是否可以收盘。"""
        ts = bar.timestamp
        unit = self.interval_unit
        n    = self.interval

        if unit == "m":
            # 分钟倍数：当 (分钟数 + 1) 是 n 的整数倍时，本根 bar 是窗口最后一根
            return (ts.minute + 1) % n == 0

        if unit == "H":
            # 小时倍数：以小时为单位判断
            # 例：4H 窗口 → 当小时 % 4 == 3 且分钟 == 59 时收盘
            return ts.minute == 59 and (ts.hour + 1) % n == 0

        if unit == "D":
            # 日线：每天最后一分钟（23:59 UTC）收盘
            return ts.hour == 23 and ts.minute == 59

        return False

    # ──────────────────────────────── 强制 flush ─────────────────────────────

    def finish(self) -> None:
        """
        强制结束当前未完成的 bar（回测结束时调用，确保最后一根 bar 被处理）。
        """
        if self._bar is not None:
            self._finish_1m_bar()
        if self._window_bar is not None and self.on_window_bar:
            self.on_window_bar(self._window_bar)
            self._window_bar = None

    def __repr__(self) -> str:
        return (
            f"BarGenerator(interval={self.interval}{self.interval_unit}, "
            f"has_bar={self._bar is not None}, "
            f"has_window={self._window_bar is not None})"
        )
