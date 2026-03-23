# 量化交易系统

多市场量化交易系统，当前支持 OKX 加密货币，架构设计支持后续接入 Interactive Brokers（美股）和 CTP（国内期货）。

## 特色

- **抽象层与实现层分离**：所有交易所共用同一套抽象接口（`BaseGateway`），各市场实现自己的适配器
- **统一数据模型**：行情、订单、持仓等数据结构统一，策略代码跨市场复用
- **事件驱动架构**：核心引擎基于事件总线（`EventBus`），模块间完全解耦
- **插件化扩展**：新增市场 = 新增一个 gateway 目录，无需改动核心代码
- **配置驱动**：YAML 配置文件 + 环境变量，灵活切换实盘/模拟盘

---

## 快速开始

### 1. 安装依赖

```bash
cd quant_trading
pip install -r requirements.txt
```

### 2. 配置 API Key（环境变量）

```bash
export OKX_API_KEY=你的API_KEY
export OKX_SECRET_KEY=你的SECRET_KEY
export OKX_PASSPHRASE=你的PASSPHRASE
export OKX_FLAG=1   # 1=模拟盘（推荐先用模拟盘）
```

### 3. 运行示例

```bash
# 连接并查看账户
python examples/01_connect_okx.py

# 获取行情数据
python examples/02_market_data.py

# 现货交易全流程
python examples/03_spot_trading.py

# 衍生品交易全流程
python examples/04_derivatives_trading.py

# 策略委托（止盈止损）
python examples/05_algo_orders.py

# 风控演示
python examples/06_risk_management.py

# WebSocket 实时数据
python examples/07_websocket_realtime.py

# 完整工作流
python examples/08_full_workflow.py
```

### 4. 运行测试

```bash
# 单元测试（无需网络）
pytest tests/test_models.py tests/test_okx_market_data.py tests/test_risk_engine.py -v

# 集成测试（需要 API Key）
pytest tests/test_okx_trader.py -v
```

---

## 项目结构

```
quant_trading/
├── config/             # YAML 配置文件
├── core/               # 核心层（引擎、事件总线、数据模型、枚举）
├── gateway/            # 网关层
│   ├── base_gateway.py # 抽象基类
│   ├── okx/            # OKX 实现
│   ├── ib/             # IB 预留（含 README）
│   └── ctp/            # CTP 预留（含 README）
├── risk/               # 风控层
├── strategy_core/      # 策略层（抽象基类 + 策略实现）
├── strategy_runners/   # 策略入口（回测 / 实盘脚本）
├── data/               # 数据层（数据库、录制、加载）
├── utils/              # 工具层
├── examples/           # 示例脚本（01~08）
└── tests/              # 测试
```

---

## 新增市场

新增一个市场只需：

1. 在 `gateway/` 下新建目录（如 `gateway/ib/`）
2. 创建 `IBGateway` 类，继承 `BaseGateway`
3. 实现所有 `@abstractmethod` 方法
4. 在 `config/settings.yaml` 中添加配置
5. 在 `core/enums.py` 的 `Exchange` 枚举中添加新交易所

**核心代码零改动**。

---

## 风控规则

| 规则 | 默认值 | 说明 |
|------|--------|------|
| 每日最大亏损 | 5% | 超过则停止所有交易 |
| 单笔最大亏损 | 2% | 止损设置超限则拒绝下单 |
| 单品种最大仓位 | 30% | 持仓价值占总权益上限 |
| 最大连续亏损 | 5次 | 超过则停止所有交易 |
| 价格偏离限制 | 10% | 委托价偏离最新价超限则拒绝 |

配置文件：`config/settings.yaml` → `risk` 节。

---

## 支持的交易类型

| 类型 | OKX | IB | CTP |
|------|-----|-----|-----|
| 现货买卖 | ✅ | ✅ | ❌ |
| 永续合约 | ✅ | ✅ | ✅ |
| 交割期货 | ✅ | ✅ | ✅ |
| 期权 | ✅ | ✅ | ❌ |
| 止盈止损 | ✅ | ✅ | ❌ |
| 冰山委托 | ✅ | ✅ | ❌ |
| TWAP | ✅ | ✅ | ❌ |

---

## 环境变量说明

| 变量 | 说明 |
|------|------|
| `OKX_API_KEY` | OKX API Key |
| `OKX_SECRET_KEY` | OKX Secret Key |
| `OKX_PASSPHRASE` | OKX Passphrase |
| `OKX_FLAG` | 0=实盘, 1=模拟盘（默认 1） |
| `QUANT_LOG_DIR` | 日志目录（默认 `logs/`） |
