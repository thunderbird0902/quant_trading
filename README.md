# 量化交易系统

多市场量化交易系统，支持 **OKX 加密货币交易所**（已实盘验证），架构设计支持后续接入 Interactive Brokers（美股）和 CTP（国内期货）。

## 目录

- [特性](#特性)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [策略运行](#策略运行)
- [脚本工具](#脚本工具)
- [项目结构](#项目结构)
- [风控规则](#风控规则)
- [Telegram 通知](#telegram-通知)
- [新增交易所](#新增交易所)
- [环境变量](#环境变量)

---

## 特性

- **事件驱动架构**：基于 `EventBus` 事件总线，行情/交易/风控完全解耦
- **多市场统一接口**：`BaseGateway` 抽象层，各交易所实现适配器即可接入
- **双向持仓支持**：OKX 双向持仓模式（对冲/套利策略可用）
- **完整回测引擎**：支持历史 K 线回测 + Walk-Forward 验证 + HTML 交互式报告
- **多层风控体系**：亏损限额 / 仓位限制 / 价格偏离 / Emergency Stop
- **实时通知**：Telegram Bot 推送成交、持仓、风控事件
- **插件化扩展**：新增交易所只需新增 `gateway/xxx/` 目录，核心代码零改动

---

## 快速开始

### 1. 安装依赖

```bash
cd quant_trading
pip install -r requirements.txt
```

### 2. 配置 API Key

**方式一：环境变量（推荐）**
```bash
export OKX_API_KEY=你的API_KEY
export OKX_SECRET_KEY=你的SECRET_KEY
export OKX_PASSPHRASE=你的PASSPHRASE
export OKX_FLAG=1   # 1=模拟盘，0=实盘
```

**方式二：写入配置文件**（仅本地使用，勿上传 GitHub）
```bash
# 复制配置模板
cp config/example.yaml config/okx_config.yaml

# 编辑 config/okx_config.yaml，填入真实 API Key
```

### 3. 运行示例

```bash
# 01 连接 OKX 并查看账户
python examples/01_connect_okx.py

# 02 获取行情数据
python examples/02_market_data.py

# 03 现货交易全流程
python examples/03_spot_trading.py

# 04 衍生品（合约）交易全流程
python examples/04_derivatives_trading.py

# 05 策略委托（止盈止损）
python examples/05_algo_orders.py

# 06 风控演示
python examples/06_risk_management.py

# 07 WebSocket 实时数据
python examples/07_websocket_realtime.py

# 08 完整工作流（连接 → 订阅 → 下单 → 风控 → 日志）
python examples/08_full_workflow.py
```

### 4. 运行测试

```bash
# 单元测试（无需网络）
pytest tests/test_models.py tests/test_okx_market_data.py tests/test_risk_engine.py -v

# 集成测试（需要真实 API Key）
pytest tests/test_okx_trader.py -v
```

---

## 配置说明

### settings.yaml（系统主配置）

```yaml
system:
  mode: "demo"           # demo=模拟盘 / live=实盘
  log_level: "INFO"     # DEBUG / INFO / WARNING / ERROR
  log_dir: "logs"
  database: "sqlite"     # sqlite / postgresql

telegram:
  enabled: true
  bot_token: ""          # 从 @BotFather 获取
  chat_id: ""            # 从 @userinfobot 获取
  notify_trade: true      # 推送成交
  notify_position: true   # 推送持仓变化
  notify_risk: true       # 推送风控事件
  equity_interval: 300     # 定时权益推送间隔（秒），0=禁用

risk:
  enabled: true
  max_daily_loss_pct: 0.05      # 每日最大亏损 5%
  max_single_loss_pct: 0.02     # 单笔最大亏损 2%
  max_position_pct: 0.30        # 单品种最大仓位 30%
  max_total_position_pct: 0.90  # 总仓位最大 90%
  max_consecutive_losses: 5      # 最大连续亏损次数
  price_deviation_limit: 0.10   # 价格偏离限制 10%
  max_drawdown_pct: 0.15       # 最大回撤保护（0=禁用）
  hedge_mode: true              # 双向持仓模式
```

### okx_config.yaml（OKX 专属配置）

```yaml
okx:
  api_key: ""
  secret_key: ""
  passphrase: ""
  flag: "1"            # 0=实盘, 1=模拟盘
  spot_whitelist:
    - "BTC-USDT"
    - "ETH-USDT"
    - "SOL-USDT"
  swap_whitelist:
    - "BTC-USDT-SWAP"
    - "ETH-USDT-SWAP"
  default_leverage: 5
  default_margin_mode: "cross"
```

---

## 策略运行

### 策略列表

| 策略 | 文件 | 说明 |
|------|------|------|
| 双均线 | `strategy_runners/double_ma/` | 趋势跟踪，适用于 1H/4H |
| RSI | `strategy_runners/rsi/` | 超买超卖均值回归 |

### 回测

```bash
# 双均线回测（默认参数）
python -m strategy_runners.double_ma.backtest

# RSI 回测
python -m strategy_runners.rsi.backtest

# 生成 HTML 报告
python -m strategy_runners.double_ma.backtest --generate-report

# 覆盖参数
python -m strategy_runners.double_ma.backtest \
    --fast-period 10 --slow-period 30 --position-pct 0.95

# 指定时间范围
START_DATE=2024-01-01 END_DATE=2024-12-31 python -m strategy_runners.rsi.backtest
```

### 实盘

```bash
# 双均线实盘（模拟盘）
python -m strategy_runners.double_ma.live

# RSI 实盘
python -m strategy_runners.rsi.live

# 小仓位先验证！
POSITION_PCT=0.05 python -m strategy_runners.double_ma.live

# 调整风控参数
RISK_MAX_DAILY_LOSS=0.03 RISK_MAX_POSITION=0.3 python -m strategy_runners.double_ma.live

# 指定品种和周期
python -m strategy_runners.double_ma.live --inst-id ETH-USDT --interval 4H
```

### Walk-Forward 验证

```bash
python -m strategy_runners.rsi.backtest --walk-forward
N_SPLITS=10 METRIC=calmar_ratio python -m strategy_runners.rsi.backtest --walk-forward
```

---

## 脚本工具

使用 `scripts/` 下的 shell 脚本可以更便捷地运行回测/实盘：

```bash
./scripts/backtest.sh double_ma          # 双均线回测
./scripts/backtest.sh rsi                 # RSI 回测
./scripts/live.sh double_ma              # 双均线实盘
./scripts/walk_forward.sh rsi             # Walk-Forward 分析
./scripts/grid_search.sh rsi             # 参数网格搜索
./scripts/data.sh                        # 拉取 K 线数据
./scripts/report.sh ./output/rsi.json     # 生成 HTML 报告
```

详细参数见 [scripts/README.md](scripts/README.md)。

---

## 项目结构

```
quant_trading/
├── config/                  # 配置文件（gitignore 保护，敏感信息不上传）
│   ├── settings.yaml        # 系统主配置
│   ├── okx_config.yaml      # OKX API 配置
│   └── example.yaml         # 配置模板
│
├── core/                    # 核心层（与交易所无关）
│   ├── engine.py           # MainEngine 主引擎
│   ├── event_bus.py        # 事件总线（发布/订阅）
│   ├── models.py           # 数据模型（OrderData/PositionData/BarData...）
│   ├── enums.py            # 枚举（Exchange/OrderSide/OrderType...）
│   └── exceptions.py       # 异常体系
│
├── gateway/                 # 网关层（各交易所适配器）
│   ├── base_gateway.py     # 抽象基类（所有市场需实现此接口）
│   └── okx/                # OKX 实现
│       ├── okx_gateway.py   # 主入口
│       ├── okx_trader.py   # 交易接口
│       ├── okx_market_data.py  # 行情接口
│       ├── okx_account.py  # 账户接口
│       └── okx_websocket.py # WebSocket 推送
│
├── risk/                    # 风控层
│   ├── risk_engine.py      # 风控引擎（总调度）
│   ├── order_validator.py  # 订单参数校验
│   ├── position_limit.py   # 仓位限制
│   ├── loss_limit.py       # 亏损限额
│   └── rate_limiter.py     # 频率限制
│
├── strategy_core/           # 策略核心
│   ├── base_strategy.py    # 策略基类（on_bar/on_trade/on_order...）
│   ├── strategy_engine.py  # 策略引擎（生命周期管理）
│   ├── array_manager.py    # K 线数据管理 + 指标计算
│   ├── bar_generator.py    # K 线生成器
│   └── impls/              # 策略实现
│       ├── double_ma_strategy.py  # 双均线
│       └── rsi_strategy.py        # RSI
│
├── strategy_runners/        # 策略入口（回测/实盘脚本）
│   ├── double_ma/
│   │   ├── backtest.py     # 回测入口
│   │   ├── live.py         # 实盘入口
│   │   └── params.yaml     # 默认参数
│   └── rsi/
│       ├── backtest.py
│       ├── live.py
│       └── params.yaml
│
├── backtest/               # 回测引擎
│   ├── engine.py           # BacktestEngine 主引擎
│   ├── broker.py           # SimulatedBroker 模拟撮合
│   ├── performance.py       # PerformanceAnalyzer 绩效分析
│   ├── report.py           # HTML 报告生成器
│   └── walk_forward.py     # Walk-Forward 分析
│
├── data/                   # 数据层
│   ├── database.py         # SQLite 数据库管理
│   ├── data_recorder.py   # 行情录制器
│   └── data_feed.py        # 数据加载（优先本地缓存 → API 补拉）
│
├── utils/                  # 工具层
│   ├── config_loader.py    # YAML 配置加载
│   ├── telegram_notifier.py # Telegram Bot 通知
│   ├── retry.py            # 重试装饰器
│   └── helpers.py          # 辅助函数
│
├── examples/               # 示例脚本（01~08）
├── scripts/                # Shell 工具脚本
├── tests/                  # 测试
└── output/                 # 回测报告输出目录
```

---

## 风控规则

| 规则 | 默认值 | 说明 |
|------|--------|------|
| 每日最大亏损 | 5% | 超过停止所有交易 |
| 单笔最大亏损 | 2% | 超限拒绝下单 |
| 单品种最大仓位 | 30% | 持仓价值/总权益 |
| 总仓位最大 | 90% | 所有品种合计 |
| 最大连续亏损 | 5 次 | 超过停止所有交易 |
| 价格偏离限制 | 10% | 委托价/最新价偏离 |
| 最大回撤保护 | 15% | 超过触发 Emergency Stop |
| Emergency Stop 超时 | 10 秒 | 撤单+平仓强制退出 |

> ⚠️ **注意**：回测报告未计入永续合约资金费率（每 8 小时结算），实盘 PnL 会因此偏差。

---

## Telegram 通知

系统支持通过 Telegram Bot 实时推送交易状态。

### 配置

1. **获取 Bot Token**：在 Telegram 搜索 `@BotFather`，发送 `/newbot`
2. **获取 Chat ID**：搜索 `@userinfobot`，发送 `/start`
3. **编辑 `config/settings.yaml`**：
   ```yaml
   telegram:
     enabled: true
     bot_token: "your_bot_token"
     chat_id: "your_chat_id"
     notify_trade: true
     notify_position: true
     notify_risk: true
     equity_interval: 300
   ```

### 推送类型

| 类型 | 触发时机 |
|------|---------|
| 📈 开仓通知 | 订单成交（新开仓位） |
| 📉 平仓通知 | 订单成交（平仓，含盈亏） |
| 📊 持仓变化 | 收到 POSITION_UPDATED 事件 |
| 🚨 风控告警 | 亏损超过阈值/连续亏损超限 |
| 🛑 Emergency Stop | 风控触发紧急停止 |
| ▶️ 策略状态 | 策略启动/停止 |

---

## 新增交易所

新增一个市场只需：

1. 在 `gateway/` 下新建目录（如 `gateway/ib/`）
2. 创建 `IBGateway` 类，继承 `BaseGateway`
3. 实现所有 `@abstractmethod` 方法（`connect/send_order/get_positions...`）
4. 在 `config/settings.yaml` 中添加配置节
5. 在 `core/enums.py` 的 `Exchange` 枚举中添加新交易所

**核心代码零改动。**

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OKX_API_KEY` | OKX API Key | - |
| `OKX_SECRET_KEY` | OKX Secret Key | - |
| `OKX_PASSPHRASE` | OKX Passphrase | - |
| `OKX_FLAG` | 0=实盘, 1=模拟盘 | 1 |
| `QUANT_LOG_LEVEL` | 日志级别 | INFO |
| `QUANT_LOG_DIR` | 日志目录 | logs/ |

---

## 开发指南

### 添加新策略

1. 在 `strategy_core/impls/` 下创建新策略类，继承 `BaseStrategy`
2. 实现 `on_bar()` 核心逻辑
3. 在 `strategy_runners/` 下创建 `backtest.py` 和 `live.py` 入口
4. 注册到 `__init__.py`

### 添加风控规则

1. 在 `risk/` 下创建新规则类
2. 在 `RiskEngine.check_order()` 中注册调用

### 运行回测并查看报告

```bash
python -m strategy_runners.rsi.backtest --generate-report
# 报告生成在 output/ 目录
```
