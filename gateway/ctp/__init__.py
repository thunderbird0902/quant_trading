"""
CTP Gateway — 国内商品期货（上期所、郑商所、大商所、中金所）

当前状态：未实现（接口预留）。

如果在 settings.yaml 中将 gateways.ctp.enabled 设为 true，
任何尝试实例化 CTPGateway 的操作都会立即抛出 NotImplementedError，
并给出明确的提示，而不是静默失败或产生难以排查的错误。

实现路线参考：
  - 依赖 vn.py 的 pyctp / openctp 封装
  - 需要 CTP 账户、td/md 服务器地址、broker_id
  - 合约规格、保证金率等数据从 CTP 查询接口获取
"""

from core.event_bus import EventBus


class CTPGateway:
    """
    CTP 期货网关占位符（未实现）。

    配置中若启用 CTP，实例化时会立即抛出 NotImplementedError，
    避免代码默默走到错误路径或产生空指针问题。
    """

    exchange = None  # 未确定，CTP 覆盖多个交易所

    def __init__(self, event_bus: EventBus, config: dict):
        raise NotImplementedError(
            "CTP Gateway 尚未实现。\n"
            "请在 config/settings.yaml 中将 gateways.ctp.enabled 设为 false，\n"
            "或实现 CTPGateway（参考 gateway/base_gateway.py 的抽象接口）。\n"
            "配置参考：config/ctp_config.yaml"
        )
