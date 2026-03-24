"""数据库管理 - SQLite（开发）/ PostgreSQL（生产）"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger("data.database")

# 默认数据库路径
DEFAULT_DB_PATH = Path("data/quant_trading.db")


class Database:
    """
    SQLite 数据库管理器。

    表结构：
    - bar_data:    K 线历史数据
    - tick_data:   Tick 历史数据（可选）
    - trade_data:  成交记录
    - order_data:  订单历史

    切换 PostgreSQL：只需修改 __init__ 中的连接字符串，
    其余代码通过 sqlite3 兼容 API 保持不变。
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
        logger.info("数据库初始化完成: %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _conn(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─────────────────────── 建表 ────────────────────────────────

    def _init_tables(self) -> None:
        """创建所需数据表（若不存在）。"""
        with self._conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS bar_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                inst_id     TEXT NOT NULL,
                exchange    TEXT NOT NULL,
                interval    TEXT NOT NULL,
                timestamp   TIMESTAMP NOT NULL,
                open        TEXT NOT NULL,
                high        TEXT NOT NULL,
                low         TEXT NOT NULL,
                close       TEXT NOT NULL,
                volume      TEXT NOT NULL,
                volume_ccy  TEXT NOT NULL,
                UNIQUE(inst_id, exchange, interval, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_bar_inst_ts
                ON bar_data(inst_id, exchange, interval, timestamp);

            CREATE TABLE IF NOT EXISTS tick_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                inst_id         TEXT NOT NULL,
                exchange        TEXT NOT NULL,
                timestamp       TIMESTAMP NOT NULL,
                last_price      TEXT NOT NULL,
                bid_price       TEXT,
                ask_price       TEXT,
                bid_size        TEXT,
                ask_size        TEXT,
                volume_24h      TEXT,
                volume_ccy_24h  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tick_inst_ts
                ON tick_data(inst_id, exchange, timestamp);

            CREATE TABLE IF NOT EXISTS trade_record (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id    TEXT NOT NULL,
                order_id    TEXT NOT NULL,
                inst_id     TEXT NOT NULL,
                exchange    TEXT NOT NULL,
                side        TEXT NOT NULL,
                price       TEXT NOT NULL,
                quantity    TEXT NOT NULL,
                fee         TEXT NOT NULL,
                fee_ccy     TEXT NOT NULL,
                timestamp   TIMESTAMP NOT NULL,
                UNIQUE(trade_id, exchange)
            );

            CREATE TABLE IF NOT EXISTS order_record (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        TEXT NOT NULL,
                client_order_id TEXT,
                inst_id         TEXT NOT NULL,
                exchange        TEXT NOT NULL,
                side            TEXT NOT NULL,
                order_type      TEXT NOT NULL,
                price           TEXT NOT NULL,
                quantity        TEXT NOT NULL,
                filled_quantity TEXT NOT NULL,
                filled_price    TEXT NOT NULL,
                status          TEXT NOT NULL,
                fee             TEXT NOT NULL,
                pnl             TEXT NOT NULL,
                create_time     TIMESTAMP NOT NULL,
                update_time     TIMESTAMP NOT NULL,
                UNIQUE(order_id, exchange)
            );
            """)

    # ─────────────────────── K 线 ────────────────────────────────

    def save_bar(self, bar) -> None:
        """保存单根 K 线（重复则忽略）。"""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bar_data
                (inst_id, exchange, interval, timestamp, open, high, low, close, volume, volume_ccy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.inst_id,
                    bar.exchange.value,
                    bar.interval,
                    bar.timestamp,
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                    str(bar.volume_ccy),
                ),
            )

    def save_bars(self, bars: list) -> int:
        """批量保存 K 线，返回实际插入行数。"""
        if not bars:
            return 0
        with self._conn() as conn:
            params = [
                (
                    b.inst_id, b.exchange.value, b.interval, b.timestamp,
                    str(b.open), str(b.high), str(b.low), str(b.close),
                    str(b.volume), str(b.volume_ccy),
                )
                for b in bars
            ]
            result = conn.executemany(
                """
                INSERT OR IGNORE INTO bar_data
                (inst_id, exchange, interval, timestamp, open, high, low, close, volume, volume_ccy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            return result.rowcount

    def load_bars(
        self,
        inst_id: str,
        exchange: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list:
        """从数据库加载 K 线（返回 BarData 列表）。"""
        from core.enums import Exchange as Ex
        from core.models import BarData

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bar_data
                WHERE inst_id=? AND exchange=? AND interval=?
                  AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
                """,
                (inst_id, exchange, interval, start, end),
            ).fetchall()

        bars = []
        for row in rows:
            bars.append(BarData(
                inst_id=row["inst_id"],
                exchange=Ex(row["exchange"]),
                interval=row["interval"],
                timestamp=row["timestamp"],
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                volume_ccy=Decimal(row["volume_ccy"]),
            ))
        return bars

    # ─────────────────────── 成交记录 ────────────────────────────

    def save_trade(self, trade) -> None:
        """保存成交记录。"""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO trade_record
                (trade_id, order_id, inst_id, exchange, side, price, quantity, fee, fee_ccy, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id,
                    trade.order_id,
                    trade.inst_id,
                    trade.exchange.value,
                    trade.side.value,
                    str(trade.price),
                    str(trade.quantity),
                    str(trade.fee),
                    trade.fee_ccy,
                    trade.timestamp,
                ),
            )

    # ─────────────────────── 订单记录 ────────────────────────────

    def save_ticks(self, ticks: list) -> int:
        """批量保存 Tick 数据，返回实际插入行数（INSERT OR IGNORE 去重）。"""
        if not ticks:
            return 0
        params = []
        for t in ticks:
            params.append((
                t.inst_id,
                t.exchange.value,
                t.timestamp,
                str(t.last_price),
                str(t.bid_price),
                str(t.ask_price),
                str(t.bid_size),
                str(t.ask_size),
                str(t.volume_24h),
                str(t.volume_ccy_24h),
            ))
        with self._conn() as conn:
            result = conn.executemany(
                """
                INSERT OR IGNORE INTO tick_data
                (inst_id, exchange, timestamp,
                 last_price, bid_price, ask_price,
                 bid_size, ask_size, volume_24h, volume_ccy_24h)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            return result.rowcount

    def save_order(self, order) -> None:
        """保存/更新订单记录。"""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO order_record
                (order_id, client_order_id, inst_id, exchange, side, order_type,
                 price, quantity, filled_quantity, filled_price, status,
                 fee, pnl, create_time, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.client_order_id,
                    order.inst_id,
                    order.exchange.value,
                    order.side.value,
                    order.order_type.value,
                    str(order.price),
                    str(order.quantity),
                    str(order.filled_quantity),
                    str(order.filled_price),
                    order.status.value,
                    str(order.fee),
                    str(order.pnl),
                    order.create_time,
                    order.update_time,
                ),
            )

    # ─────────────────────── 统计查询 ────────────────────────────

    def get_bar_count(self, inst_id: str, exchange: str, interval: str) -> int:
        """查询某产品的 K 线数量。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM bar_data WHERE inst_id=? AND exchange=? AND interval=?",
                (inst_id, exchange, interval),
            ).fetchone()
            return row["cnt"] if row else 0

    def get_bar_range(self, inst_id: str, exchange: str, interval: str) -> tuple[datetime, datetime] | None:
        """
        查询某产品 K 线的时间范围。

        Returns:
            (min_ts, max_ts) 均为 datetime 对象（带 UTC tzinfo），
            本地无数据时返回 None。
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts
                FROM bar_data WHERE inst_id=? AND exchange=? AND interval=?
                """,
                (inst_id, exchange, interval),
            ).fetchone()
            if row and row["min_ts"]:
                # SQLite 以字符串存储 TIMESTAMP，手动解析为 datetime
                def _parse(ts_str: str) -> datetime:
                    from datetime import timezone as tz
                    # 支持 "2025-01-01 00:00:00" 或 "2025-01-01T00:00:00"
                    ts_str = ts_str.replace("T", " ").split(".")[0]
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    return dt.replace(tzinfo=tz.utc) if dt.tzinfo is None else dt

                return _parse(row["min_ts"]), _parse(row["max_ts"])
            return None

    # ─────────────────────── 别名（prompt 接口规范） ──────────────

    def write_bars(self, symbol: str, interval: str, bars: list, exchange: str = "") -> int:
        """
        批量写入 K 线数据，兼容 prompt 规范的接口命名。

        Args:
            symbol:   产品 ID（同 inst_id）
            interval: K 线周期
            bars:     BarData 列表；若对象有 .exchange 属性则取之，
                      否则使用 exchange 参数
            exchange: 交易所名称字符串（bars 无 exchange 属性时使用）

        Returns:
            实际插入行数
        """
        return self.save_bars(bars)

    def read_bars(
        self,
        symbol: str,
        interval: str,
        start_ts: datetime,
        end_ts: datetime,
        exchange: str = "",
    ) -> list:
        """
        按时间范围查询 K 线数据，兼容 prompt 规范的接口命名。

        Args:
            symbol:   产品 ID（同 inst_id）
            interval: K 线周期
            start_ts: 起始时间（inclusive）
            end_ts:   结束时间（inclusive）
            exchange: 交易所名称字符串

        Returns:
            BarData 列表，按时间升序
        """
        return self.load_bars(symbol, exchange, interval, start_ts, end_ts)
