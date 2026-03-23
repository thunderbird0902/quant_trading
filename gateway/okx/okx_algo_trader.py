"""OKX 策略委托模块 - 止盈止损、计划委托、移动止损、冰山、TWAP"""

from __future__ import annotations

import logging
from decimal import Decimal

from okx import Trade

from core.enums import MarginMode, OrderSide, OrderStatus, OrderType, PositionSide
from core.models import AlgoOrderData, OrderRequest
from utils.helpers import gen_client_order_id, safe_decimal
from utils.retry import retry

from .okx_utils import (
    check_response,
    margin_mode_to_okx,
    order_type_to_okx,
    pos_side_to_okx,
    parse_algo_order,
)

logger = logging.getLogger("trading.okx.algo")


class OKXAlgoTrader:
    """
    OKX 策略委托模块（REST API）。

    支持以下策略委托类型：
    - conditional：止盈止损委托（TP/SL）
    - trigger：计划委托（条件单）
    - move_order_stop：移动止盈止损（Trailing Stop）
    - iceberg：冰山委托
    - twap：TWAP 时间加权委托
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "1"):
        self._trade_api = Trade.TradeAPI(
            api_key, secret_key, passphrase, False, flag
        )

    # ─────────────────────── 止盈止损（TP/SL）────────────────────

    @retry(max_attempts=3, delay=1.0)
    def send_tp_sl_order(
        self,
        inst_id: str,
        side: OrderSide,
        quantity: str,
        margin_mode: MarginMode,
        position_side: PositionSide | None = None,
        tp_trigger_price: str = "",
        tp_order_price: str = "-1",
        sl_trigger_price: str = "",
        sl_order_price: str = "-1",
        client_order_id: str = "",
    ) -> AlgoOrderData:
        """
        提交止盈止损委托（conditional 类型）。

        Args:
            tp_trigger_price: 止盈触发价，为空则不设止盈
            tp_order_price:   止盈委托价（"-1" = 市价止盈）
            sl_trigger_price: 止损触发价，为空则不设止损
            sl_order_price:   止损委托价（"-1" = 市价止损）

        Returns:
            AlgoOrderData
        """
        if not client_order_id:
            client_order_id = gen_client_order_id("tp")

        params = {
            "instId": inst_id,
            "tdMode": margin_mode_to_okx(margin_mode),
            "side": side.value.lower(),
            "ordType": "conditional",
            "sz": quantity,
            "algoClOrdId": client_order_id,
        }
        if position_side and position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(position_side)
        if tp_trigger_price:
            params["tpTriggerPx"] = tp_trigger_price
            params["tpOrdPx"] = tp_order_price
            params["tpTriggerPxType"] = "last"
        if sl_trigger_price:
            params["slTriggerPx"] = sl_trigger_price
            params["slOrdPx"] = sl_order_price
            params["slTriggerPxType"] = "last"

        resp = self._trade_api.place_algo_order(**params)
        data = check_response(resp, f"send_tp_sl_order({inst_id})")
        result = data[0]
        algo_id = result.get("algoId", "")

        logger.info(
            "止盈止损委托成功 %s side=%s sz=%s tp=%s sl=%s algoId=%s",
            inst_id, side.value, quantity, tp_trigger_price, sl_trigger_price, algo_id,
        )
        return self._build_algo_order(algo_id, inst_id, side, OrderType.STOP_LIMIT,
                                       quantity, position_side, client_order_id)

    # ─────────────────────── 计划委托（Trigger）──────────────────

    @retry(max_attempts=3, delay=1.0)
    def send_trigger_order(
        self,
        inst_id: str,
        side: OrderSide,
        quantity: str,
        margin_mode: MarginMode,
        trigger_price: str,
        order_price: str = "-1",
        trigger_price_type: str = "last",
        position_side: PositionSide | None = None,
        client_order_id: str = "",
    ) -> AlgoOrderData:
        """
        提交计划委托（trigger 类型）。

        Args:
            trigger_price:      触发价格
            order_price:        触发后的委托价（"-1" = 市价）
            trigger_price_type: 触发价格类型："last"/"index"/"mark"

        Returns:
            AlgoOrderData
        """
        if not client_order_id:
            client_order_id = gen_client_order_id("tg")

        params = {
            "instId": inst_id,
            "tdMode": margin_mode_to_okx(margin_mode),
            "side": side.value.lower(),
            "ordType": "trigger",
            "sz": quantity,
            "triggerPx": trigger_price,
            "orderPx": order_price,
            "triggerPxType": trigger_price_type,
            "algoClOrdId": client_order_id,
        }
        if position_side and position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(position_side)

        resp = self._trade_api.place_algo_order(**params)
        data = check_response(resp, f"send_trigger_order({inst_id})")
        algo_id = data[0].get("algoId", "")
        logger.info(
            "计划委托成功 %s side=%s triggerPx=%s algoId=%s",
            inst_id, side.value, trigger_price, algo_id,
        )
        return self._build_algo_order(algo_id, inst_id, side, OrderType.TRIGGER,
                                       quantity, position_side, client_order_id)

    # ─────────────────────── 移动止损（Trailing Stop）────────────

    @retry(max_attempts=3, delay=1.0)
    def send_trailing_stop_order(
        self,
        inst_id: str,
        side: OrderSide,
        quantity: str,
        margin_mode: MarginMode,
        callback_ratio: str = "",
        callback_spread: str = "",
        active_price: str = "",
        position_side: PositionSide | None = None,
        client_order_id: str = "",
    ) -> AlgoOrderData:
        """
        提交移动止盈止损委托（move_order_stop 类型）。

        Args:
            callback_ratio:  回调比例（如 "0.05" = 5%），与 callback_spread 二选一
            callback_spread: 回调价差（固定金额）
            active_price:    激活价格（可选）

        Returns:
            AlgoOrderData
        """
        if not callback_ratio and not callback_spread:
            raise ValueError("callback_ratio 和 callback_spread 至少设置一个")
        if not client_order_id:
            client_order_id = gen_client_order_id("ts")

        params = {
            "instId": inst_id,
            "tdMode": margin_mode_to_okx(margin_mode),
            "side": side.value.lower(),
            "ordType": "move_order_stop",
            "sz": quantity,
            "algoClOrdId": client_order_id,
        }
        if callback_ratio:
            params["callbackRatio"] = callback_ratio
        if callback_spread:
            params["callbackSpread"] = callback_spread
        if active_price:
            params["activePx"] = active_price
        if position_side and position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(position_side)

        resp = self._trade_api.place_algo_order(**params)
        data = check_response(resp, f"send_trailing_stop_order({inst_id})")
        algo_id = data[0].get("algoId", "")
        logger.info(
            "移动止损委托成功 %s side=%s callbackRatio=%s algoId=%s",
            inst_id, side.value, callback_ratio, algo_id,
        )
        return self._build_algo_order(algo_id, inst_id, side, OrderType.TRAILING_STOP,
                                       quantity, position_side, client_order_id)

    # ─────────────────────── 冰山委托（Iceberg）──────────────────

    @retry(max_attempts=3, delay=1.0)
    def send_iceberg_order(
        self,
        inst_id: str,
        side: OrderSide,
        quantity: str,
        price_limit: str,
        margin_mode: MarginMode,
        sz_limit: str,
        px_var: str = "",
        px_spread: str = "",
        position_side: PositionSide | None = None,
        client_order_id: str = "",
    ) -> AlgoOrderData:
        """
        提交冰山委托（iceberg 类型）。

        Args:
            quantity:    总委托量
            price_limit: 价格上限
            sz_limit:    单笔委托量
            px_var:      价格幅度（比例），与 px_spread 二选一
            px_spread:   价格幅度（金额）

        Returns:
            AlgoOrderData
        """
        if not px_var and not px_spread:
            raise ValueError("px_var 和 px_spread 至少设置一个")
        if not client_order_id:
            client_order_id = gen_client_order_id("ic")

        params = {
            "instId": inst_id,
            "tdMode": margin_mode_to_okx(margin_mode),
            "side": side.value.lower(),
            "ordType": "iceberg",
            "sz": quantity,
            "pxLimit": price_limit,
            "szLimit": sz_limit,
            "algoClOrdId": client_order_id,
        }
        if px_var:
            params["pxVar"] = px_var
        if px_spread:
            params["pxSpread"] = px_spread
        if position_side and position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(position_side)

        resp = self._trade_api.place_algo_order(**params)
        data = check_response(resp, f"send_iceberg_order({inst_id})")
        algo_id = data[0].get("algoId", "")
        logger.info("冰山委托成功 %s side=%s sz=%s algoId=%s", inst_id, side.value, quantity, algo_id)
        return self._build_algo_order(algo_id, inst_id, side, OrderType.ICEBERG,
                                       quantity, position_side, client_order_id)

    # ─────────────────────── TWAP 委托 ────────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def send_twap_order(
        self,
        inst_id: str,
        side: OrderSide,
        quantity: str,
        price_limit: str,
        margin_mode: MarginMode,
        sz_limit: str,
        time_interval: str,
        px_var: str = "",
        px_spread: str = "",
        position_side: PositionSide | None = None,
        client_order_id: str = "",
    ) -> AlgoOrderData:
        """
        提交 TWAP 时间加权委托（twap 类型）。

        Args:
            sz_limit:      单笔委托量
            time_interval: 下单间隔（秒，如 "60"）
            px_var:        价格幅度比例
            px_spread:     价格幅度金额

        Returns:
            AlgoOrderData
        """
        if not px_var and not px_spread:
            raise ValueError("px_var 和 px_spread 至少设置一个")
        if not client_order_id:
            client_order_id = gen_client_order_id("tw")

        params = {
            "instId": inst_id,
            "tdMode": margin_mode_to_okx(margin_mode),
            "side": side.value.lower(),
            "ordType": "twap",
            "sz": quantity,
            "pxLimit": price_limit,
            "szLimit": sz_limit,
            "timeInterval": time_interval,
            "algoClOrdId": client_order_id,
        }
        if px_var:
            params["pxVar"] = px_var
        if px_spread:
            params["pxSpread"] = px_spread
        if position_side and position_side != PositionSide.NET:
            params["posSide"] = pos_side_to_okx(position_side)

        resp = self._trade_api.place_algo_order(**params)
        data = check_response(resp, f"send_twap_order({inst_id})")
        algo_id = data[0].get("algoId", "")
        logger.info("TWAP委托成功 %s side=%s sz=%s interval=%ss algoId=%s",
                    inst_id, side.value, quantity, time_interval, algo_id)
        return self._build_algo_order(algo_id, inst_id, side, OrderType.TWAP,
                                       quantity, position_side, client_order_id)

    # ─────────────────────── 策略委托管理 ────────────────────────

    @retry(max_attempts=3, delay=1.0)
    def cancel_algo_order(self, algo_id: str, inst_id: str) -> bool:
        """撤销策略委托。"""
        resp = self._trade_api.cancel_algo_order([{"algoId": algo_id, "instId": inst_id}])
        check_response(resp, f"cancel_algo_order({algo_id})")
        logger.info("策略委托撤销成功 algoId=%s", algo_id)
        return True

    @retry(max_attempts=3, delay=1.0)
    def modify_algo_order(
        self,
        algo_id: str,
        inst_id: str,
        new_sz: str = "",
        new_tp_trigger_px: str = "",
        new_sl_trigger_px: str = "",
    ) -> bool:
        """修改策略委托参数。"""
        params = {"instId": inst_id, "algoId": algo_id}
        if new_sz:
            params["newSz"] = new_sz
        if new_tp_trigger_px:
            params["newTpTriggerPx"] = new_tp_trigger_px
        if new_sl_trigger_px:
            params["newSlTriggerPx"] = new_sl_trigger_px
        resp = self._trade_api.amend_algo_order(**params)
        check_response(resp, f"modify_algo_order({algo_id})")
        logger.info("策略委托修改成功 algoId=%s", algo_id)
        return True

    @retry(max_attempts=3, delay=1.0)
    def get_algo_order_detail(self, algo_id: str) -> AlgoOrderData:
        """查询策略委托详情。"""
        resp = self._trade_api.get_algo_order_details(algoId=algo_id)
        data = check_response(resp, f"get_algo_order_detail({algo_id})")
        if not data:
            from core.exceptions import OrderNotFoundError
            raise OrderNotFoundError(algo_id)
        return parse_algo_order(data[0])

    @retry(max_attempts=3, delay=1.0)
    def get_algo_orders(self, order_type: str | None = None) -> list[AlgoOrderData]:
        """
        获取未完成策略委托列表。

        Args:
            order_type: OKX algo ordType（"conditional"/"trigger"/"move_order_stop"/"iceberg"/"twap"）
                        None 则不过滤
        """
        params = {}
        if order_type:
            params["ordType"] = order_type
        resp = self._trade_api.order_algos_list(**params)
        data = check_response(resp, "get_algo_orders")
        return [parse_algo_order(item) for item in data]

    @retry(max_attempts=3, delay=1.0)
    def get_algo_orders_history(self, order_type: str, state: str = "effective") -> list[AlgoOrderData]:
        """
        获取历史策略委托。

        Args:
            order_type: "conditional"/"trigger"/"move_order_stop"/"iceberg"/"twap"
            state:      "effective"（已生效）/"canceled"（已撤销）/"order_failed"（失败）
        """
        resp = self._trade_api.order_algos_history(ordType=order_type, state=state)
        data = check_response(resp, f"get_algo_orders_history({order_type})")
        return [parse_algo_order(item) for item in data]

    # ─────────────────────── 私有工具 ────────────────────────────

    def _build_algo_order(
        self,
        algo_id: str,
        inst_id: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: str,
        position_side: PositionSide | None,
        client_order_id: str,
    ) -> AlgoOrderData:
        """构造策略委托 AlgoOrderData 对象。"""
        from utils.helpers import now_utc
        from core.enums import Exchange
        return AlgoOrderData(
            algo_id=algo_id,
            inst_id=inst_id,
            exchange=Exchange.OKX,
            side=side,
            order_type=order_type,
            quantity=safe_decimal(quantity),
            status=OrderStatus.SUBMITTED,
            create_time=now_utc(),
            position_side=position_side,
            extra={"algoClOrdId": client_order_id},
        )
