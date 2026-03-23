"""重试装饰器 - 支持同步/异步函数，指数退避"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Callable, Type

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable | None = None,
):
    """
    重试装饰器，支持同步和异步函数。

    策略：首次失败等待 delay 秒，之后每次翻倍（指数退避），
    最长等待 max_delay 秒。

    Args:
        max_attempts: 最大尝试次数（含第一次）
        delay:        首次重试前等待秒数
        backoff:      退避乘数（每次等待 = 上次 × backoff）
        max_delay:    单次等待上限（秒）
        exceptions:   触发重试的异常类型
        on_retry:     每次重试前的回调 fn(attempt, exc, wait)，可用于打日志/告警

    Examples:
        @retry(max_attempts=3, delay=1, exceptions=(NetworkError,))
        def call_api(): ...

        @retry(max_attempts=5, delay=0.5)
        async def async_call(): ...
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                wait = delay
                last_exc: Exception | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as exc:
                        last_exc = exc
                        if attempt == max_attempts:
                            break
                        actual_wait = min(wait, max_delay)
                        if on_retry:
                            on_retry(attempt, exc, actual_wait)
                        else:
                            logger.warning(
                                "重试 %s (attempt=%d/%d, wait=%.1fs): %s",
                                func.__qualname__, attempt, max_attempts, actual_wait, exc,
                            )
                        await asyncio.sleep(actual_wait)
                        wait *= backoff
                raise last_exc  # type: ignore[misc]
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                wait = delay
                last_exc: Exception | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as exc:
                        last_exc = exc
                        if attempt == max_attempts:
                            break
                        actual_wait = min(wait, max_delay)
                        if on_retry:
                            on_retry(attempt, exc, actual_wait)
                        else:
                            logger.warning(
                                "重试 %s (attempt=%d/%d, wait=%.1fs): %s",
                                func.__qualname__, attempt, max_attempts, actual_wait, exc,
                            )
                        time.sleep(actual_wait)
                        wait *= backoff
                raise last_exc  # type: ignore[misc]
            return sync_wrapper
    return decorator


def retry_on_network_error(max_attempts: int = 3, delay: float = 1.0):
    """
    快捷装饰器：仅对网络相关错误重试。

    捕获 NetworkError / ConnectionError / TimeoutError。
    """
    from core.exceptions import NetworkError as QuantNetworkError
    return retry(
        max_attempts=max_attempts,
        delay=delay,
        exceptions=(QuantNetworkError, ConnectionError, TimeoutError, OSError),
    )
