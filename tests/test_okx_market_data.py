"""测试：OKX 工具函数（无需网络连接）"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderStatus, OrderType, PositionSide
from core.exceptions import APIError
from gateway.okx.okx_utils import (
    check_response,
    market_type_to_okx,
    okx_to_market_type,
    margin_mode_to_okx,
    order_type_to_okx,
    parse_ticker,
    parse_kline,
    parse_order,
    parse_position,
    parse_balance,
    parse_funding_rate,
)


class TestEnumMapping:
    def test_market_type_to_okx(self):
        assert market_type_to_okx(MarketType.SPOT) == "SPOT"
        assert market_type_to_okx(MarketType.SWAP) == "SWAP"
        assert market_type_to_okx(MarketType.FUTURES) == "FUTURES"
        assert market_type_to_okx(MarketType.OPTION) == "OPTION"

    def test_okx_to_market_type(self):
        assert okx_to_market_type("SPOT") == MarketType.SPOT
        assert okx_to_market_type("SWAP") == MarketType.SWAP
        assert okx_to_market_type("MARGIN") == MarketType.SPOT   # MARGIN → SPOT

    def test_margin_mode_to_okx(self):
        assert margin_mode_to_okx(MarginMode.CROSS) == "cross"
        assert margin_mode_to_okx(MarginMode.ISOLATED) == "isolated"
        assert margin_mode_to_okx(MarginMode.CASH) == "cash"

    def test_order_type_to_okx(self):
        assert order_type_to_okx(OrderType.LIMIT) == "limit"
        assert order_type_to_okx(OrderType.MARKET) == "market"
        assert order_type_to_okx(OrderType.STOP_LIMIT) == "conditional"
        assert order_type_to_okx(OrderType.TRAILING_STOP) == "move_order_stop"


class TestCheckResponse:
    def test_success(self):
        resp = {"code": "0", "data": [{"id": "1"}]}
        data = check_response(resp, "test")
        assert data == [{"id": "1"}]

    def test_empty_data(self):
        resp = {"code": "0", "data": []}
        data = check_response(resp, "test")
        assert data == []

    def test_error_raises(self):
        resp = {"code": "51001", "msg": "产品不存在", "data": []}
        with pytest.raises(APIError) as exc_info:
            check_response(resp, "test_op")
        assert exc_info.value.code == "51001"
        assert "产品不存在" in str(exc_info.value)


class TestParseTicker:
    def _make_raw(self):
        return {
            "instId": "BTC-USDT",
            "last": "50000.5",
            "bidPx": "49999.9",
            "askPx": "50001.1",
            "bidSz": "0.1",
            "askSz": "0.2",
            "high24h": "51000",
            "low24h": "49000",
            "vol24h": "1000",
            "volCcy24h": "50000000",
            "ts": "1704067200000",
            "open24h": "49500",
        }

    def test_basic_fields(self):
        tick = parse_ticker(self._make_raw())
        assert tick.inst_id == "BTC-USDT"
        assert tick.exchange == Exchange.OKX
        assert tick.last_price == Decimal("50000.5")
        assert tick.bid_price == Decimal("49999.9")
        assert tick.ask_price == Decimal("50001.1")
        assert tick.volume_24h == Decimal("1000")

    def test_timestamp(self):
        tick = parse_ticker(self._make_raw())
        assert tick.timestamp.year == 2024


class TestParseKline:
    def test_basic_kline(self):
        raw = ["1704067200000", "49000", "51000", "48000", "50000", "1000", "50000000", "50000000", "1"]
        bar = parse_kline(raw, "BTC-USDT", "1H")
        assert bar.inst_id == "BTC-USDT"
        assert bar.open == Decimal("49000")
        assert bar.high == Decimal("51000")
        assert bar.low == Decimal("48000")
        assert bar.close == Decimal("50000")
        assert bar.volume == Decimal("1000")
        assert bar.interval == "1H"


class TestParseOrder:
    def _make_raw(self, state="live"):
        return {
            "ordId": "123456789",
            "clOrdId": "qt_abc",
            "instId": "BTC-USDT",
            "side": "buy",
            "posSide": "net",
            "ordType": "limit",
            "px": "50000",
            "sz": "0.001",
            "fillSz": "0",
            "avgPx": "0",
            "state": state,
            "fee": "0",
            "pnl": "0",
            "cTime": "1704067200000",
            "uTime": "1704067200000",
            "tdMode": "cash",
            "feeCcy": "USDT",
        }

    def test_submitted_order(self):
        order = parse_order(self._make_raw("live"))
        assert order.order_id == "123456789"
        assert order.status == OrderStatus.SUBMITTED
        assert order.side == OrderSide.BUY

    def test_filled_order(self):
        raw = self._make_raw("filled")
        raw["fillSz"] = "0.001"
        raw["avgPx"] = "50001"
        order = parse_order(raw)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == Decimal("0.001")

    def test_is_active(self):
        order = parse_order(self._make_raw("live"))
        assert order.is_active is True

        order_filled = parse_order(self._make_raw("filled"))
        assert order_filled.is_active is False


class TestParseBalance:
    def test_basic_balance(self):
        raw = {
            "totalEq": "10000.5",
            "adjEq": "9500",
            "imr": "500",
            "upl": "100",
            "uTime": "1704067200000",
            "details": [
                {
                    "ccy": "USDT",
                    "availBal": "9000",
                    "frozenBal": "500",
                    "eq": "9500",
                    "eqUsd": "9500",
                },
                {
                    "ccy": "BTC",
                    "availBal": "0.01",
                    "frozenBal": "0",
                    "eq": "0.01",
                    "eqUsd": "500",
                },
            ],
        }
        balance = parse_balance(raw)
        assert balance.exchange == Exchange.OKX
        assert balance.total_equity == Decimal("10000.5")
        assert len(balance.details) == 2

        usdt = next(d for d in balance.details if d.currency == "USDT")
        assert usdt.available == Decimal("9000")
