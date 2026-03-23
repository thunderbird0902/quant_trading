"""通用工具函数"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal


# ─────────────────────── 时间工具 ────────────────────────────

def now_utc() -> datetime:
    """返回当前 UTC 时间（带时区信息）。"""
    return datetime.now(timezone.utc)


def ts_to_datetime(ts_ms: int | str) -> datetime:
    """
    毫秒时间戳 → UTC datetime。

    Args:
        ts_ms: 毫秒级时间戳（int 或 str）
    """
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)


def datetime_to_ts(dt: datetime) -> int:
    """UTC datetime → 毫秒时间戳。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ─────────────────────── 精度工具 ────────────────────────────

def round_to(value: Decimal | str | float, step: Decimal | str) -> Decimal:
    """
    将 value 向下截断到 step 的整数倍。

    Examples:
        round_to("1.2345", "0.01")  → Decimal("1.23")
        round_to("100.5", "5")      → Decimal("100")
    """
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step == 0:
        return value
    return (value // step) * step


def decimal_places(step: Decimal | str) -> int:
    """
    计算 step 对应的小数位数。

    Examples:
        decimal_places("0.001") → 3
        decimal_places("10")    → 0
    """
    step = Decimal(str(step)).normalize()
    sign, digits, exponent = step.as_tuple()
    return max(0, -exponent)


# ─────────────────────── 订单 ID 工具 ────────────────────────

def gen_client_order_id(prefix: str = "qt") -> str:
    """
    生成唯一客户端订单 ID（最多 32 字符，OKX 限制）。

    格式：{prefix}{8位十六进制随机}
    """
    rand_hex = uuid.uuid4().hex[:16]
    cid = f"{prefix}{rand_hex}"
    return cid[:32]


# ─────────────────────── 数值工具 ────────────────────────────

def safe_decimal(value: str | int | float | None, default: Decimal = Decimal("0")) -> Decimal:
    """
    安全转换为 Decimal，失败时返回 default。

    Args:
        value: 待转换值
        default: 转换失败时的默认值
    """
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


# ─────────────────────── 列表工具 ────────────────────────────

def chunk_list(lst: list, size: int) -> list[list]:
    """
    将列表按 size 拆分为子列表。

    Examples:
        chunk_list([1,2,3,4,5], 2) → [[1,2],[3,4],[5]]
    """
    return [lst[i: i + size] for i in range(0, len(lst), size)]
