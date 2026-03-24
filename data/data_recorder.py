"""行情录制器 - 通过 WebSocket 实时录制 K 线和 Tick 到数据库"""

from __future__ import annotations

import logging
from core.event_bus import EventBus, EventType, Event
from data.database import Database

logger = logging.getLogger("data.recorder")


class DataRecorder:
    """
    行情录制器。

    订阅事件总线上的 TICK / BAR 事件，持久化到数据库。
    运行时对数据库写入做批量聚合，减少 I/O 压力。
    写入失败仅记录日志，不影响主流程。
    """

    def __init__(self, event_bus: EventBus, database: Database, batch_size: int = 50):
        """
        Args:
            event_bus:  事件总线
            database:   数据库实例
            batch_size: 批量写入阈值（积累到 batch_size 根后统一入库）
        """
        self.event_bus = event_bus
        self.db = database
        self.batch_size = batch_size

        self._bar_buffer: list = []
        self._tick_buffer: list = []

        self._bar_count = 0
        self._tick_count = 0

    def start(self) -> None:
        """启动录制：订阅行情事件。"""
        self.event_bus.subscribe(EventType.BAR, self._on_bar)
        self.event_bus.subscribe(EventType.TICK, self._on_tick)
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_order)
        self.event_bus.subscribe(EventType.TRADE, self._on_trade)
        logger.info("DataRecorder 已启动")

    def stop(self) -> None:
        """停止录制，刷新所有缓冲区。"""
        self._flush_bars()
        self._flush_ticks()
        self.event_bus.unsubscribe(EventType.BAR, self._on_bar)
        self.event_bus.unsubscribe(EventType.TICK, self._on_tick)
        logger.info(
            "DataRecorder 已停止，共录制 %d 根K线 %d 条Tick",
            self._bar_count, self._tick_count,
        )

    def _on_bar(self, event: Event) -> None:
        try:
            bar = event.data
            self._bar_buffer.append(bar)
            if len(self._bar_buffer) >= self.batch_size:
                self._flush_bars()
        except Exception:
            logger.exception("K线事件处理失败")

    def _on_tick(self, event: Event) -> None:
        """
        实盘 Tick 写入 SQLite。

        使用 INSERT OR IGNORE 天然去重（依赖 tick_data 表的
        (inst_id, exchange, timestamp) 唯一索引）。
        写入失败记录日志，不影响主流程。
        """
        try:
            tick = event.data
            self._tick_buffer.append(tick)
            self._tick_count += 1
            if len(self._tick_buffer) >= self.batch_size:
                self._flush_ticks()
        except Exception:
            logger.exception("Tick 事件处理失败")

    def _on_order(self, event: Event) -> None:
        try:
            self.db.save_order(event.data)
        except Exception:
            logger.exception("订单记录入库失败")

    def _on_trade(self, event: Event) -> None:
        try:
            self.db.save_trade(event.data)
        except Exception:
            logger.exception("成交记录入库失败")

    def _flush_bars(self) -> None:
        if not self._bar_buffer:
            return
        try:
            count = self.db.save_bars(self._bar_buffer)
            self._bar_count += count
            logger.debug("K线入库 %d 根（总计 %d）", count, self._bar_count)
        except Exception:
            logger.exception("K线批量入库失败，缓冲区已丢弃")
        finally:
            self._bar_buffer.clear()

    def _flush_ticks(self) -> None:
        """将 tick_buffer 中的 Tick 批量写入数据库。"""
        if not self._tick_buffer:
            return
        try:
            count = self.db.save_ticks(self._tick_buffer)
            logger.debug("Tick 入库 %d 条", count)
        except Exception:
            logger.exception("Tick 批量入库失败，缓冲区已丢弃")
        finally:
            self._tick_buffer.clear()
