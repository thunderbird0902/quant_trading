"""
strategy_core/array_manager.py
================================
ArrayManager — 预分配 numpy 环形缓冲区 + 技术指标计算。

设计目标
--------
1. 固定大小的环形数组，避免 list/deque 的动态内存分配。
2. 每次 update_bar() 只写一个元素，O(1) 复杂度。
3. 提供常用技术指标（SMA/EMA/RSI/MACD/Bollinger/ATR…）的计算接口。
4. 用 Literal[bool] 重载：array=True 返回 np.ndarray，False 返回 float。
5. inited 属性：当缓冲区已被至少 size 根 K 线填满后为 True。

依赖
----
- numpy  （必须）
- talib  （可选；未安装时指标方法会 raise ImportError）

使用示例
--------
    am = ArrayManager(size=100)
    for bar in bars:
        am.update_bar(bar)
        if not am.inited:
            continue
        fast = am.sma(10)
        slow = am.sma(30)
        rsi_val = am.rsi(14)
"""

from __future__ import annotations

from typing import Literal, overload

import numpy as np

from core.models import BarData


class ArrayManager:
    """
    预分配 numpy 环形缓冲区。

    Parameters
    ----------
    size : int
        缓冲区大小，默认 100。当 count >= size 时 inited=True。
    """

    def __init__(self, size: int = 100) -> None:
        self.size: int = size
        self.count: int = 0          # 已接收的 bar 总数（不超过 size 后不再自增）

        # OHLCV 环形数组（float64）
        self.open_array:   np.ndarray = np.zeros(size, dtype=np.float64)
        self.high_array:   np.ndarray = np.zeros(size, dtype=np.float64)
        self.low_array:    np.ndarray = np.zeros(size, dtype=np.float64)
        self.close_array:  np.ndarray = np.zeros(size, dtype=np.float64)
        self.volume_array: np.ndarray = np.zeros(size, dtype=np.float64)

        # 内部写指针（循环）
        self._idx: int = 0

    # ──────────────────────────────── 属性 ─────────────────────────────────

    @property
    def inited(self) -> bool:
        """缓冲区是否已填满（至少 size 根 bar）。"""
        return self.count >= self.size

    # ──────────────────────────────── 更新 ─────────────────────────────────

    def update_bar(self, bar: BarData) -> None:
        """
        将一根 K 线数据写入环形缓冲区。

        Parameters
        ----------
        bar : BarData
            已收盘的 K 线。
        """
        idx = self._idx
        self.open_array[idx]   = float(bar.open)
        self.high_array[idx]   = float(bar.high)
        self.low_array[idx]    = float(bar.low)
        self.close_array[idx]  = float(bar.close)
        self.volume_array[idx] = float(bar.volume)

        self._idx = (idx + 1) % self.size
        if self.count < self.size:
            self.count += 1

    # ──────────────────────────────── 视图 ─────────────────────────────────

    def _ordered(self, arr: np.ndarray) -> np.ndarray:
        """
        将环形数组转换为时间顺序（最旧→最新）的连续数组。
        仅在 inited 后调用有意义。
        """
        if self.count < self.size:
            # 未填满，直接取前 count 个元素（已是顺序）
            return arr[:self.count].copy()
        # 已填满：_idx 指向最旧元素
        return np.concatenate([arr[self._idx:], arr[:self._idx]])

    @property
    def open(self) -> np.ndarray:
        return self._ordered(self.open_array)

    @property
    def high(self) -> np.ndarray:
        return self._ordered(self.high_array)

    @property
    def low(self) -> np.ndarray:
        return self._ordered(self.low_array)

    @property
    def close(self) -> np.ndarray:
        return self._ordered(self.close_array)

    @property
    def volume(self) -> np.ndarray:
        return self._ordered(self.volume_array)

    # ──────────────────────────────── 指标：SMA ─────────────────────────────

    @overload
    def sma(self, period: int, array: Literal[False] = ...) -> float: ...
    @overload
    def sma(self, period: int, array: Literal[True]) -> np.ndarray: ...

    def sma(self, period: int, array: bool = False) -> float | np.ndarray:
        """
        简单移动平均（SMA）。

        Parameters
        ----------
        period : int   计算周期
        array  : bool  True → 返回整列，False → 返回最新值（默认）
        """
        try:
            import talib
            result = talib.SMA(self.close, timeperiod=period)
        except ImportError:
            result = self._sma_np(self.close, period)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _sma_np(data: np.ndarray, period: int) -> np.ndarray:
        """纯 numpy 实现 SMA（TA-Lib 不可用时的降级）。"""
        result = np.full_like(data, np.nan)
        if len(data) < period:
            return result
        kernel = np.ones(period) / period
        valid = np.convolve(data, kernel, mode="valid")
        result[period - 1:] = valid
        return result

    # ──────────────────────────────── 指标：EMA ─────────────────────────────

    @overload
    def ema(self, period: int, array: Literal[False] = ...) -> float: ...
    @overload
    def ema(self, period: int, array: Literal[True]) -> np.ndarray: ...

    def ema(self, period: int, array: bool = False) -> float | np.ndarray:
        """指数移动平均（EMA）。"""
        try:
            import talib
            result = talib.EMA(self.close, timeperiod=period)
        except ImportError:
            result = self._ema_np(self.close, period)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _ema_np(data: np.ndarray, period: int) -> np.ndarray:
        result = np.full_like(data, np.nan)
        if len(data) < period:
            return result
        alpha = 2.0 / (period + 1)
        result[period - 1] = np.mean(data[:period])
        for i in range(period, len(data)):
            result[i] = data[i] * alpha + result[i - 1] * (1 - alpha)
        return result

    # ──────────────────────────────── 指标：RSI ─────────────────────────────

    @overload
    def rsi(self, period: int, array: Literal[False] = ...) -> float: ...
    @overload
    def rsi(self, period: int, array: Literal[True]) -> np.ndarray: ...

    def rsi(self, period: int, array: bool = False) -> float | np.ndarray:
        """
        RSI（相对强弱指数）。

        使用 TA-Lib 的 Wilder 平滑方法（与大多数平台一致）。
        未安装 TA-Lib 时使用简单平均版（精度略有差异）。
        """
        try:
            import talib
            result = talib.RSI(self.close, timeperiod=period)
        except ImportError:
            result = self._rsi_np(self.close, period)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _rsi_np(data: np.ndarray, period: int) -> np.ndarray:
        """简单平均版 RSI（TA-Lib 降级）。"""
        result = np.full_like(data, np.nan)
        if len(data) < period + 1:
            return result
        delta = np.diff(data)
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        for i in range(period, len(data)):
            start = i - period
            avg_gain = np.mean(gains[start:i])
            avg_loss = np.mean(losses[start:i])
            if avg_loss == 0:
                result[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - 100.0 / (1.0 + rs)
        return result

    # ──────────────────────────────── 指标：STD ─────────────────────────────

    @overload
    def std(self, period: int, nbdev: float = ..., array: Literal[False] = ...) -> float: ...
    @overload
    def std(self, period: int, nbdev: float, array: Literal[True]) -> np.ndarray: ...

    def std(self, period: int, nbdev: float = 1.0, array: bool = False) -> float | np.ndarray:
        """滚动标准差。"""
        try:
            import talib
            result = talib.STDDEV(self.close, timeperiod=period, nbdev=nbdev)
        except ImportError:
            result = self._std_np(self.close, period, nbdev)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _std_np(data: np.ndarray, period: int, nbdev: float = 1.0) -> np.ndarray:
        result = np.full_like(data, np.nan)
        for i in range(period - 1, len(data)):
            result[i] = np.std(data[i - period + 1: i + 1], ddof=0) * nbdev
        return result

    # ──────────────────────────────── 指标：Bollinger Bands ─────────────────

    def boll(
        self,
        period: int,
        dev: float = 2.0,
        array: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | tuple[float, float, float]:
        """
        布林带（Bollinger Bands）。

        Returns
        -------
        (upper, mid, lower)
            array=False → 三个 float（最新值）
            array=True  → 三个 np.ndarray
        """
        try:
            import talib
            upper, mid, lower = talib.BBANDS(self.close, timeperiod=period,
                                              nbdevup=dev, nbdevdn=dev)
        except ImportError:
            mid   = self._sma_np(self.close, period)
            sigma = self._std_np(self.close, period)
            upper = mid + dev * sigma
            lower = mid - dev * sigma

        if array:
            return upper, mid, lower
        return self._to_scalar(upper), self._to_scalar(mid), self._to_scalar(lower)

    # ──────────────────────────────── 指标：MACD ─────────────────────────────

    def macd(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        array: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | tuple[float, float, float]:
        """
        MACD（差离值、信号线、柱状图）。

        Returns
        -------
        (macd_line, signal_line, histogram)
        """
        try:
            import talib
            macd_line, signal_line, hist = talib.MACD(
                self.close, fastperiod=fast, slowperiod=slow, signalperiod=signal
            )
        except ImportError:
            ema_fast = self._ema_np(self.close, fast)
            ema_slow = self._ema_np(self.close, slow)
            macd_line = ema_fast - ema_slow
            signal_line = self._ema_np(macd_line, signal)
            hist = macd_line - signal_line

        if array:
            return macd_line, signal_line, hist
        return self._to_scalar(macd_line), self._to_scalar(signal_line), self._to_scalar(hist)

    # ──────────────────────────────── 指标：ATR ─────────────────────────────

    @overload
    def atr(self, period: int, array: Literal[False] = ...) -> float: ...
    @overload
    def atr(self, period: int, array: Literal[True]) -> np.ndarray: ...

    def atr(self, period: int, array: bool = False) -> float | np.ndarray:
        """平均真实波幅（ATR）。"""
        try:
            import talib
            result = talib.ATR(self.high, self.low, self.close, timeperiod=period)
        except ImportError:
            result = self._atr_np(self.high, self.low, self.close, period)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        result = np.full_like(close, np.nan)
        if len(close) < 2:
            return result
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
        )
        for i in range(period - 1, len(tr)):
            result[i] = np.mean(tr[i - period + 1: i + 1])
        return result

    # ──────────────────────────────── 指标：ROC ─────────────────────────────

    @overload
    def roc(self, period: int, array: Literal[False] = ...) -> float: ...
    @overload
    def roc(self, period: int, array: Literal[True]) -> np.ndarray: ...

    def roc(self, period: int, array: bool = False) -> float | np.ndarray:
        """变动率（Rate of Change）。"""
        try:
            import talib
            result = talib.ROC(self.close, timeperiod=period)
        except ImportError:
            result = self._roc_np(self.close, period)

        return result if array else self._to_scalar(result)

    @staticmethod
    def _roc_np(data: np.ndarray, period: int) -> np.ndarray:
        result = np.full_like(data, np.nan)
        for i in range(period, len(data)):
            prev = data[i - period]
            if prev != 0:
                result[i] = (data[i] - prev) / prev * 100.0
        return result

    # ──────────────────────────────── 指标：KDJ ─────────────────────────────

    def kdj(
        self,
        fastk_period: int = 9,
        slowk_period: int = 3,
        slowd_period: int = 3,
        array: bool = False,
    ) -> tuple[np.ndarray, np.ndarray] | tuple[float, float]:
        """
        KDJ 随机指标（K 和 D 线）。

        Returns
        -------
        (k, d)
        """
        try:
            import talib
            k, d = talib.STOCH(
                self.high, self.low, self.close,
                fastk_period=fastk_period,
                slowk_period=slowk_period,
                slowk_matype=1,
                slowd_period=slowd_period,
                slowd_matype=1,
            )
        except ImportError:
            # 简化版：仅计算最高/最低后的 fast %K
            close = self.close
            high  = self.high
            low   = self.low
            k = np.full_like(close, np.nan)
            for i in range(fastk_period - 1, len(close)):
                h = np.max(high[i - fastk_period + 1: i + 1])
                l = np.min(low[i - fastk_period + 1: i + 1])
                denom = h - l
                k[i] = (close[i] - l) / denom * 100.0 if denom != 0 else 50.0
            d = self._sma_np(k, slowd_period)

        if array:
            return k, d
        return self._to_scalar(k), self._to_scalar(d)

    # ──────────────────────────────── 辅助 ─────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ArrayManager(size={self.size}, count={self.count}, "
            f"inited={self.inited})"
        )

    @staticmethod
    def _to_scalar(arr: np.ndarray) -> float:
        """
        将指标数组的末尾值安全地转为 float。

        - 空数组（count==0 时会出现）返回 float('nan') 而非 IndexError
        - 末尾为 NaN 时返回 nan（正常的预热期行为，调用方应检查 inited）
        """
        return float(arr[-1]) if len(arr) > 0 else float("nan")
