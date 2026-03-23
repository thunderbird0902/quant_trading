"""OKX 行情数据实现"""

from __future__ import annotations

import logging
from datetime import datetime

from okx import MarketData, PublicData

from core.enums import Exchange, MarketType
from core.models import (
    BarData, FundingRateData, IndexTickerData, Instrument,
    MarkPriceData, OrderBook, TickData, TradeData,
)
from utils.helpers import datetime_to_ts
from utils.retry import retry

from .okx_utils import (
    _Throttle,
    check_response,
    inst_type_from_id,
    market_type_to_okx,
    parse_funding_rate,
    parse_index_ticker,
    parse_instrument,
    parse_kline,
    parse_mark_price,
    parse_orderbook,
    parse_ticker,
    parse_trade,
)

logger = logging.getLogger("market.okx")


class OKXMarketData:
    """
    OKX 行情数据模块（REST API）。

    封装 python-okx MarketData 和 PublicData API。
    所有返回值均为统一数据模型。
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "1"):
        """
        Args:
            api_key:    OKX API Key
            secret_key: OKX Secret Key
            passphrase: OKX Passphrase
            flag:       "0" = 实盘，"1" = 模拟盘
        """
        self._market_api = MarketData.MarketAPI(
            api_key, secret_key, passphrase, False, flag
        )
        self._public_api = PublicData.PublicAPI(
            api_key, secret_key, passphrase, False, flag
        )
        # 行情类端点限频：20 次/2 秒（OKX 官方限制）
        self._throttle = _Throttle(max_calls=20, period=2.0)

    # ─────────────────────── 产品信息 ────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_instruments(self, market_type: MarketType) -> list[Instrument]:
        """
        获取产品列表。

        Args:
            market_type: SPOT / SWAP / FUTURES / OPTION

        Returns:
            Instrument 列表
        """
        inst_type = market_type_to_okx(market_type)
        self._throttle.acquire()
        resp = self._public_api.get_instruments(instType=inst_type)
        data = check_response(resp, f"get_instruments({inst_type})")
        instruments = [parse_instrument(item) for item in data]
        logger.debug("获取产品列表 instType=%s count=%d", inst_type, len(instruments))
        return instruments

    # ─────────────────────── 行情快照 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_ticker(self, inst_id: str) -> TickData:
        """获取单一产品最新行情。"""
        self._throttle.acquire()
        resp = self._market_api.get_ticker(instId=inst_id)
        data = check_response(resp, f"get_ticker({inst_id})")
        if not data:
            raise ValueError(f"get_ticker 返回空数据: {inst_id}")
        tick = parse_ticker(data[0])
        logger.debug("行情 %s last=%.4f", inst_id, float(tick.last_price))
        return tick

    @retry(max_attempts=3, delay=0.5)
    def get_tickers(self, market_type: MarketType) -> list[TickData]:
        """获取指定市场类型的全部行情。"""
        inst_type = market_type_to_okx(market_type)
        self._throttle.acquire()
        resp = self._market_api.get_tickers(instType=inst_type)
        data = check_response(resp, f"get_tickers({inst_type})")
        return [parse_ticker(item) for item in data]

    # ─────────────────────── K 线 ────────────────────────────────

    def get_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        """
        获取最近 K 线数据（自动分批，支持超过 300 条）。

        Args:
            inst_id:  产品 ID，如 "BTC-USDT"
            interval: K 线周期，如 "1m", "1H", "1D"
            limit:    数量，无上限，内部每批最多 300 条自动翻页

        Returns:
            BarData 列表，按时间升序
        """
        all_bars: list[BarData] = []
        remaining = limit
        after: int | None = None  # OKX after 游标（毫秒时间戳），None 表示从最新开始

        while remaining > 0:
            batch = min(remaining, 300)
            kwargs: dict = dict(instId=inst_id, bar=interval, limit=str(batch))
            if after is not None:
                kwargs["after"] = str(after)

            self._throttle.acquire()
            resp = self._market_api.get_candlesticks(**kwargs)
            data = check_response(resp, f"get_klines({inst_id},{interval})")
            if not data:
                break

            bars = [parse_kline(row, inst_id, interval) for row in data]
            all_bars.extend(bars)
            remaining -= len(bars)

            if len(bars) < batch:
                break  # 返回条数不足，说明已无更多数据

            after = int(data[-1][0]) - 1  # 最旧时间戳前移一毫秒作为下批游标

        all_bars.sort(key=lambda b: b.timestamp)
        logger.debug("K线 %s %s count=%d", inst_id, interval, len(all_bars))
        return all_bars

    @retry(max_attempts=3, delay=1.0)
    def get_history_klines(
        self,
        inst_id: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        """
        获取历史 K 线（按时间范围，最多 100 条/次，自动分批）。

        Args:
            inst_id:  产品 ID
            interval: K 线周期
            start:    开始时间（UTC）
            end:      结束时间（UTC）

        Returns:
            BarData 列表，按时间升序
        """
        all_bars: list[BarData] = []
        after = datetime_to_ts(end)   # OKX 用 after 表示时间倒查的游标
        before = datetime_to_ts(start)

        while True:
            self._throttle.acquire()
            resp = self._market_api.get_history_candlesticks(
                instId=inst_id,
                bar=interval,
                after=str(after),
                before=str(before),
                limit="100",
            )
            data = check_response(resp, f"get_history_klines({inst_id})")
            if not data:
                break
            bars = [parse_kline(row, inst_id, interval) for row in data]
            all_bars.extend(bars)
            # 更新游标（OKX 返回的最旧时间戳作为下次 after）
            oldest_ts = int(data[-1][0])
            if oldest_ts <= before:
                break
            after = oldest_ts - 1

        all_bars.sort(key=lambda b: b.timestamp)
        logger.debug("历史K线 %s %s count=%d", inst_id, interval, len(all_bars))
        return all_bars

    # ─────────────────────── 深度 ────────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_orderbook(self, inst_id: str, depth: int = 20) -> OrderBook:
        """
        获取订单簿深度。

        Args:
            depth: 档位数，1-400，books5 固定 5 档
        """
        if depth <= 5:
            resp = self._market_api.get_orderbook(instId=inst_id, sz="5")
        else:
            resp = self._market_api.get_orderbook(instId=inst_id, sz=str(depth))
        data = check_response(resp, f"get_orderbook({inst_id})")
        if not data:
            raise ValueError(f"get_orderbook 返回空数据: {inst_id}")
        return parse_orderbook(data[0], inst_id)

    # ─────────────────────── 成交 ────────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_recent_trades(self, inst_id: str, limit: int = 100) -> list[TradeData]:
        """获取最近成交记录。"""
        resp = self._market_api.get_trades(instId=inst_id, limit=str(min(limit, 500)))
        data = check_response(resp, f"get_recent_trades({inst_id})")
        return [parse_trade(item) for item in data]

    # ─────────────────────── 平台成交量 ──────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_platform_volume(self) -> dict:
        """获取平台24小时总成交量。"""
        resp = self._market_api.get_volume()
        data = check_response(resp, "get_platform_volume")
        return data[0] if data else {}

    # ─────────────────────── 指数行情 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_index_tickers(self, quote_ccy: str = "USDT") -> list[IndexTickerData]:
        """获取指数行情列表。"""
        resp = self._public_api.get_index_tickers(quoteCcy=quote_ccy)
        data = check_response(resp, "get_index_tickers")
        return [parse_index_ticker(item) for item in data]

    @retry(max_attempts=3, delay=1.0)
    def get_index_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        """获取指数 K 线（如 BTC-USDT 指数）。"""
        resp = self._public_api.get_index_candlesticks(
            instId=inst_id, bar=interval, limit=str(min(limit, 300))
        )
        data = check_response(resp, f"get_index_klines({inst_id})")
        bars = [parse_kline(row, inst_id, f"INDEX_{interval}") for row in data]
        bars.sort(key=lambda b: b.timestamp)
        return bars

    @retry(max_attempts=3, delay=1.0)
    def get_mark_price_klines(self, inst_id: str, interval: str, limit: int = 100) -> list[BarData]:
        """获取标记价格 K 线。"""
        resp = self._public_api.get_mark_price_candlesticks(
            instId=inst_id, bar=interval, limit=str(min(limit, 300))
        )
        data = check_response(resp, f"get_mark_price_klines({inst_id})")
        bars = [parse_kline(row, inst_id, f"MARK_{interval}") for row in data]
        bars.sort(key=lambda b: b.timestamp)
        return bars

    # ─────────────────────── 合约专属 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_funding_rate(self, inst_id: str) -> FundingRateData:
        """获取当前资金费率（永续合约）。"""
        resp = self._public_api.get_funding_rate(instId=inst_id)
        data = check_response(resp, f"get_funding_rate({inst_id})")
        if not data:
            raise ValueError(f"get_funding_rate 返回空数据: {inst_id}")
        return parse_funding_rate(data[0])

    @retry(max_attempts=3, delay=1.0)
    def get_funding_rate_history(self, inst_id: str, limit: int = 30) -> list[FundingRateData]:
        """获取历史资金费率。"""
        resp = self._public_api.get_funding_rate_history(
            instId=inst_id, limit=str(min(limit, 100))
        )
        data = check_response(resp, f"get_funding_rate_history({inst_id})")
        return [parse_funding_rate(item) for item in data]

    @retry(max_attempts=3, delay=0.5)
    def get_mark_price(self, inst_id: str) -> MarkPriceData:
        """获取标记价格。"""
        inst_type = inst_type_from_id(inst_id)
        resp = self._public_api.get_mark_price(instType=inst_type, instId=inst_id)
        data = check_response(resp, f"get_mark_price({inst_id})")
        if not data:
            raise ValueError(f"get_mark_price 返回空数据: {inst_id}")
        return parse_mark_price(data[0])

    @retry(max_attempts=3, delay=0.5)
    def get_price_limit(self, inst_id: str) -> dict:
        """获取限价范围（合约涨跌停价）。"""
        resp = self._public_api.get_price_limit(instId=inst_id)
        data = check_response(resp, f"get_price_limit({inst_id})")
        return data[0] if data else {}

    @retry(max_attempts=3, delay=1.0)
    def get_exchange_rate(self) -> dict:
        """获取法币汇率（USD/CNY 等）。"""
        resp = self._public_api.get_exchange_rate()
        data = check_response(resp, "get_exchange_rate")
        return data[0] if data else {}
