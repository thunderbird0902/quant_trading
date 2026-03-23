# CTP 期货 Gateway 实现说明

## 概述

本目录预留给国内期货 CTP（穿透式监管）协议的 Gateway 实现。
CTP 是国内商品期货（上期所、大商所、郑商所、中金所）的标准接口协议，
通过 vn.py 提供的 vnpy_ctp 适配器接入。

---

## 依赖安装

```bash
# vn.py CTP 接口（包含编译好的 .so/.pyd）
pip install vnpy_ctp

# 或使用 vnpy 完整版
pip install vnpy
```

注意：vnpy_ctp 依赖 Windows 平台（官方 CTP API 仅支持 Windows/Linux）。
macOS 用户需要使用 Docker 或远程 Linux 环境。

---

## 账户模式与连接

CTP 需要期货公司的服务器地址（行情服务器 + 交易服务器各一个）：

```python
config = {
    "broker_id": "9999",                    # 期货公司代码（9999=仿真）
    "td_address": "tcp://180.168.146.187:10101",  # 交易服务器
    "md_address": "tcp://180.168.146.187:10111",  # 行情服务器
    "user_id": "你的账号",
    "password": "你的密码",
    "app_id": "simnow_client_test",         # 仿真盘固定值
    "auth_code": "0000000000000000",         # 仿真盘固定值
}
```

仿真账户申请：SimNow（https://www.simnow.com.cn）

---

## 需要实现的核心方法

### 连接管理
```python
def connect(self) -> None:
    # CTP 需要两步：
    # 1. 连接行情服务器（行情 API）
    # 2. 连接交易服务器（交易 API）+ 认证 + 登录
    # vnpy_ctp 封装了这些步骤

def disconnect(self) -> None:
    # 注销并释放 API 对象
```

### 行情数据
```python
def get_instruments(self, market_type: MarketType) -> list[Instrument]:
    # CTP 在登录成功后自动推送合约信息
    # 通过 OnRspQryInstrument 回调获取
    # 合约代码：rb2501（螺纹钢 2501 合约）

def subscribe_ticker(self, inst_id: str) -> None:
    # mdApi.SubscribeMarketData(["rb2501"])
    # 行情通过 OnRtnDepthMarketData 回调推送

def get_klines(self, inst_id: str, interval: str, limit: int) -> list[BarData]:
    # CTP 本身不提供历史 K 线
    # 需要通过数据提供商（如 tqsdk、rqdata）获取
    # 或自己从 Tick 合成
```

### 账户
```python
def get_balance(self) -> BalanceData:
    # 查询资金账户：tdApi.ReqQryTradingAccount()
    # 通过 OnRspQryTradingAccount 回调获取
    # 关键字段：Balance（总资金）, Available（可用）, FrozenMargin（冻结保证金）

def get_positions(self) -> list[PositionData]:
    # 查询持仓：tdApi.ReqQryInvestorPosition()
    # 通过 OnRspQryInvestorPosition 回调获取
    # 注意：CTP 持仓分今仓/昨仓，多头/空头分别查询
```

### 交易
```python
def send_order(self, request: OrderRequest) -> OrderData:
    # CTP 下单：tdApi.ReqOrderInsert(inputOrder, requestId)
    # 关键字段映射：
    #   side BUY  → Direction.THOST_FTDC_D_Buy
    #   side SELL → Direction.THOST_FTDC_D_Sell
    #   OPEN  → CombOffsetFlag.THOST_FTDC_OF_Open
    #   CLOSE → CombOffsetFlag.THOST_FTDC_OF_Close / CloseToday

def cancel_order(self, order_id: str, inst_id: str) -> bool:
    # tdApi.ReqOrderAction(inputOrderAction, requestId)
```

---

## 数据模型映射

| 统一字段 | CTP 字段 |
|---------|---------|
| inst_id | InstrumentID（如 "rb2501"）|
| exchange | Exchange.CTP |
| market_type | MarketType.FUTURES |
| base_ccy | InstrumentID 前缀（如 "rb"）|
| quote_ccy | "CNY" |
| tick_size | PriceTick |
| lot_size | VolumeMultiple |
| last_price | LastPrice |
| open_interest | OpenInterest（持仓量）|

### 开平仓映射（CTP 特有）

CTP 区分**开仓**（Open）和**平仓**（Close/CloseToday/CloseYesterday），
需要在 OrderRequest.extra 中传递 offset 信息：

```python
# 统一接口 → CTP 映射：
# position_side=LONG + side=BUY    → 买开（多头开仓）
# position_side=LONG + side=SELL   → 卖平（多头平仓）
# position_side=SHORT + side=SELL  → 卖开（空头开仓）
# position_side=SHORT + side=BUY   → 买平（空头平仓）
```

---

## 示例代码框架

```python
# gateway/ctp/ctp_gateway.py

from vnpy_ctp import CtpGateway as VnpyCTPGateway
from gateway.base_gateway import BaseGateway

class CTPGateway(BaseGateway):
    exchange = Exchange.CTP

    def __init__(self, event_bus, config):
        super().__init__(event_bus, config)
        # 可以直接包装 vnpy 的 CTP Gateway
        # 或使用原生 CTP API

    def connect(self):
        # 连接行情和交易服务器
        pass

    def send_order(self, request: OrderRequest) -> OrderData:
        # 转换为 CTP 格式并下单
        pass
```

---

## 注意事项

1. **平仓规则**：上期所区分平今仓（CloseToday）和平昨仓（CloseYesterday），
   需要根据持仓来源选择正确的 offset
2. **手续费**：期货手续费按手收取（固定值），而非百分比
3. **最小变动**：合约有最小变动价位（PriceTick），下单时必须是整数倍
4. **夜盘**：部分商品有夜盘交易，需处理跨日行情
5. **资金划转**：CTP 没有内置划转接口，需联系期货公司
6. **历史数据**：CTP 不提供历史数据，需通过第三方（tqsdk、rqdata 等）获取

---

## 相关资源

- [vn.py 文档](https://www.vnpy.com/docs)
- [SimNow 仿真平台](https://www.simnow.com.cn)
- [CTP API 官方文档](http://www.sfit.com.cn/5_2_DocumentDown.htm)
- [OpenCTP](https://github.com/openctp/openctp)（提供各交易所的仿真环境）
