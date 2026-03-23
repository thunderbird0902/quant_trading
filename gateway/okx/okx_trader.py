"""OKX 交易模块 - 现货 + 衍生品下单、撤单、查询"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from okx import Trade

from core.enums import MarginMode, MarketType, OrderSide, OrderStatus, OrderType, PositionSide
from core.models import OrderData, OrderRequest, TradeData
from utils.helpers import gen_client_order_id, safe_decimal
from utils.retry import retry

from .okx_utils import (
    _Throttle,
    check_response,
    check_batch_response,
    margin_mode_to_okx,
    market_type_to_okx,
    order_type_to_okx,
    pos_side_to_okx,
    parse_order,
    parse_trade,
)

logger = logging.getLogger("trading.okx.trader")


class OKXTrader:
    """
    OKX 交易模块（REST API）。

    支持现货和衍生品的完整下单流程：
    - 限价 / 市价 / FOK / IOC
    - 批量下单 / 批量撤单 / 修改订单
    - 订单查询（单笔、未完成、历史）
    - 成交明细查询
    - 倒计时全撤
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "1"):
        self._trade_api = Trade.TradeAPI(
            api_key, secret_key, passphrase, False, flag
        )
        # 交易类端点限频：20 次/2 秒（OKX 官方限制）
        self._throttle = _Throttle(max_calls=20, period=2.0)

    # ─────────────────────── 下单 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def send_order(self, request: OrderRequest) -> OrderData:
        """
        提交单笔订单（现货 + 衍生品通用）。

        现货买入：tdMode="cash"，市价买入需传 tgtCcy="quote_ccy"
        衍生品：tdMode="cross"/"isolated"，需传 posSide（多空模式）

        Args:
            request: 统一下单请求

        Returns:
            已提交的 OrderData（status=SUBMITTED）
        """
        if not request.client_order_id:
            request.client_order_id = gen_client_order_id()

        self._throttle.acquire()   # 交易端点限频
        td_mode = margin_mode_to_okx(request.margin_mode)
        params = {
            "instId": request.inst_id,
            "tdMode": td_mode,
            "side": request.side.value.lower(),
            "ordType": order_type_to_okx(request.order_type),
            "sz": str(request.quantity),
            "clOrdId": request.client_order_id,
        }

        # 价格：市价单不传 px
        if request.price is not None and request.order_type != OrderType.MARKET:
            params["px"] = str(request.price)

        # 持仓方向（多空模式）
        if request.position_side and request.position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(request.position_side)

        # 现货市价买入按金额下单
        if (request.order_type == OrderType.MARKET
                and request.side == OrderSide.BUY
                and request.margin_mode == MarginMode.CASH):
            params["tgtCcy"] = "quote_ccy"  # 按计价货币金额

        # 现货市价卖出按数量下单
        if (request.order_type == OrderType.MARKET
                and request.side == OrderSide.SELL
                and request.margin_mode == MarginMode.CASH):
            params["tgtCcy"] = "base_ccy"   # 按基础货币数量

        # 附加止盈止损
        if request.take_profit_price:
            params["tpTriggerPx"] = str(request.take_profit_price)
            params["tpOrdPx"] = "-1"  # -1 = 市价止盈
        if request.stop_loss_price:
            params["slTriggerPx"] = str(request.stop_loss_price)
            params["slOrdPx"] = "-1"  # -1 = 市价止损

        # 合并 extra 参数
        params.update(request.extra)

        resp = self._trade_api.place_order(**params)
        data = check_response(resp, f"send_order({request.inst_id})")
        result = data[0]

        logger.info(
            "下单成功 %s %s %s px=%s sz=%s ordId=%s",
            request.inst_id,
            request.side.value,
            request.order_type.value,
            request.price,
            request.quantity,
            result.get("ordId"),
        )

        # 构造 OrderData（状态从 API 返回补全）
        order = self._build_submitted_order(request, result.get("ordId", ""))
        return order

    @retry(max_attempts=3, delay=1.0)
    def batch_send_orders(self, requests: list[OrderRequest]) -> list[OrderData]:
        """
        批量下单（最多 20 个）。

        Returns:
            OrderData 列表，与 requests 一一对应。
            部分失败的订单 status=REJECTED，不抛异常。
        """
        from utils.helpers import chunk_list
        results: list[OrderData] = []

        for batch in chunk_list(requests, 20):
            self._throttle.acquire()   # 每批请求前限频
            orders_params = []
            for req in batch:
                if not req.client_order_id:
                    req.client_order_id = gen_client_order_id()
                orders_params.append({
                    "instId": req.inst_id,
                    "tdMode": margin_mode_to_okx(req.margin_mode),
                    "side": req.side.value.lower(),
                    "ordType": order_type_to_okx(req.order_type),
                    "sz": str(req.quantity),
                    "px": str(req.price) if req.price else "",
                    "clOrdId": req.client_order_id,
                    "posSide": pos_side_to_okx(req.position_side) if req.position_side else "",
                })

            resp = self._trade_api.place_multiple_orders(orders_params)
            data = check_batch_response(resp, "batch_send_orders")

            for req, result in zip(batch, data):
                if result.get("sCode") == "0":
                    results.append(self._build_submitted_order(req, result.get("ordId", "")))
                    logger.info("批量下单成功 %s ordId=%s", req.inst_id, result.get("ordId"))
                else:
                    logger.warning(
                        "批量下单部分失败 %s: [%s] %s",
                        req.inst_id, result.get("sCode"), result.get("sMsg"),
                    )
                    results.append(self._build_rejected_order(req, result.get("sMsg", "")))

        return results

    # ─────────────────────── 撤单 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def cancel_order(self, order_id: str, inst_id: str) -> bool:
        """撤销单笔订单。"""
        self._throttle.acquire()
        resp = self._trade_api.cancel_order(instId=inst_id, ordId=order_id)
        check_response(resp, f"cancel_order({order_id})")
        logger.info("撤单成功 ordId=%s instId=%s", order_id, inst_id)
        return True

    @retry(max_attempts=3, delay=1.0)
    def batch_cancel_orders(self, orders: list[tuple[str, str]]) -> list[bool]:
        """
        批量撤单。

        Args:
            orders: [(order_id, inst_id), ...]

        Returns:
            bool 列表，True 表示该笔撤单成功
        """
        from utils.helpers import chunk_list
        results: list[bool] = []

        for batch in chunk_list(orders, 20):
            self._throttle.acquire()
            cancel_params = [{"instId": inst_id, "ordId": order_id} for order_id, inst_id in batch]
            resp = self._trade_api.cancel_multiple_orders(cancel_params)
            data = check_batch_response(resp, "batch_cancel_orders")
            for result in data:
                success = result.get("sCode") == "0"
                results.append(success)
                if not success:
                    logger.warning("批量撤单部分失败: [%s] %s", result.get("sCode"), result.get("sMsg"))

        return results

    # ─────────────────────── 修改订单 ────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def modify_order(
        self,
        order_id: str,
        inst_id: str,
        new_price: Decimal | None = None,
        new_quantity: Decimal | None = None,
    ) -> bool:
        """修改订单价格/数量（OKX 不撤单直接改）。"""
        params = {"instId": inst_id, "ordId": order_id}
        if new_price is not None:
            params["newPx"] = str(new_price)
        if new_quantity is not None:
            params["newSz"] = str(new_quantity)
        resp = self._trade_api.amend_order(**params)
        check_response(resp, f"modify_order({order_id})")
        logger.info("改单成功 ordId=%s px=%s sz=%s", order_id, new_price, new_quantity)
        return True

    @retry(max_attempts=3, delay=1.0)
    def batch_modify_orders(self, amendments: list[dict]) -> list[bool]:
        """
        批量修改订单。

        Args:
            amendments: [{"ordId": ..., "instId": ..., "newPx": ..., "newSz": ...}, ...]
        """
        from utils.helpers import chunk_list
        results: list[bool] = []
        for batch in chunk_list(amendments, 20):
            resp = self._trade_api.amend_multiple_orders(batch)
            data = check_batch_response(resp, "batch_modify_orders")
            for result in data:
                results.append(result.get("sCode") == "0")
        return results

    # ─────────────────────── 倒计时全撤 ──────────────────────────

    def cancel_all_after(self, timeout_seconds: int) -> bool:
        """
        设置倒计时全撤（Dead Man's Switch）。

        Args:
            timeout_seconds: 超时秒数（10-120），0 表示取消倒计时

        Returns:
            True 表示设置成功
        """
        resp = self._trade_api.cancel_all_after(timeOut=str(timeout_seconds))
        check_response(resp, "cancel_all_after")
        if timeout_seconds > 0:
            logger.info("倒计时全撤已设置: %d 秒", timeout_seconds)
        else:
            logger.info("倒计时全撤已取消")
        return True

    # ─────────────────────── 查询 ────────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def get_order(self, order_id: str, inst_id: str) -> OrderData:
        """查询单笔订单详情。支持 ordId 或 clOrdId。"""
        # 先尝试 ordId，若不是纯数字则尝试 clOrdId
        if order_id.isdigit():
            resp = self._trade_api.get_order(instId=inst_id, ordId=order_id)
        else:
            resp = self._trade_api.get_order(instId=inst_id, clOrdId=order_id)
        data = check_response(resp, f"get_order({order_id})")
        if not data:
            from core.exceptions import OrderNotFoundError
            raise OrderNotFoundError(order_id)
        return parse_order(data[0])

    @retry(max_attempts=3, delay=1.0)
    def get_open_orders(self, inst_id: str | None = None) -> list[OrderData]:
        """获取未完成订单。"""
        kwargs = {}
        if inst_id:
            kwargs["instId"] = inst_id
        resp = self._trade_api.get_order_list(**kwargs)
        data = check_response(resp, "get_open_orders")
        return [parse_order(item) for item in data]

    @retry(max_attempts=3, delay=1.0)
    def get_order_history(self, market_type: MarketType, days: int = 7) -> list[OrderData]:
        """
        获取历史订单（近 7 天用 get_orders_history，超过用 archive）。
        """
        inst_type = market_type_to_okx(market_type)
        if days <= 7:
            resp = self._trade_api.get_orders_history(instType=inst_type, limit="100")
        else:
            resp = self._trade_api.get_orders_history_archive(instType=inst_type, limit="100")
        data = check_response(resp, f"get_order_history({inst_type})")
        return [parse_order(item) for item in data]

    @retry(max_attempts=3, delay=1.0)
    def get_trade_history(self, market_type: MarketType, days: int = 7) -> list[TradeData]:
        """获取成交明细（近 3 天用 get_fills，超过用 get_fills_history）。"""
        inst_type = market_type_to_okx(market_type)
        if days <= 3:
            resp = self._trade_api.get_fills(instType=inst_type, limit="100")
        else:
            resp = self._trade_api.get_fills_history(instType=inst_type, limit="100")
        data = check_response(resp, f"get_trade_history({inst_type})")
        return [parse_trade(item) for item in data]

    # ─────────────────────── 私有工具 ────────────────────────────

    def _build_submitted_order(self, request: OrderRequest, order_id: str) -> OrderData:
        """根据 OrderRequest 和 OKX 返回的 ordId 构造 SUBMITTED 状态的 OrderData。"""
        from utils.helpers import now_utc
        return OrderData(
            order_id=order_id,
            client_order_id=request.client_order_id or "",
            inst_id=request.inst_id,
            exchange=request.exchange,
            side=request.side,
            order_type=request.order_type,
            price=request.price or Decimal("0"),
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            filled_price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
            fee=Decimal("0"),
            pnl=Decimal("0"),
            create_time=now_utc(),
            update_time=now_utc(),
            position_side=request.position_side,
        )

    def _build_rejected_order(self, request: OrderRequest, msg: str) -> OrderData:
        """构造 REJECTED 状态的 OrderData（批量下单部分失败时使用）。"""
        from utils.helpers import now_utc
        return OrderData(
            order_id="",
            client_order_id=request.client_order_id or "",
            inst_id=request.inst_id,
            exchange=request.exchange,
            side=request.side,
            order_type=request.order_type,
            price=request.price or Decimal("0"),
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            filled_price=Decimal("0"),
            status=OrderStatus.REJECTED,
            fee=Decimal("0"),
            pnl=Decimal("0"),
            create_time=now_utc(),
            update_time=now_utc(),
            extra={"reject_reason": msg},
        )
