"""API 频率限制管理 - 防止超过交易所限频导致报错"""

from __future__ import annotations

import time
import logging
from collections import deque
from threading import Lock

logger = logging.getLogger("risk.rate_limiter")


class RateLimiter:
    """
    滑动窗口频率限制器（线程安全，阻塞式）。

    超限时自动等待（而非抛出异常），保证请求最终成功。
    适合 gateway 层保证不超 API 配额。
    """

    def __init__(self, max_calls: int, period: float):
        """
        Args:
            max_calls: 在 period 秒内最大调用次数
            period:    时间窗口（秒）
        """
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        """
        请求一次调用配额。

        如果当前窗口内已达到 max_calls，则阻塞等待，
        直到窗口滑动出足够空间（无限重试直到成功）。
        """
        while True:
            wait_time: float | None = None
            with self._lock:
                now = time.monotonic()
                # 清理过期记录
                while self._calls and self._calls[0] <= now - self.period:
                    self._calls.popleft()

                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return

                # 在锁内计算等待时长，锁外睡眠（避免持锁睡眠）
                oldest = self._calls[0]
                wait_time = self.period - (now - oldest) + 0.01  # 多等 10ms 避免边界
                logger.debug(
                    "频率限制触发，等待 %.2f 秒 (当前 %d/%d 次/%.0fs)",
                    wait_time, len(self._calls), self.max_calls, self.period,
                )

            # 释放锁后等待，然后回到 while True 重新尝试
            time.sleep(wait_time)

    def __call__(self, func):
        """作为装饰器使用。"""
        import functools

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)

        return wrapper


class OKXRateLimiter:
    """
    OKX HTTP API 频率限制器集合（Gateway 层使用）。

    职责边界：
    - 本类用于遵守 OKX REST API 的 HTTP 请求频率限制，属于 I/O 层职责。
      应由 gateway 在发出 HTTP 请求前调用，不应在风控层调用。
    - 风控层的业务下单频率限制（如同一策略 1 秒内不超过 N 笔）
      请使用 OrderRateLimiter。

    接口限制（参考值，以官方文档为准）：
    - 交易类（下单/撤单）: 20 次/2 秒
    - 行情类（查询行情）: 20 次/2 秒
    - 账户类（查询账户）: 10 次/2 秒
    - 历史数据类：10 次/2 秒
    """

    def __init__(self):
        self.trade   = RateLimiter(max_calls=20, period=2.0)
        self.market  = RateLimiter(max_calls=20, period=2.0)
        self.account = RateLimiter(max_calls=10, period=2.0)
        self.history = RateLimiter(max_calls=10, period=2.0)

    def check_trade(self) -> None:
        """交易接口频控检查。"""
        self.trade.acquire()

    def check_market(self) -> None:
        """行情接口频控检查。"""
        self.market.acquire()

    def check_account(self) -> None:
        """账户接口频控检查。"""
        self.account.acquire()

    def check_history(self) -> None:
        """历史数据接口频控检查。"""
        self.history.acquire()


class OrderRateLimiter:
    """
    业务层下单频率限制器（风控层使用，非阻塞）。

    职责边界：
    - 本类控制业务维度的下单频率，例如防止策略失控时短时间内
      疯狂下单（如同一账户 1 秒内超过 N 笔）。属于风控层职责。
    - 与 OKXRateLimiter 的区别：超频时直接抛出异常（非阻塞），
      让上层决定是拒绝还是排队，而不是在检查链内阻塞等待。

    扩展：若需要按策略维度限频，可在 check() 中使用
    request.extra.get("strategy_id") 作为分组 key。
    """

    def __init__(self, max_orders: int = 10, period: float = 1.0):
        """
        Args:
            max_orders: 在 period 秒内允许的最大下单数（默认 10 单/秒）
            period:     时间窗口（秒）
        """
        self.max_orders = max_orders
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def check(self, request=None) -> None:
        """
        检查业务下单频率，超限时立即抛出异常（不阻塞）。

        Args:
            request: 下单请求（预留，暂未按策略维度区分）

        Raises:
            RateLimitError: 超出频率限制
        """
        from core.exceptions import RateLimitError as RLE
        with self._lock:
            now = time.monotonic()
            while self._calls and self._calls[0] <= now - self.period:
                self._calls.popleft()

            if len(self._calls) >= self.max_orders:
                raise RLE(
                    f"下单频率超限：{len(self._calls)}/{self.max_orders} 单 "
                    f"/ {self.period:.0f}s，请求已拒绝"
                )
            self._calls.append(now)
