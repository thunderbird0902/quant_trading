"""
IB Gateway — Interactive Brokers（美股、期权、期货、外汇）

当前状态：未实现（接口预留）。

如果在 settings.yaml 中将 gateways.ib.enabled 设为 true，
任何尝试实例化 IBGateway 的操作都会立即抛出 NotImplementedError，
并给出明确的提示，而不是静默失败。

实现路线参考：
  - 依赖 ib_insync 或 ibapi 官方 SDK
  - 需要运行中的 TWS 或 IB Gateway 进程（本地端口连接）
  - 配置项：host（默认 127.0.0.1）、port（TWS 模拟 7497 / 实盘 7496）、client_id
  - 合约数据通过 reqContractDetails 获取
"""

from core.event_bus import EventBus


class IBGateway:
    """
    Interactive Brokers 网关占位符（未实现）。

    配置中若启用 IB，实例化时会立即抛出 NotImplementedError，
    避免代码默默走到错误路径或产生空指针问题。
    """

    exchange = None  # IB 覆盖多个市场（SMART 路由）

    def __init__(self, event_bus: EventBus, config: dict):
        raise NotImplementedError(
            "IB Gateway 尚未实现。\n"
            "请在 config/settings.yaml 中将 gateways.ib.enabled 设为 false，\n"
            "或实现 IBGateway（参考 gateway/base_gateway.py 的抽象接口）。\n"
            "配置参考：config/ib_config.yaml\n"
            "依赖建议：pip install ib_insync"
        )
