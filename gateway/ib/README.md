# Interactive Brokers (IB) Gateway 实现说明

## 概述

本目录预留给 Interactive Brokers (IB) 的 Gateway 实现。
IB Gateway 将继承 `gateway/base_gateway.py` 中的 `BaseGateway` 基类，
实现所有抽象方法，以支持美股、期权、期货等多品种交易。

---

## 依赖安装

```bash
# 官方 Python API（需要先安装 TWS 或 IB Gateway 客户端）
pip install ibapi

# 或使用社区封装库（推荐，更易用）
pip install ib_insync
```

IB 客户端下载：https://www.interactivebrokers.com/en/trading/ibgateway-stable.php

---

## 账户模式与连接

IB 使用 TWS（Trader Workstation）或 IB Gateway 作为本地代理：

```python
# TWS 默认端口: 7496（实盘）/ 7497（模拟盘）
# IB Gateway 默认端口: 4001（实盘）/ 4002（模拟盘）

config = {
    "host": "127.0.0.1",
    "port": 7497,       # 模拟盘
    "client_id": 1,     # 客户端 ID（同一账户可多客户端连接）
}
```

---

## 需要实现的核心方法

### 连接管理
```python
def connect(self) -> None:
    # 连接 TWS/IB Gateway
    # ib_insync: ib.connect(host, port, clientId)

def disconnect(self) -> None:
    # ib.disconnect()
```

### 行情数据
```python
def get_instruments(self, market_type: MarketType) -> list[Instrument]:
    # IB 使用 Contract 对象描述产品
    # 股票: Stock("AAPL", "SMART", "USD")
    # 期货: Future("ES", "202412", "CME")
    # 期权: Option("AAPL", "202412", 150, "C", "SMART")
    # ib.reqContractDetails(contract) 获取合约详情

def get_ticker(self, inst_id: str) -> TickData:
    # ib.reqMktData(contract) 获取实时行情
    # 或 ib.ticker(contract)

def get_klines(self, inst_id: str, interval: str, limit: int) -> list[BarData]:
    # ib.reqHistoricalData(contract, endDateTime, durationStr, barSizeSetting, ...)
    # barSizeSetting 映射：
    #   "1m" → "1 min"
    #   "1H" → "1 hour"
    #   "1D" → "1 day"
```

### 账户
```python
def get_balance(self) -> BalanceData:
    # ib.accountValues() 或 ib.accountSummary()
    # 关键字段：TotalCashValue, NetLiquidation, AvailableFunds

def get_positions(self) -> list[PositionData]:
    # ib.positions() 或 ib.portfolio()
```

### 交易
```python
def send_order(self, request: OrderRequest) -> OrderData:
    # IB 订单类型映射：
    # LIMIT → LimitOrder(action, quantity, price)
    # MARKET → MarketOrder(action, quantity)
    # STOP_LIMIT → StopLimitOrder(action, quantity, stop_price, limit_price)
    # 注意：IB 用 "BUY"/"SELL" 而非 buy/sell

def cancel_order(self, order_id: str, inst_id: str) -> bool:
    # ib.cancelOrder(trade.order)
```

---

## 数据模型映射

| 统一字段 | IB 字段 |
|---------|---------|
| inst_id | symbol-exchange-currency（如 "AAPL-SMART-USD"）|
| exchange | Exchange.IB |
| market_type | MarketType.STOCK / FUTURES / OPTION |
| base_ccy | symbol（如 "AAPL"）|
| quote_ccy | currency（如 "USD"）|
| tick_size | minTick |
| lot_size | minSize |
| last_price | last |
| bid_price | bid |
| ask_price | ask |

---

## 示例代码框架

```python
# gateway/ib/ib_gateway.py

from ib_insync import IB, Stock, Future, LimitOrder, MarketOrder
from gateway.base_gateway import BaseGateway

class IBGateway(BaseGateway):
    exchange = Exchange.IB

    def __init__(self, event_bus, config):
        super().__init__(event_bus, config)
        self._ib = IB()

    def connect(self):
        self._ib.connect(
            host=self.config["host"],
            port=self.config["port"],
            clientId=self.config["client_id"],
        )
        self._connected = True

    def send_order(self, request: OrderRequest) -> OrderData:
        contract = self._make_contract(request.inst_id)
        order = LimitOrder(
            action=request.side.value,
            totalQuantity=float(request.quantity),
            lmtPrice=float(request.price),
        )
        trade = self._ib.placeOrder(contract, order)
        return self._parse_trade(trade)
```

---

## 注意事项

1. **异步模型**：ib_insync 基于 asyncio，与本系统的异步框架天然兼容
2. **市场时段**：美股有明确开盘/收盘时间，需处理 PreMarket/AfterHours
3. **费用计算**：IB 手续费复杂，建议直接查询账单而非自己计算
4. **合约 ID**：IB 使用 conId（数字）作为唯一标识，inst_id 需做转换
5. **数据订阅限制**：标准账户同时最多 100 个实时行情订阅
