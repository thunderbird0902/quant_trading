"""OKX 账户模块"""

from __future__ import annotations

import logging
from decimal import Decimal

from okx import Account, PublicData

from core.enums import MarginMode, MarketType, PositionMode, PositionSide
from core.models import (
    BalanceData, CurrencyBalance, FeeRate, FundingRateData,
    MarkPriceData, PositionData,
)
from utils.helpers import safe_decimal
from utils.retry import retry

from .okx_utils import (
    _Throttle,
    check_response,
    inst_type_from_id,
    market_type_to_okx,
    margin_mode_to_okx,
    pos_side_to_okx,
    parse_balance,
    parse_fee_rate,
    parse_funding_rate,
    parse_mark_price,
    parse_position,
)

logger = logging.getLogger("trading.okx.account")


class OKXAccount:
    """
    OKX 账户模块（REST API）。

    封装账户信息、持仓、杠杆、手续费等查询接口。
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "1"):
        self._account_api = Account.AccountAPI(
            api_key, secret_key, passphrase, False, flag
        )
        self._public_api = PublicData.PublicAPI(
            api_key, secret_key, passphrase, False, flag
        )
        # 账户类端点限频：10 次/2 秒（OKX 官方限制）
        self._throttle = _Throttle(max_calls=10, period=2.0)

    # ─────────────────────── 余额 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_balance(self, ccy: str = "") -> BalanceData:
        """
        获取账户余额。

        Args:
            ccy: 指定币种，如 "BTC,ETH"；为空时返回全部
        """
        kwargs = {"ccy": ccy} if ccy else {}
        self._throttle.acquire()
        resp = self._account_api.get_account_balance(**kwargs)
        data = check_response(resp, "get_balance")
        if not data:
            # 返回空余额
            return BalanceData(
                exchange=__import__("core.enums", fromlist=["Exchange"]).Exchange.OKX,
                total_equity=Decimal("0"),
                available_balance=Decimal("0"),
                frozen_balance=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                details=[],
                update_time=__import__("utils.helpers", fromlist=["now_utc"]).now_utc(),
            )
        return parse_balance(data[0])

    @retry(max_attempts=3, delay=0.5)
    def get_funding_balance(self, ccy: str = "") -> list[CurrencyBalance]:
        """
        获取资金账户余额（充提账户）。

        Args:
            ccy: 指定币种；为空时返回全部
        """
        from okx import Funding
        # 资金账户余额需要 Funding API
        # 这里使用 Account API 的资产接口代替
        resp = self._account_api.get_account_balance(ccy=ccy)
        data = check_response(resp, "get_funding_balance")
        if not data:
            return []
        balance = parse_balance(data[0])
        return balance.details

    # ─────────────────────── 持仓 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_positions(self, inst_id: str | None = None) -> list[PositionData]:
        """
        获取持仓列表。

        Args:
            inst_id: 指定产品，None 则返回全部
        """
        kwargs = {}
        if inst_id:
            kwargs["instId"] = inst_id
        self._throttle.acquire()
        resp = self._account_api.get_positions(**kwargs)
        data = check_response(resp, "get_positions")
        return [parse_position(item) for item in data if safe_decimal(item.get("pos")) != 0]

    @retry(max_attempts=3, delay=1.0)
    def get_positions_history(
        self,
        inst_type: str = "",
        inst_id: str = "",
        limit: int = 20,
    ) -> list[PositionData]:
        """获取历史持仓（已平仓）。"""
        kwargs = {"limit": str(limit)}
        if inst_type:
            kwargs["instType"] = inst_type
        if inst_id:
            kwargs["instId"] = inst_id
        resp = self._account_api.get_positions_history(**kwargs)
        data = check_response(resp, "get_positions_history")
        return [parse_position(item) for item in data]

    # ─────────────────────── 账户配置 ────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_account_config(self) -> dict:
        """
        获取账户配置。

        Returns:
            包含 acctLv（账户模式）、posMode（持仓模式）等的字典
        """
        resp = self._account_api.get_account_config()
        data = check_response(resp, "get_account_config")
        return data[0] if data else {}

    @retry(max_attempts=3, delay=1.0)
    def get_account_position_risk(self) -> dict:
        """获取账户持仓风险（衍生品）。"""
        resp = self._account_api.get_account_position_risk()
        data = check_response(resp, "get_account_position_risk")
        return data[0] if data else {}

    # ─────────────────────── 账单 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_bills(self, limit: int = 100) -> list[dict]:
        """获取近七天账单流水。"""
        resp = self._account_api.get_account_bills(limit=str(min(limit, 100)))
        return check_response(resp, "get_bills")

    @retry(max_attempts=3, delay=1.0)
    def get_bills_archive(self, limit: int = 100) -> list[dict]:
        """获取近三个月账单流水。"""
        resp = self._account_api.get_account_bills_archive(limit=str(min(limit, 100)))
        return check_response(resp, "get_bills_archive")

    # ─────────────────────── 持仓模式 ────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def set_position_mode(self, mode: PositionMode) -> bool:
        """
        切换持仓模式。

        Args:
            mode: NET（买卖模式）或 LONG_SHORT（多空模式）

        Returns:
            True 表示切换成功
        """
        pos_mode = "net_mode" if mode == PositionMode.NET else "long_short_mode"
        resp = self._account_api.set_position_mode(posMode=pos_mode)
        check_response(resp, "set_position_mode")
        logger.info("持仓模式已切换为: %s", pos_mode)
        return True

    # ─────────────────────── 杠杆 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def set_leverage(
        self,
        inst_id: str,
        leverage: int,
        margin_mode: MarginMode,
        position_side: PositionSide | None = None,
    ) -> bool:
        """
        设置杠杆（支持9种场景，OKX 规则）。

        OKX 杠杆设置场景说明：
        - 全仓买卖模式（cross + net）：按 instId 设置，posSide 不传
        - 全仓多空模式（cross + long/short）：需传 posSide
        - 逐仓买卖模式（isolated + net）：按 instId 设置，posSide 不传
        - 逐仓多空模式（isolated + long/short）：需传 posSide
        - 现货全仓（cross + SPOT）：按币种设置（ccy 参数）

        Args:
            inst_id:       产品 ID
            leverage:      杠杆倍数
            margin_mode:   CROSS 或 ISOLATED
            position_side: LONG / SHORT（多空模式时需传），NET 时不传
        """
        kwargs = {
            "instId": inst_id,
            "lever": str(leverage),
            "mgnMode": margin_mode_to_okx(margin_mode),
        }
        if position_side and position_side != PositionSide.NET:
            kwargs["posSide"] = pos_side_to_okx(position_side)

        self._throttle.acquire()
        resp = self._account_api.set_leverage(**kwargs)
        check_response(resp, f"set_leverage({inst_id}, {leverage}x)")
        logger.info("杠杆设置成功 %s %dx %s", inst_id, leverage, margin_mode.value)
        return True

    @retry(max_attempts=3, delay=0.5)
    def get_leverage(self, inst_id: str, margin_mode: MarginMode) -> list[dict]:
        """
        查询当前杠杆倍数。

        Returns:
            杠杆信息列表（全仓/逐仓/多仓/空仓等可能有多条）
        """
        resp = self._account_api.get_leverage(
            instId=inst_id,
            mgnMode=margin_mode_to_okx(margin_mode),
        )
        return check_response(resp, f"get_leverage({inst_id})")

    # ─────────────────────── 下单限额 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_max_order_size(
        self,
        inst_id: str,
        td_mode: str,
        ccy: str = "",
        price: str = "",
    ) -> dict:
        """查询最大可买/卖数量。"""
        kwargs = {"instId": inst_id, "tdMode": td_mode}
        if ccy:
            kwargs["ccy"] = ccy
        if price:
            kwargs["px"] = price
        resp = self._account_api.get_max_order_size(**kwargs)
        data = check_response(resp, f"get_max_order_size({inst_id})")
        return data[0] if data else {}

    @retry(max_attempts=3, delay=0.5)
    def get_max_avail_size(self, inst_id: str, td_mode: str) -> dict:
        """查询最大可用（现货/合约）下单量。"""
        resp = self._account_api.get_max_avail_size(instId=inst_id, tdMode=td_mode)
        data = check_response(resp, f"get_max_avail_size({inst_id})")
        return data[0] if data else {}

    @retry(max_attempts=3, delay=0.5)
    def get_max_withdrawal(self, ccy: str = "") -> list[dict]:
        """查询最大可提币数量。"""
        kwargs = {"ccy": ccy} if ccy else {}
        resp = self._account_api.get_max_withdrawal(**kwargs)
        return check_response(resp, "get_max_withdrawal")

    # ─────────────────────── 保证金调整 ──────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def adjust_margin(
        self,
        inst_id: str,
        position_side: PositionSide,
        action: str,
        amount: str,
    ) -> bool:
        """
        调整逐仓保证金。

        Args:
            inst_id:       产品 ID
            position_side: LONG / SHORT / NET
            action:        "add"（增加）或 "reduce"（减少）
            amount:        调整金额

        Returns:
            True 表示调整成功
        """
        resp = self._account_api.adjust_margin(
            instId=inst_id,
            posSide=pos_side_to_okx(position_side),
            type=action,
            amt=amount,
        )
        check_response(resp, f"adjust_margin({inst_id})")
        logger.info("保证金调整成功 %s %s %s", inst_id, action, amount)
        return True

    # ─────────────────────── 手续费 ──────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_fee_rate(self, inst_id: str, market_type: MarketType) -> FeeRate:
        """
        获取手续费费率。

        Args:
            inst_id:     产品 ID
            market_type: SPOT / SWAP / FUTURES 等
        """
        inst_type = market_type_to_okx(market_type)
        self._throttle.acquire()
        resp = self._account_api.get_fee_rates(
            instType=inst_type, instId=inst_id
        )
        data = check_response(resp, f"get_fee_rate({inst_id})")
        if not data:
            from core.models import FeeRate as FR
            from core.enums import Exchange
            return FR(
                exchange=Exchange.OKX,
                inst_type=market_type,
                maker=safe_decimal("-0.0002"),
                taker=safe_decimal("0.0005"),
                level="Lv1",
            )
        return parse_fee_rate(data[0], market_type)

    # ─────────────────────── 资金费率 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_funding_rate(self, inst_id: str) -> FundingRateData:
        """获取当前资金费率（永续合约）。"""
        resp = self._public_api.get_funding_rate(instId=inst_id)
        data = check_response(resp, f"get_funding_rate({inst_id})")
        if not data:
            raise ValueError(f"资金费率数据为空: {inst_id}")
        return parse_funding_rate(data[0])

    @retry(max_attempts=3, delay=1.0)
    def get_funding_rate_history(self, inst_id: str, limit: int = 30) -> list[FundingRateData]:
        """获取历史资金费率。"""
        resp = self._public_api.get_funding_rate_history(
            instId=inst_id, limit=str(min(limit, 100))
        )
        data = check_response(resp, f"get_funding_rate_history({inst_id})")
        return [parse_funding_rate(item) for item in data]

    # ─────────────────────── 标记价格 ────────────────────────────

    @retry(max_attempts=3, delay=0.5)
    def get_mark_price(self, inst_id: str) -> MarkPriceData:
        """获取标记价格（合约）。"""
        inst_type = inst_type_from_id(inst_id)
        resp = self._public_api.get_mark_price(instType=inst_type, instId=inst_id)
        data = check_response(resp, f"get_mark_price({inst_id})")
        if not data:
            raise ValueError(f"标记价格数据为空: {inst_id}")
        return parse_mark_price(data[0])

    @retry(max_attempts=3, delay=0.5)
    def get_price_limit(self, inst_id: str) -> dict:
        """获取合约涨跌停价格限制。"""
        resp = self._public_api.get_price_limit(instId=inst_id)
        data = check_response(resp, f"get_price_limit({inst_id})")
        return data[0] if data else {}
