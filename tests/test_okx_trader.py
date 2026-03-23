"""
OKX 交易接口测试（集成测试，需要真实 API Key）

运行方式：
    export OKX_API_KEY=xxx OKX_SECRET_KEY=xxx OKX_PASSPHRASE=xxx OKX_FLAG=1
    pytest tests/test_okx_trader.py -v

未设置 API Key 时，测试会自动跳过。
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

import pytest

from core.enums import Exchange, MarginMode, MarketType, OrderSide, OrderType
from core.event_bus import EventBus
from gateway.okx.okx_gateway import OKXGateway
from utils.config_loader import load_okx_config


# ── 跳过条件：无 API Key ──────────────────────────────────────
@pytest.fixture(scope="module")
def gateway():
    config = load_okx_config()
    okx_cfg = config["okx"]

    if not okx_cfg.get("api_key"):
        pytest.skip("未设置 OKX_API_KEY，跳过集成测试")

    event_bus = EventBus()
    gw = OKXGateway(event_bus, okx_cfg)
    try:
        gw.connect()
    except Exception as e:
        pytest.skip(f"OKX 连接失败，跳过集成测试: {e}")
    yield gw
    gw.disconnect()


# ── 行情测试（不需要余额）────────────────────────────────────

class TestOKXMarketData:
    def test_get_spot_instruments(self, gateway):
        instruments = gateway.get_instruments(MarketType.SPOT)
        assert len(instruments) > 0
        btc = next((i for i in instruments if i.inst_id == "BTC-USDT"), None)
        assert btc is not None
        assert btc.tick_size > 0
        assert btc.min_size > 0

    def test_get_ticker(self, gateway):
        tick = gateway.get_ticker("BTC-USDT")
        assert tick.inst_id == "BTC-USDT"
        assert tick.last_price > 0
        assert tick.bid_price > 0
        assert tick.ask_price > tick.bid_price

    def test_get_klines(self, gateway):
        bars = gateway.get_klines("BTC-USDT", "1H", limit=10)
        assert len(bars) > 0
        assert len(bars) <= 10
        # 时间升序
        if len(bars) > 1:
            assert bars[0].timestamp < bars[-1].timestamp
        assert bars[0].close > 0

    def test_get_orderbook(self, gateway):
        book = gateway.get_orderbook("BTC-USDT", depth=5)
        assert book.inst_id == "BTC-USDT"
        assert len(book.asks) > 0
        assert len(book.bids) > 0
        # asks 价格 > bids 价格
        assert book.asks[0][0] > book.bids[0][0]

    def test_get_swap_instruments(self, gateway):
        instruments = gateway.get_instruments(MarketType.SWAP)
        assert len(instruments) > 0
        btc_swap = next((i for i in instruments if i.inst_id == "BTC-USDT-SWAP"), None)
        assert btc_swap is not None
        assert btc_swap.contract_value > 0

    def test_get_funding_rate(self, gateway):
        fr = gateway.get_funding_rate("BTC-USDT-SWAP")
        assert fr.inst_id == "BTC-USDT-SWAP"
        # 资金费率通常在 -0.03% ~ 0.03% 之间
        assert abs(float(fr.funding_rate)) < 0.01


# ── 账户测试 ─────────────────────────────────────────────────

class TestOKXAccount:
    def test_get_balance(self, gateway):
        balance = gateway.get_balance()
        assert balance.exchange == Exchange.OKX
        assert balance.total_equity >= 0

    def test_get_account_config(self, gateway):
        config = gateway.get_account_config()
        assert "acctLv" in config
        assert "posMode" in config

    def test_get_positions(self, gateway):
        positions = gateway.get_positions()
        # 模拟盘可能无持仓，只验证返回类型
        assert isinstance(positions, list)

    def test_get_fee_rate(self, gateway):
        fee = gateway.get_fee_rate("BTC-USDT")
        assert fee.exchange == Exchange.OKX
        # 手续费率应为负数（Maker 返佣）或小正数
        assert abs(float(fee.taker)) < 0.01


# ── 下单测试（谨慎，仅模拟盘）───────────────────────────────

class TestOKXTrader:
    @pytest.mark.parametrize("inst_id", ["BTC-USDT"])
    def test_place_and_cancel_limit_order(self, gateway, inst_id):
        """测试挂限价单并撤销（不会实际成交）。"""
        from core.models import OrderRequest
        from utils.helpers import round_to

        tick = gateway.get_ticker(inst_id)
        current_price = tick.last_price
        instruments = gateway.get_instruments(MarketType.SPOT)
        inst = next((i for i in instruments if i.inst_id == inst_id), None)

        if not inst:
            pytest.skip(f"找不到产品信息: {inst_id}")

        # 挂远低于市价的限价单，不会成交
        limit_price = round_to(current_price * Decimal("0.9"), inst.tick_size)

        request = OrderRequest(
            inst_id=inst_id,
            exchange=Exchange.OKX,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=limit_price,
            quantity=inst.min_size,
            margin_mode=MarginMode.CASH,
        )

        order = gateway.send_order(request)
        assert order.order_id
        assert order.status.value in ("SUBMITTED", "live", "PARTIAL_FILLED")

        # 立即撤单
        import time
        time.sleep(0.5)
        success = gateway.cancel_order(order.order_id, inst_id)
        assert success

        # 验证撤单成功
        time.sleep(0.5)
        order_detail = gateway.get_order(order.order_id, inst_id)
        from core.enums import OrderStatus
        assert order_detail.status == OrderStatus.CANCELLED

    def test_get_open_orders(self, gateway):
        orders = gateway.get_open_orders()
        assert isinstance(orders, list)

    def test_get_order_history(self, gateway):
        history = gateway.get_order_history(MarketType.SPOT, days=7)
        assert isinstance(history, list)
