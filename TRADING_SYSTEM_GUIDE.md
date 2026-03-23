# 量化交易系统完全指南

## 目录

1. [系统架构概览](#系统架构概览)
2. [核心组件设计](#核心组件设计)
3. [关键模块详解](#关键模块详解)
4. [回测操作指南](#回测操作指南)
5. [实盘操作指南](#实盘操作指南)
6. [风控与策略管理详解](#风控与策略管理详解)
7. [常见问题](#常见问题)
8. [重要修复记录](#重要修复记录)
9. [性能优化建议](#性能优化建议)
10. [系统检查清单](#系统检查清单)
11. [文件索引](#文件索引)

---

## 系统架构概览

### 整体设计

本系统采用 **事件驱动** + **模块化分层** 的架构，支持 **回测** 和 **实盘** 无缝切换。

```
┌─────────────────────────────────────────────────────────────┐
│              用户层：strategy_runners/                       │
│     (double_ma/backtest.py, double_ma/live.py 等)          │
└──────────────┬──────────────────────────────────────────────┘
               │ 调用
┌──────────────▼──────────────────────────────────────────────┐
│          策略核心层：strategy_core/                          │
│    BaseStrategy、ArrayManager、BarGenerator、DataUtils      │
└──────────────┬──────────────────────────────────────────────┘
               │
       ┌───────┴───────┐
       │               │
  [回测模式]     [实盘模式]
       │               │
   ┌───▼──────┐   ┌───▼──────────────────────────────────────┐
   │Backtest  │   │           MainEngine (core/engine.py)     │
   │Engine +  │   │  ┌─────────────────────────────────────┐ │
   │Simulated │   │  │  EventBus (事件总线)                  │ │
   │Broker    │   │  └────────────┬────────────────────────┘ │
   └──────────┘   │               │                          │
                  │   ┌───────────┼───────────┬────────────┐ │
                  │   ▼           ▼           ▼            │ │
                  │ OKXGateway RiskEngine StrategyEngine    │ │
                  │ (gateway/)   (risk/)  (strategy_core/) │ │
                  │   │           │           │            │ │
                  │   ▼           ▼           ▼            │ │
                  │ OKX REST   风控检查   策略信号路由       │ │
                  │ API/WebSocket           BaseStrategy     │ │
                  └─────────────────────────────────────────┘ │
```

### 项目目录结构

```
quant_trading/
├── core/                    # 核心基础设施
│   ├── engine.py           # MainEngine 主引擎
│   ├── event_bus.py        # 事件总线
│   ├── enums.py            # 枚举类型
│   ├── models.py           # 数据模型
│   ├── exceptions.py       # 异常定义
│   └── logger.py           # 日志配置
├── gateway/                # 交易所网关
│   ├── base_gateway.py     # Gateway 基类
│   └── okx/                # OKX 交易所实现
│       ├── okx_gateway.py  # OKX Gateway 主类
│       ├── okx_trader.py   # 交易接口
│       ├── okx_market_data.py  # 行情接口
│       ├── okx_account.py   # 账户接口
│       ├── okx_websocket.py # WebSocket 推送
│       └── okx_algo_trader.py # 算法订单
├── strategy_core/          # 策略核心
│   ├── base_strategy.py    # 策略基类
│   ├── array_manager.py    # 技术指标缓冲区
│   ├── bar_generator.py    # K线聚合器
│   ├── strategy_engine.py  # 策略引擎
│   └── impls/              # 策略实现
│       ├── double_ma_strategy.py
│       └── rsi_strategy.py
├── backtest/               # 回测模块
│   ├── engine.py           # BacktestEngine
│   ├── broker.py           # SimulatedBroker
│   ├── performance.py      # 绩效分析
│   └── report.py           # HTML报告生成
├── risk/                   # 风控模块
│   ├── risk_engine.py      # 风控引擎
│   ├── order_validator.py  # 订单校验
│   ├── position_limit.py   # 仓位限制
│   ├── loss_limit.py       # 亏损限制
│   └── rate_limiter.py     # 频率限制
├── strategy_runners/       # 策略运行器
│   ├── cli.py              # 通用CLI工具
│   ├── double_ma/          # 双均线策略
│   │   ├── backtest.py     # 回测入口
│   │   ├── live.py        # 实盘入口
│   │   └── params.yaml     # 默认参数
│   └── rsi/               # RSI策略
├── config/                 # 配置文件
│   └── okx_config.yaml    # OKX API配置
├── data/                   # 数据模块
│   ├── data_feed.py        # 数据供给
│   └── database.py         # 本地数据库
└── tests/                  # 单元测试
```

### 数据流

#### 回测模式
```
params.yaml (配置参数)
     ↓
backtest.py (CLI 入口)
     ↓
BacktestEngine (核心驱动)
     ├─→ K 线 (历史数据)
     ├─→ SimulatedBroker (撮合)
     ├─→ _BacktestStrategyEngine (模拟策略引擎)
     ├─→ Strategy (信号生成)
     └─→ PerformanceAnalyzer (绩效计算)
     ↓
Report (HTML报告)
```

#### 实盘模式
```
params.yaml (配置参数)
     ↓
live.py (CLI 入口)
     ↓
MainEngine (core/engine.py)
     ├─→ EventBus (事件总线)
     ├─→ OKXGateway (连接交易所)
     │     ├─→ OKXWebSocket (K线/Tick推送)
     │     ├─→ OKXTrader (下单成交)
     │     └─→ OKXAccount (账户余额)
     ├─→ RiskEngine (风控检查)
     ├─→ StrategyEngine (信号路由)
     ├─→ Strategy (交易逻辑)
     └─→ OKX REST API (下单成交)
```

---

## 核心组件设计

### 1. BacktestEngine（回测引擎）

**文件**：`backtest/engine.py`

**职责**：K 线驱动的回测核心，模拟交易所行为，计算绩效指标。

#### 关键特性

| 特性 | 实现 | 说明 |
|------|------|------|
| **T+1 延迟撮合** | 订单在 *下一根* K 线撮合 | 避免前视偏差（look-ahead bias） |
| **预热机制** | `warmup_bars` 期间 `trading=False` | 只更新指标，不产生订单 |
| **时钟同步** | 每根 bar 设置 `broker._current_timestamp` | broker 获知当前时间 |
| **强制平仓** | 回测末尾自动平仓所有持仓 | 避免虚拟浮盈 |
| **防前视** | `_get_klines()` 严格返回 `bar_index` 之前的数据 | on_init、on_bar 中都受保护 |
| **模拟策略引擎** | `_BacktestStrategyEngine` | 实现与实盘 `StrategyEngine` 相同的接口 |
| **线程安全** | 策略异常被捕获，永不崩溃回测 | on_bar/on_init 等回调中的异常被记录为 warning |

#### 初始化参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `strategy_class` | - | 策略类（BaseStrategy 子类） |
| `strategy_config` | - | 策略参数字典 |
| `inst_id` | - | 交易品种 |
| `bars` | - | 历史 K 线列表（按时间升序） |
| `initial_capital` | `Decimal("100000")` | 初始资金（100,000 USDT） |
| `taker_fee` | `Decimal("0.0005")` | 市价单手续费率（0.05%） |
| `maker_fee` | `Decimal("0.0002")` | 限价单手续费率（0.02%） |
| `slippage_pct` | `Decimal("0")` | 滑点（默认无滑点） |
| `exchange` | `Exchange.OKX` | 交易所 |
| `risk_free_rate` | `0.02` | 年化无风险利率（2%，用于夏普计算） |
| `warmup_bars` | `0` | 预热 K 线数（0=无预热） |
| `generate_report` | `True` | 是否生成 HTML 报告 |
| `report_output_dir` | `"./output/"` | 报告输出目录 |

#### 完整执行流程

```python
# 1. __init__ 阶段
broker = SimulatedBroker(initial_capital, taker_fee, maker_fee, slippage_pct)
fake_engine = _BacktestStrategyEngine(broker, exchange)
strategy = MyStrategy(name="backtest", strategy_engine=fake_engine, ...)
strategy.trading = False   # 预热前禁止交易
strategy.active = True
fake_engine._inject_history(inst_id, bars)  # 注入历史供 on_init 使用

# 2. run() 阶段
# 2a. on_init 期间：注入 warmup_bars-1 根 K 线（而非全量历史）[P1-4]
fake_engine._set_current_index(warmup_bars - 1)
strategy.on_init()

# 2b. 主循环：每根 bar
for i, bar in enumerate(bars):
    in_warmup = (i < warmup_bars)
    
    # [P2-1] 设置模拟时钟（订单时间戳用 bar 时间，而非 datetime.now()）
    broker.set_current_timestamp(bar.timestamp)
    
    # [P1-4] 更新当前 bar 索引（防 get_klines 泄露未来）
    fake_engine._set_current_index(i)
    
    if in_warmup:
        # 预热阶段：只更新权益快照，不撮合订单
        broker._update_mark_prices(bar)
        broker._snapshot_equity(bar.timestamp)
    else:
        # [P1-5] warmup 刚结束：开启交易并清空残余挂单
        if i == warmup_bars:
            strategy.trading = True
            broker.clear_pending_orders()
        
        # 第1步：撮合上一轮 on_bar 产生的挂单
        broker.match_orders(bar)
    
    # 第2步：触发策略回调（可能产生新订单）
    strategy.on_bar(bar)

# 3. 回测结束 [P2-3]
strategy.trading = False   # 禁止策略继续下单
_close_all_positions()     # 以最后bar收盘价强制平仓

# 4. 计算绩效 + 生成报告
performance = PerformanceAnalyzer(...).compute()
generate_report(...)         # 可选 HTML 报告
```

#### 预热机制详解

| 阶段 | `strategy.trading` | broker 行为 |
|------|-------------------|-------------|
| `i < warmup_bars` | `False` | `_update_mark_prices` + `_snapshot_equity`，不撮合订单 |
| `i == warmup_bars` | → `True` | `clear_pending_orders()` 清空 warmup 期间产生的挂单 |
| `i > warmup_bars` | `True` | 正常撮合 + 信号处理 |

**注意**：策略代码中的 `buy/sell/short/cover` 调用在 `trading=False` 时仍会执行到 broker，但 broker 会拒绝挂单（因为 `trading` 标志检查）。

#### 工厂方法（DataFeed 模式）

```python
engine = BacktestEngine.from_data_feed(
    strategy_class=DoubleMaStrategy,
    strategy_config={"fast_period": 10, ...},
    inst_id="BTC-USDT",
    interval="1H",
    start=datetime(2024, 1, 1),
    end=datetime(2024, 12, 31),
    data_feed=data_feed,    # DataFeed 实例（支持 SQLite 缓存 + API 补拉）
    initial_capital=Decimal("100000"),
    ...
)
```

### 1.1 _BacktestStrategyEngine（回测用模拟策略引擎）

**文件**：`backtest/engine.py`（内部类）

**职责**：模拟实盘 `StrategyEngine` 的全部接口，使策略代码在回测和实盘中完全复用。

**策略调用 `self.buy()` 后的完整路由**：
```
Strategy.buy()
    → _BacktestStrategyEngine._buy()
    → SimulatedBroker.send_order()
    → 订单进入 pending_orders 列表
    → 下一根 bar 的 match_orders() 执行撮合
```

**提供的接口**：
| 方法 | 说明 |
|------|------|
| `_buy()` | 买入（现货做多 / 合约开多） |
| `_sell()` | 卖出（现货平多 / 合约平多） |
| `_short()` | 做空（合约开空） |
| `_cover()` | 平空（合约平空） |
| `_close_long()` | 平多仓（OKX 双仓模式：side=SELL, posSide=LONG） |
| `_close_short()` | 平空仓（OKX 双仓模式：side=BUY, posSide=SHORT） |
| `_cancel()` | 撤单 |
| `_get_position()` | 查询持仓 |
| `_get_balance()` | 查询余额 |
| `_get_klines()` | 查询历史K线（**防前视版本**，只返回当前bar之前的数据） |

### 2. SimulatedBroker（模拟经纪商）

**文件**：`backtest/broker.py`

**职责**：模拟订单撮合、持仓管理、P&L 计算。支持做空（期货/合约）。

#### 订单撮合规则

| 订单类型 | 成交条件 | 成交价格 |
|----------|----------|----------|
| **市价买入** | 一定成交 | `bar.open * (1 + slippage_pct)` |
| **市价卖出** | 一定成交 | `bar.open * (1 - slippage_pct)` |
| **限价买入** | `bar.low <= price` | `min(price, bar.open)` |
| **限价卖出** | `bar.high >= price` | `max(price, bar.open)` |

撮合遍历 `pending_orders`，逐个尝试成交：
- 市价单：直接成交（可能部分成交）
- 限价单：对比 bar OHLC 判断是否触及价格

#### 持仓模型

| 方向 | 开仓 | 持仓价值 | 平仓扣费 |
|------|------|----------|----------|
| **多头** `qty > 0` | `cash -= price * qty + fee` | `open_qty * mark_price - open_fee` | `taker_fee` |
| **空头** `qty < 0` | `frozen_margin = price * |qty|` | `frozen_margin + (avg - mark) * |qty|` | `taker_fee` |

#### P&L 计算

```python
# 开仓 买入 0.002 BTC @ 50000（taker fee 0.05%）
open_fee = 50000 * 0.002 * 0.0005 = 0.05 USDT

# 平仓 卖出 0.002 BTC @ 51000
pnl_gross = (51000 - 50000) * 0.002 = 2.0 USDT
close_fee = 51000 * 0.002 * 0.0005 = 0.051 USDT
pnl_net = pnl_gross - open_fee - close_fee = 1.949 USDT
```

#### 关键修复记录

| ID | 问题 | 修复 |
|----|------|------|
| `[P0-1]` | 做空时资金/保证金检查缺失 | `send_order` 新增现金和保证金充足性检查 |
| `[P1-1]` | 买入平空时无法反转为多仓 | 处理 `close_qty < order.qty` 时开立剩余多头 |
| `[P1-2]` | 卖出平多时无法反转为空仓 | 处理 `close_qty < order.qty` 时开立剩余空头 |
| `[P1-3]` | `force_close` 手续费重复扣除 | 强制平仓只扣一次 fee |
| `[P2-1]` | 订单使用 `datetime.now()` 而非 bar 时间 | broker 使用 `set_current_timestamp` 设置的时间 |
| `[P2-2]` | 权益快照对同一时间戳重复追加 | 同一时间戳覆盖而非追加 |
| `[P2-3]` | `force_close` 期间策略可能继续下单 | 回测结束前先设置 `strategy.trading=False` |
| `[P2-4]` | `get_balance().total_equity` 用缓存值 | 改为实时计算 |
| `[P2-5]` | `frozen_margin` 记录了费用 | 只记录保证金，不含费用 |

#### `force_close_position` 行为

回测结束时调用：以最后一根 K 线的 `close` 价格平所有持仓，使用 `taker_fee`，生成 `TradeData` 和 `OrderData`（状态=FILLED），追加到 `_filled_orders`。

#### `clear_pending_orders` 行为

warmup 结束后调用：清空 `pending_orders` 列表，丢弃预热期间产生的所有挂单。

### 3. PerformanceAnalyzer（绩效分析器）

**文件**：`backtest/performance.py`

**职责**：从 broker 的权益曲线和成交记录计算全套绩效指标。

#### 完整指标列表

**收益类**：
| 指标 | 说明 |
|------|------|
| `initial_capital` | 初始资金 |
| `final_equity` | 最终权益 |
| `total_pnl` | 总盈亏（USDT） |
| `total_return_pct` | 总收益率 |
| `annual_return_pct` | 年化收益率（复利） |

**回撤类**：
| 指标 | 说明 |
|------|------|
| `max_drawdown_pct` | 最大回撤比例（如 0.15 = 15%） |
| `max_drawdown_amount` | 最大回撤（USDT 绝对值） |
| `max_drawdown_duration_days` | 最大回撤持续天数 |

**风险调整类**：
| 指标 | 说明 |
|------|------|
| `annual_volatility_pct` | 年化波动率 |
| `sharpe_ratio` | 夏普比率 = `(年化收益 - 无风险利率) / 年化波动率` |
| `sortino_ratio` | 索提诺比率 = `(年化收益 - 无风险利率) / 下行偏差` |
| `calmar_ratio` | 卡玛比率 = `年化收益率 / 最大回撤` |

**交易统计类**：
| 指标 | 说明 |
|------|------|
| `total_trades` | 总交易次数（含开仓和平仓） |
| `round_trips` | 完整轮次数（一次开仓+平仓=1轮） |
| `win_trades` | 盈利交易次数 |
| `loss_trades` | 亏损交易次数 |
| `win_rate_pct` | 胜率 |
| `profit_factor` | 盈利因子 = 总盈利 / 总亏损 |
| `avg_trade_pnl` | 平均每笔交易盈亏 |
| `max_win` | 单笔最大盈利 |
| `max_loss` | 单笔最大亏损 |
| `avg_win` | 平均盈利 |
| `avg_loss` | 平均亏损 |
| `max_consecutive_wins` | 最大连续盈利次数 |
| `max_consecutive_losses` | 最大连续亏损次数 |
| `expectancy` | 期望值 = `胜率 * 平均盈利 - (1-胜率) * 平均亏损` |
| `total_fees` | 总手续费 |
| `long_trades` | 多头交易次数 |
| `short_trades` | 空头交易次数 |
| `avg_holding_hours` | 平均持仓时间（小时，FIFO 配对） |

**年化公式**：周期 ≥ 7 天用复利 `(1+total_return)^(1/years) - 1`，短于 7 天用线性。

**Sortino 标准算法**：`downside_dev = sqrt(mean(min(r - 0, 0)²))`，年度化因子 `sqrt(periods_per_year)`。

**持仓时间计算**：按 `inst_id` 的 FIFO 队列，入场订单（`pnl==0`）入队，出场订单（`pnl!=0`）弹出最早入场订单计算 `(exit_time - entry_time).total_seconds() / 3600`。

### 4. ArrayManager（技术指标缓冲区）

**文件**：`strategy_core/array_manager.py`

**职责**：高性能技术指标计算，预分配环形缓冲区。

#### 设计

```python
am = ArrayManager(size=100)  # 容量 100 根 K 线

# 每根新 K 线
am.update_bar(bar)

# 指标准备完毕？
if am.inited:  # count >= size
    fast_ma = am.sma(10)
    slow_ma = am.sma(30)
    rsi = am.rsi(14)
```

#### 支持的指标

| 指标 | 用法 | 返回值 |
|------|------|--------|
| **SMA** | `am.sma(20)` | `float` |
| **EMA** | `am.ema(12)` | `float` |
| **RSI** | `am.rsi(14)` | `float` (0-100) |
| **MACD** | `am.macd(12, 26, 9)` | `(macd, signal, hist)` |
| **Bollinger** | `am.boll(20, 2)` | `(upper, mid, lower)` |
| **ATR** | `am.atr(14)` | `float` |
| **KDJ** | `am.kdj(9, 3, 3)` | `(k, d)` |

#### 数组模式

```python
# 获取完整数组（最后 N 个值）
closes = am.close_array(array=True)  # returns np.ndarray
fast_ma_array = am.sma(10, array=True)

# 遍历历史值
for close in closes:
    print(close)
```

### 5. BaseStrategy（策略基类）

**文件**：`strategy_core/base_strategy.py`

**职责**：策略框架，定义事件回调和交易接口。

#### 必须实现

```python
class MyStrategy(BaseStrategy):
    def on_init(self):
        """初始化：加载历史数据，预热指标"""
        bars = self.get_klines(self.inst_id, "1H", limit=100)
        for bar in bars:
            self._am.update_bar(bar)

    def on_bar(self, bar: BarData):
        """核心信号逻辑，每根 K 线触发"""
        if bar.inst_id != self.inst_id:
            return

        # 更新指标
        self._am.update_bar(bar)

        # 生成信号
        if self._am.inited:
            # ... 交易逻辑 ...
            if 金叉信号:
                self.buy(price=bar.close, quantity=qty)
            elif 死叉信号:
                self.sell(price=bar.close, quantity=qty)
```

#### 交易方法

```python
# 现货：买入开仓 / 卖出平仓
self.buy(price, quantity, order_type)    # OrderType.MARKET 或 LIMIT
self.sell(price, quantity, order_type)

# 期货：做多 / 平多
self.short(price, quantity, order_type)  # 做空（SELL）
self.cover(price, quantity, order_type)  # 平空（BUY）

# 撤单
self.cancel(order_id)
```

#### 数据查询

```python
# 查询持仓
pos = self.get_position(self.inst_id)
if pos:
    print(pos.quantity, pos.entry_price)

# 查询账户
bal = self.get_balance()
print(bal.available, bal.equity)

# 查询历史 K 线（防前视）
bars = self.get_klines(self.inst_id, "1H", limit=50)
# ✓ 回测：只返回当前 bar 之前的数据
# ✓ 实盘：返回最新 50 根 K 线
```

#### 辅助方法

```python
# 按资金比例计算下单量
qty = self.calc_quantity(
    price=bar.close,           # 参考价格
    pct=0.95,                  # 使用 95% 可用资金
    lot_size=0.001,            # 精度：0.001 (BTC)
)

# 日志输出
self.write_log(f"信号: {signal_name}")
```

#### 事件回调（可选）

```python
def on_start(self):
    """策略启动"""
    pass

def on_stop(self):
    """策略停止"""
    pass

def on_order(self, order: OrderData):
    """订单状态变化"""
    pass

def on_trade(self, trade: TradeData):
    """成交回报"""
    pass

def on_position(self, position: PositionData):
    """持仓更新"""
    pass

def on_tick(self, tick: TickData):
    """Tick 数据（实盘）"""
    pass
```

### 6. BarGenerator（K 线聚合）

**文件**：`strategy_core/bar_generator.py`

**职责**：Tick → 1 分钟 → N 分钟/小时/天 K 线聚合。

#### 聚合流程

```
Tick 行情
  ↓
1 分钟 K 线（自动生成）
  ↓
N 分钟 K 线（需手动定义 window）
  ↓
小时 / 天 K 线
```

#### 使用示例

```python
bg = BarGenerator(on_bar_1m, on_bar_5m)

# 处理 Tick
bg.update_tick(tick)

# 5 分钟完成时会自动调用 on_bar_5m(bar_5m)
```

### 7. MainEngine（主引擎）

**文件**：`core/engine.py`

**职责**：系统主入口，负责初始化和管理所有组件的生命周期。

#### 核心职责

| 职责 | 说明 |
|------|------|
| **Gateway管理** | 注册、连接、断开交易所网关 |
| **引擎注入** | 注入 RiskEngine、StrategyEngine |
| **信号处理** | 注册 SIGINT/SIGTERM，支持优雅退出 |
| **订单路由** | send_order → 风控检查 → Gateway |
| **状态同步** | 启动时从Gateway同步持仓和余额 |

#### 启动流程

```
main_engine.start()
    │
    ├─→ _preflight_check()        # 配置完整性自检
    ├─→ _register_signal_handlers() # 注册信号处理
    ├─→ connect_all()              # 连接所有Gateway
    ├─→ _post_connect_check()     # 连接后验证
    ├─→ risk_engine.start()       # 启动风控引擎
    ├─→ strategy_engine.start()   # 启动策略引擎
    ├─→ _sync_state_from_gateways() # 同步持仓/余额状态
    └─→ _print_startup_config()   # 打印启动配置确认
```

#### 事件总线集成

MainEngine 内置 EventBus，所有组件通过事件总线通信：

| 事件类型 | 发布者 | 订阅者 |
|----------|--------|--------|
| `BAR` | Gateway | StrategyEngine |
| `TICK` | Gateway | StrategyEngine |
| `ORDER_SUBMITTED` | MainEngine | - |
| `ORDER_UPDATED` | Gateway | StrategyEngine |
| `TRADE` | Gateway | StrategyEngine, RiskEngine |
| `POSITION_UPDATED` | Gateway | StrategyEngine, RiskEngine |
| `BALANCE_UPDATED` | Gateway | StrategyEngine, RiskEngine |
| `RISK_BREACH` | RiskEngine | StrategyEngine |

### 8. OKXGateway（OKX交易所网关）

**文件**：`gateway/okx/okx_gateway.py`

**职责**：封装OKX交易所的所有接口，提供统一的BaseGateway接口。

#### 子模块

| 模块 | 职责 |
|------|------|
| `OKXMarketData` | 行情接口（K线、Tick、订单簿） |
| `OKXAccount` | 账户接口（余额、持仓） |
| `OKXTrader` | 现货/合约交易接口 |
| `OKXAlgoTrader` | 算法订单（计划单、冰山单等） |
| `OKXWebSocket` | WebSocket实时行情推送 |

#### 配置参数

```yaml
# config/okx_config.yaml
okx:
  api_key: "YOUR_API_KEY"
  secret_key: "YOUR_SECRET_KEY"
  passphrase: "YOUR_PASSPHRASE"
  flag: "0"              # 0=实盘, 1=模拟盘
  spot_whitelist:        # 允许交易的现货
    - "BTC-USDT"
    - "ETH-USDT"
  swap_whitelist:        # 允许交易的合约
    - "BTC-USDT-SWAP"
  default_leverage: 5    # 合约默认杠杆
```

### 9. RiskEngine（风控引擎）

**文件**：`risk/risk_engine.py`

**职责**：对每笔订单执行完整的风控检查链，触发风控时暂停所有交易。

#### 风控检查链（按顺序执行，成本从低到高）

| 顺序 | 检查器 | 检查内容 |
|------|--------|----------|
| ① | `OKXRateLimiter` | API请求频率限制 |
| ② | `OrderValidator` | 精度、最小量、价格偏离 |
| ③ | `PositionLimitChecker` | 品种仓位上限、总仓位比例 |
| ④ | `LossLimitChecker` | 单笔亏损、每日亏损、连续亏损 |

#### 风控配置

```yaml
# params.yaml 或 config/settings.yaml
risk:
  enabled: true
  max_daily_loss_pct: 0.05      # 单日最大亏损 5%
  max_single_loss_pct: 0.02     # 单笔最大亏损 2%
  max_consecutive_losses: 5      # 最大连续亏损次数
  max_position_pct: 0.5         # 单品种最大仓位 50%
  max_total_position_pct: 0.9   # 总仓位上限 90%
  max_orders_per_second: 10     # 下单频率限制
```

### 10. StrategyEngine（策略引擎）

**文件**：`strategy_core/strategy_engine.py`

**职责**：加载/启停策略，管理策略生命周期，将行情/交易事件分发给对应策略。

#### 策略生命周期

```
add_strategy() → start_strategy() → [运行中] → stop_strategy()
    │                  │
    │                  └─→ on_init() → on_start()
    │                               on_bar() / on_tick()
    │                               on_stop()
    └─→ 接收事件: BAR, TICK, ORDER, TRADE, POSITION, BALANCE
```

#### 多品种订阅

```python
# 订阅额外品种
engine.subscribe_extra("my_strategy", "ETH-USDT")
```

---

## 关键模块详解

### 双均线策略（DoubleMaStrategy）

**文件**：`strategy_core/impls/double_ma_strategy.py`

**适用场景**：趋势跟踪，币圈 1H/4H 周期效果好。

#### 逻辑

```
快线 = SMA(10)
慢线 = SMA(30)

金叉信号：fast_ma > slow_ma AND prev_fast <= prev_slow
  → 若无多头仓位，买入开仓（仓位 95% 可用资金）

死叉信号：fast_ma < slow_ma AND prev_fast >= prev_slow
  → 平仓所有多头持仓
```

#### 参数配置

```yaml
# strategy_runners/double_ma/params.yaml
strategy:
  fast_period: 10      # 快线周期（灵敏度高）
  slow_period: 30      # 慢线周期（趋势判断）
  position_pct: 0.95   # 每次开仓用 95% 可用资金
  lot_size: 0.001      # BTC 下单精度（0.001）
  interval: "1H"       # K 线周期

backtest:
  capital: 100000
  grid:                # 网格搜索范围
    fast_period: [5, 10, 20]
    slow_period: [20, 30, 60]
```

### RSI 均值回归策略（RsiStrategy）

**文件**：`strategy_core/impls/rsi_strategy.py`

**适用场景**：振荡区间，高频均值回归。

#### 逻辑

```
RSI = 相对强弱指数(rsi_period)

超卖信号：RSI < oversold → 买入（均值回归）
超买信号：RSI > overbought → 卖出（获利）
止损信号：亏损 > stop_loss_pct → 强制平仓
```

#### 参数配置

```yaml
# strategy_runners/rsi/params.yaml
strategy:
  rsi_period: 14       # RSI 计算周期（标准值）
  oversold: 30         # 超卖线（买入信号）
  overbought: 70       # 超买线（卖出信号）
  stop_loss_pct: 0.03  # 止损 3%（防爆仓）
  position_pct: 0.95
  lot_size: 0.001
  interval: "1H"

data:
  inst_id: "BTC-USDT"
  interval: "1H"

backtest:
  capital: 100000
  taker_fee: 0.0005
  maker_fee: 0.0002
  slippage: 0.0001
  data_limit: 1000
  mock_days: 180
  mock_seed: 42
  grid:
    rsi_period: [14, 21]
    oversold: [25, 30]
    overbought: [70, 75]
    stop_loss_pct: [0.03, 0.05]

live:
  risk:
    max_daily_loss_pct: 0.05
    max_position_pct: 0.5
    max_total_position_pct: 0.9
    max_consecutive_losses: 5
```

---

## 回测操作指南

### 快速开始

#### 1. 使用默认参数回测

```bash
cd /Users/bytedance/vnpy/quant_trading

# 双均线策略
python -m strategy_runners.double_ma.backtest

# RSI 策略
python -m strategy_runners.rsi.backtest
```

**输出**：
```
============================================================
  双均线策略回测
============================================================
  品种=BTC-USDT  周期=1H  数据来源=okx  初始资金=100,000 USDT

[1/3] 加载历史数据...
  ✓ 1000 根 K 线  [2024-10-23 → 2025-10-22]

[2/3] 使用指定参数...
  fast=10  slow=30  仓位=95%

[3/3] 完整回测报告...
  总收益率：18.32%
  夏普比率：1.45
  最大回撤：-8.21%
  ...

  ✓ HTML 报告: ./output/DoubleMaStrategy___BTC-USDT_20260323_123456.html
```

#### 2. 覆盖参数

```bash
# 修改均线周期
python -m strategy_runners.double_ma.backtest --fast-period 5 --slow-period 20

# 修改初始资金和手续费
python -m strategy_runners.double_ma.backtest --capital 50000 --taker-fee 0.001

# 修改 K 线周期和品种
python -m strategy_runners.double_ma.backtest --inst-id ETH-USDT --interval 4H
```

#### 3. 使用模拟数据（无需网络）

```bash
# 生成 365 天模拟数据
python -m strategy_runners.double_ma.backtest --data-source mock --mock-days 365

# 指定种子（可重复生成相同数据）
python -m strategy_runners.double_ma.backtest --data-source mock --mock-seed 42
```

### 网格搜索（参数优化）

```bash
# 遍历所有参数组合，找最优 Sharpe
python -m strategy_runners.double_ma.backtest --grid-search --output-dir ./my_results/
```

**网格搜索配置**（在 `params.yaml` 定义）：
```yaml
backtest:
  grid:
    fast_period: [5, 10, 20]      # 3 个值
    slow_period: [20, 30, 60]     # 3 个值
    # → 9 组参数组合（无效的自动过滤）
```

**输出示例**：
```
┌─────────┬─────────┬─────────┬──────────┬────────────┬───────┐
│ fast    │ slow    │ total_r │ sharpe   │ max_dd     │ wins  │
├─────────┼─────────┼─────────┼──────────┼────────────┼───────┤
│ 10      │ 30      │ 18.32%  │ 1.45     │ -8.21%     │ 24    │  ← 最优
│ 5       │ 20      │ 12.54%  │ 1.12     │ -10.32%    │ 18    │
│ 20      │ 60      │ 8.76%   │ 0.95     │ -12.10%    │ 12    │
...
```

### 自定义参数查询

```bash
python -m strategy_runners.double_ma.backtest --help
```

#### 双均线回测完整 CLI 参数

**策略参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fast-period` | `params.yaml`（10） | 快均线周期 |
| `--slow-period` | `params.yaml`（30） | 慢均线周期（warmup 预热根数） |
| `--position-pct` | `params.yaml`（0.95） | 开仓使用可用资金比例 |
| `--lot-size` | `params.yaml`（0.001） | 下单精度（BTC=0.001） |

**数据参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--inst-id` | `BTC-USDT` | 交易品种 |
| `--interval` | `1H` | K 线周期 |
| `--data-source` | `real` | `real`（OKX API）或 `mock`（模拟数据） |
| `--start-date` | 空 | YYYY-MM-DD UTC，开始日期（优先于 data-limit） |
| `--end-date` | 空 | YYYY-MM-DD UTC，结束日期（空表示当前） |
| `--data-limit` | `1000` | 拉取 K 线数量 |
| `--mock-days` | `365` | 模拟数据天数（mock 模式） |
| `--mock-seed` | `42` | 模拟数据随机种子（可重复） |

**回测引擎参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--capital` | `100000` | 初始资金（USDT） |
| `--taker-fee` | `0.0005` | Taker 手续费率（0.05%） |
| `--maker-fee` | `0.0002` | Maker 手续费率（0.02%） |
| `--slippage` | `0.0001` | 滑点（0.01%，默认开启） |

**报告与输出**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--grid-search` | `False` | 启用参数网格搜索 |
| `--output-dir` | 空 | JSON/CSV/HTML 输出目录（空则使用 `./output/`） |
| `--no-report` | `False` | 禁止生成 HTML 报告 |
| `--no-open` | `False` | 生成报告但不自动打开浏览器 |
| `--log-level` | `INFO` | 日志级别 DEBUG/INFO/WARNING/ERROR |

**网格搜索**：
- 按 `sharpe_ratio` 降序排列
- 自动过滤 `fast_period >= slow_period` 的无效组合
- 输出 CSV + JSON 格式

#### RSI 回测完整 CLI 参数

**策略参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--rsi-period` | `params.yaml`（14） | RSI 计算周期 |
| `--oversold` | `params.yaml`（30） | 超卖线 |
| `--overbought` | `params.yaml`（70） | 超买线 |
| `--stop-loss-pct` | `params.yaml`（0.03） | 止损比例（3%） |
| `--position-pct` | `params.yaml`（0.95） | 开仓资金比例 |
| `--lot-size` | `params.yaml`（0.001） | 下单精度 |

**数据与回测参数**：与双均线相同，`--mock-days` 默认为 `180`。

**网格搜索参数**：`rsi_period`, `oversold`, `overbought`, `stop_loss_pct`。

**注意**：RSI 策略的 warmup 根数 = `rsi_period + 5`，而非 `slow_period`。

### 查看回测报告

生成的 HTML 报告包含：
- **权益曲线**（折线图）
- **日收益率直方图**（绿红柱）
- **成交记录表**（入场、出场、P&L）
- **绩效指标**（Sharpe、最大回撤等）
- **配置参数**（策略参数、回测设置）

#### 自动打开报告
```bash
python -m strategy_runners.double_ma.backtest
# 自动打开默认浏览器，显示 HTML 报告
```

#### 手动打开报告
```bash
open ./output/DoubleMaStrategy___BTC-USDT_20260323_123456.html
# macOS

# Linux
xdg-open ./output/DoubleMaStrategy___BTC-USDT_20260323_123456.html

# Windows
start ./output/DoubleMaStrategy___BTC-USDT_20260323_123456.html
```

### 保存回测结果

```bash
# 保存 JSON + CSV
python -m strategy_runners.double_ma.backtest --output-dir ./results/
```

**输出文件**：
```
./results/
├── DoubleMaStrategy___BTC-USDT_20260323_123456.html  # HTML 报告
├── double_ma_20260323_123456.json                     # 绩效指标（JSON）
└── double_ma_grid_20260323_123456.csv                 # 网格搜索结果（CSV）
```

**JSON 内容**：
```json
{
  "strategy_name": "double_ma",
  "inst_id": "BTC-USDT",
  "initial_capital": 100000,
  "final_equity": 118320,
  "total_return_pct": 18.32,
  "annual_return_pct": 18.32,
  "sharpe_ratio": 1.45,
  "max_drawdown_pct": -8.21,
  "total_trades": 24,
  "win_rate_pct": 58.33,
  ...
}
```

---

## 实盘操作指南

### 前置准备

#### 1. 配置 OKX API Key

编辑 `config/okx_config.yaml`：
```yaml
okx:
  api_key: "YOUR_API_KEY"          # 从 OKX 获取
  secret_key: "YOUR_SECRET_KEY"
  passphrase: "YOUR_PASSPHRASE"
  flag: "0"                        # 0=实盘, 1=模拟盘
```

#### 2. 配置风控参数

编辑 `strategy_runners/double_ma/params.yaml`：
```yaml
live:
  risk:
    max_daily_loss_pct: 0.05        # 单日最大亏损 5%
    max_position_pct: 0.5          # 单品种最大仓位 50%
    max_total_position_pct: 0.9     # 总仓位上限 90%
    max_consecutive_losses: 5       # 最多连续 5 次亏损
```

### 快速启动

#### 第 1 步：小仓位验证（强烈推荐！）

```bash
# 用 5% 仓位测试连接和策略行为
python -m strategy_runners.double_ma.live \
    --position-pct 0.05 \
    --log-level DEBUG
```

监控输出：
```
2026-03-23 12:34:56 [INFO] 双均线策略实盘启动
2026-03-23 12:34:56 [INFO] 连接 OKX...
2026-03-23 12:34:57 [INFO] 实盘已启动  inst=BTC-USDT  config={...}
2026-03-23 12:35:00 [INFO] 收到 K 线行情 timestamp=2026-03-23 12:35:00
2026-03-23 12:35:00 [INFO] Bar close=42567.5 fast_ma=42123.4 slow_ma=41890.2
2026-03-23 12:35:00 [INFO] 金叉信号！买入 BTC-USDT qty=0.0001
...
```

**观察指标**：
- ✓ 连接成功（无异常）
- ✓ K 线推送正常（每周期一条）
- ✓ 信号生成合理
- ✓ 订单成交及时

#### 第 2 步：调整参数后启动

```bash
# 从回测结果拷贝最优参数
python -m strategy_runners.double_ma.live \
    --fast-period 10 \
    --slow-period 30 \
    --position-pct 0.5 \
    --log-level INFO
```

#### 第 3 步：生产环境（完整仓位）

```bash
python -m strategy_runners.double_ma.live
# 使用 params.yaml 中的默认参数
```

### 监控和管理

#### 查看日志

```bash
# 实时查看日志
tail -f logs/strategy.log

# 搜索错误
grep ERROR logs/strategy.log

# 统计成交
grep "成交回报" logs/strategy.log | wc -l
```

#### 正常停止

```bash
# 在运行窗口按 Ctrl+C
^C
```

**停止过程**：
```
2026-03-23 12:40:00 [INFO] 收到停止信号 (signal=2)，安全退出...
2026-03-23 12:40:00 [INFO] 撤销所有待成交订单...
2026-03-23 12:40:01 [INFO] 停止策略...
2026-03-23 12:40:01 [INFO] 断开连接...
2026-03-23 12:40:02 [INFO] 实盘已停止
```

#### 风控告警

```
[WARNING] 今日亏损达 4.9%，接近上限 5%
[WARNING] 单品种仓位达 49%，接近上限 50%
[WARNING] 连续亏损 4 次，接近上限 5 次
[ERROR] 触发日亏损限制 5%，停止所有交易
```

### 参数调整

#### 修改仓位大小

```bash
# 减少仓位（市场不确定）
python -m strategy_runners.double_ma.live --position-pct 0.3

# 增加仓位（信心充足）
python -m strategy_runners.double_ma.live --position-pct 0.8
```

#### 修改策略参数

```bash
# 加快交叉速度（更敏感）
python -m strategy_runners.double_ma.live \
    --fast-period 5 \
    --slow-period 15

# 放慢交叉速度（减少虚假信号）
python -m strategy_runners.double_ma.live \
    --fast-period 20 \
    --slow-period 50
```

#### 修改风控参数

```bash
# 严格的风控
python -m strategy_runners.double_ma.live \
    --risk-max-daily-loss 0.02 \
    --risk-max-position 0.3 \
    --risk-max-consecutive-losses 3

# 宽松的风控（高风险）
python -m strategy_runners.double_ma.live \
    --risk-max-daily-loss 0.10 \
    --risk-max-position 0.7 \
    --risk-max-consecutive-losses 10
```

### 从回测到实盘的完整流程

```
1. 回测优化
   python -m strategy_runners.double_ma.backtest --grid-search

   结果：fast=10, slow=30, sharpe=1.45 (最优)

2. 小仓位验证（1-2 天）
   python -m strategy_runners.double_ma.live \
       --fast-period 10 \
       --slow-period 30 \
       --position-pct 0.05

   确认：连接正常 ✓ 信号合理 ✓ 成交及时 ✓

3. 正常运营
   python -m strategy_runners.double_ma.live \
       --fast-period 10 \
       --slow-period 30 \
       --position-pct 0.5
```

#### 实盘完整 CLI 参数

**交易品种**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--inst-id` | `BTC-USDT` | 交易品种 |
| `--interval` | `1H` | K 线周期（1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D） |

**策略参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fast-period` | `params.yaml`（10） | 快均线周期 |
| `--slow-period` | `params.yaml`（30） | 慢均线周期 |
| `--position-pct` | `params.yaml`（**0.95**） | ⚠️ 实盘默认 95%，先用小仓位测试！ |
| `--lot-size` | `params.yaml`（0.001） | 下单精度 |

**风控参数**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--risk-max-daily-loss` | `0.05`（5%） | 单日最大亏损比例 |
| `--risk-max-position` | `0.5`（50%） | 单品种最大仓位比例 |
| `--risk-max-consecutive-losses` | `5` | 最大连续亏损次数 |

**其他**：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy-name` | 自动生成 | 策略实例名 |
| `--log-level` | `INFO` | 日志级别 |

#### OKX 双仓模式说明

实盘默认使用 OKX 双仓模式（Hedge Mode），需要特别注意：

| 操作 | OKX 双仓方向 | BaseStrategy 方法 |
|------|-------------|------------------|
| 开多仓 | `side=BUY, posSide=LONG` | `self.buy()` 或 `self._close_short()` |
| 平多仓 | `side=SELL, posSide=LONG` | `self.sell()` 或 `self._close_long()` |
| 开空仓 | `side=SELL, posSide=SHORT` | `self.short()` |
| 平空仓 | `side=BUY, posSide=SHORT` | `self.cover()` 或 `self._close_short()` |

**注意**：`sell()` 在双仓模式下默认是 `posSide=NET`（净仓），建议使用 `_close_long()` 明确指定方向。

#### 实盘启动配置确认

MainEngine 启动时会打印关键配置，确认无误再继续：

```
=====================================================
  MainEngine 启动确认  【模拟盘】/【实  盘】
=====================================================
  运行模式   : live
  日志级别   : INFO
  OKX flag   : 1  (0=实盘  1=模拟盘)
  风控启用   : True
  日亏损上限 : 5.0%
  单品种仓位 : 50.0%
  最大回撤   : 0.0%
  Gateways   : ['OKX']
=====================================================
```

#### 实盘 WebSocket K 线订阅

实盘 K 线通过 OKX WebSocket 实时推送，无需轮询：

```python
gateway.subscribe_kline(inst_id, interval)
# 订阅后：BAR 事件通过 EventBus 分发 → StrategyEngine._on_bar → strategy.on_bar(bar)
```

WebSocket 断线后会自动重连（最大等待 60 秒）。

#### 风控触发行为详解

| 触发条件 | 行为 |
|----------|------|
| 每日亏损达限 | 发布 `RISK_BREACH`，所有策略 `trading=False` |
| 单笔亏损达限 | 立即触发止损单 |
| 连续亏损达限 | 发布 `RISK_BREACH` |
| 最大回撤达限 | `emergency_stop()` 全部平仓 + 停止策略 |
| 系统性停止（is_halted） | 撤销所有挂单 |

**日亏损重置**：UTC 0:00 自动重置，按 UTC 自然日计算。

**Halt 状态**：只能平仓（减少方向的订单允许），不能新开仓。需手动调用 `reset_halt()` 恢复。

#### 紧急止损（Kill Switch）

当 `RiskEngine.emergency_stop()` 被触发时：

```
1. 撤销所有 Gateway 的所有未成交订单
2. 以市价平所有持仓
3. 停止 StrategyEngine
4. 设置 loss_limit._is_halted = True
5. 发布 RISK_BREACH 事件
```

**线程安全**：`check_order` 使用 `_check_lock` 保证风控检查链的原子性，防止并发策略联合突破仓位限制。

---

## 风控与策略管理详解

### 1. 风控引擎架构

```
订单请求 (OrderRequest)
       │
       ▼
RiskEngine.check_order()
       │
       ▼
┌──────┴─────────────────────────────────────────────────────────┐
│  with self._check_lock:  ← 并发安全                            │
│  ① OrderRateLimiter.check()     — 业务层下单频率               │
│  ② OrderValidator.validate()     — 精度/数量/价格/方向          │
│  ③ PositionLimitChecker.check() — 单品种 + 总仓位              │
│  ④ LossLimitChecker.check()     — 单笔/每日/连续亏损           │
└─────────────────────────────────────────────────────────────────┘
       │
   通过 ▼                    拒绝 ▼
  下单执行              RISK_BREACH 事件 + 抛出异常
```

### 2. 订单校验器（OrderValidator）

**检查项**：

| 检查项 | 条件 | 异常 |
|--------|------|------|
| 最小下单量 | `qty >= min_size` | `OrderValidationError` |
| 最大下单量（市价） | `qty <= max_market_size` | `OrderValidationError` |
| 最大下单量（限价） | `qty <= max_limit_size` | `OrderValidationError` |
| 数量精度 | `qty % lot_size == 0` | `OrderValidationError` |
| 价格精度 | `price % tick_size == 0` | `OrderValidationError` |
| 价格偏离 | `\|price - current\| / current <= limit`（默认10%） | `OrderValidationError` |
| 方向合理性 | 非对冲模式下，有多仓禁新开空，反之亦然 | `OrderValidationError` |

**对冲模式（hedge_mode=True）**：
- OKX 双仓模式下，`hedge_mode=True`（默认值）
- 允许多空同时持仓（套利/对冲策略）
- 方向检查跳过

**自动调整（auto_adjust）**：
```python
# 不抛异常，自动将 price/qty 截断到合法精度
adjusted_req = order_validator.auto_adjust(request, instrument)
```

### 3. 仓位限制（PositionLimitChecker）

**限制维度**：

| 限制类型 | 默认值 | 计算方式 |
|----------|--------|----------|
| 单品种最大持仓量 | `per_symbol_limits`（默认无） | 直接按数量叠加 |
| 单品种最大持仓价值 | `max_position_pct=0.3`（30%） | `(持仓价值 + 新单价值) / 总权益` |
| 总持仓价值 | `max_total_position_pct=0.9`（90%） | `Σ(所有持仓价值 + 新单价值) / 总权益` |

**持仓 key 格式**：`{inst_id}_{position_side.value}`（如 `BTC-USDT_LONG`）

**设计注意**：
- 仓位限制是**账户级别**，所有策略共享
- 不支持按策略隔离（`_positions` 不携带 strategy_id）
- 多空双向持仓时数量会叠加（更严格）

### 4. 亏损限制（LossLimitChecker）

**三种亏损检查**：

| 检查 | 默认阈值 | 触发条件 | 检查时机 |
|------|----------|----------|----------|
| 单笔最大亏损 | `max_single_loss_pct=0.02`（2%） | 预估亏损 > 限额 | 下单时（需设置 stop_loss_price） |
| 每日亏损 | `max_daily_loss_pct=0.05`（5%） | 已实现亏损 / 初始权益 >= 限额 | 成交后 + 持仓更新时 |
| 连续亏损 | `max_consecutive_losses=5` | 连续亏损笔数 >= 限额 | 成交后 |

**日亏损计算**：
```python
# 每日亏损 = max(已实现亏损, 已实现+浮动亏损)
combined_pnl = _daily_pnl + _total_unrealized_pnl
daily_loss_pct = abs(combined_pnl) / _initial_equity
```
即：持仓浮亏也会触发日亏损限制。

**日亏损重置**：UTC 0:00（不是24小时滚动窗口），与交易所结算时间对齐。

**Halt 状态行为**：
```
触发条件达成 → _is_halted = True
       │
       ├──→ 新开仓订单 → 拒绝（DailyLossLimitError）
       │
       └──→ 平仓/减仓订单 → 允许通过（_is_reducing_order 判断）
                SELL + LONG → 平多（减仓）✓
                BUY + SHORT → 平空（减仓）✓
```

**连续亏损计数**：
```python
成交盈利 → _consecutive_losses = 0
成交亏损 → _consecutive_losses += 1
达到上限 → _is_halted = True
```

### 5. 频率限制（RateLimiter）

| 限制器 | 作用域 | 默认值 |
|--------|--------|--------|
| `OrderRateLimiter` | 业务层下单频率 | `max_orders=10/秒` |
| `OKXRateLimiter` | OKX API 请求频率 | OKX 官方限制（交易50次/秒，行情100次/秒） |

### 6. 策略引擎事件订阅

StrategyEngine 在 EventBus 上订阅以下事件：

| 事件 | 回调 | 触发时机 |
|------|------|----------|
| `TICK` | `_on_tick` | 每个 Tick 行情推送 |
| `BAR` | `_on_bar` | 每根 K 线完成 |
| `ORDER_UPDATED` | `_on_order` | 订单状态变化 |
| `ORDER_FILLED` | `_on_order` | 订单全部成交 |
| `ORDER_PARTIAL` | `_on_order` | 订单部分成交 |
| `ORDER_CANCELLED` | `_on_order` | 订单撤销 |
| `TRADE` | `_on_trade` | 成交回报 |
| `POSITION_UPDATED` | `_on_position` | 持仓变化 |
| `BALANCE_UPDATED` | `_on_balance` | 余额变化 |
| `RISK_BREACH` | `_on_risk_breach` | 风控触发 |

### 7. 多策略持仓管理

**持仓 key 格式**（`strategy_engine.py`）：
```python
key = f"{pos.inst_id}:{pos.position_side.value}"  # 如 BTC-USDT:LONG
```

**查询优先级**：LONG → NET → SHORT（向后兼容）

```python
# 获取持仓（兼容双仓和净仓模式）
for side in (PositionSide.LONG, PositionSide.NET, PositionSide.SHORT):
    key = f"{inst_id}:{side.value}"
    pos = self._positions.get(key)
    if pos and pos.quantity != 0:
        return pos
```

**订阅额外品种**：
```python
strategy_engine.subscribe_extra("my_strategy", "ETH-USDT")
# ETH-USDT 的行情也会路由到该策略的 on_bar/on_tick
```

### 8. OKX 双仓模式（Hedge Mode）

OKX 支持双向持仓（对冲模式），与传统的净仓（NET）模式不同：

**持仓方向**：
| 方向 | OKX posSide | 说明 |
|------|-------------|------|
| 多仓 | `LONG` | 买入开多 |
| 空仓 | `SHORT` | 卖出开空 |
| 净仓 | `NET` | 多空抵消后的净持仓（传统模式） |

**下单方向组合**：
| 操作 | side | posSide | BaseStrategy 方法 |
|------|------|---------|------------------|
| 开多 | BUY | LONG | `buy()` |
| 平多 | SELL | LONG | `sell()` 或 `_close_long()` |
| 开空 | SELL | SHORT | `short()` |
| 平空 | BUY | SHORT | `cover()` 或 `_close_short()` |

**注意**：`sell()` 默认使用 `posSide=NET`，在双仓模式下可能被拒绝。应使用 `_close_long()` 明确指定方向。

### 9. 策略生命周期

```
策略注册 (add_strategy)
       │
       ▼
  策略初始化
       │
       ├──→ __init__：创建 ArrayManager、读取 config
       ├──→ on_init()：加载历史数据、预热指标
       │
       ▼
策略启动 (start_strategy)
       │
       ├──→ on_start()：启动通知（如需要）
       └──→ active = True：开始接收行情事件
       │
       ▼
   [运行中]  ← on_bar() / on_tick() 循环处理
       │
       ▼
策略停止 (stop_strategy)
       │
       ├──→ on_stop()：清理资源
       └──→ active = False：停止接收事件
```

**异常处理**：所有回调（on_init/on_bar/on_order/on_trade）中的异常被捕获并记录为 WARNING，不会导致策略崩溃。

### 10. 账户与持仓状态同步

MainEngine 启动时（`_sync_state_from_gateways`）会从 Gateway 查询当前持仓和余额，通过事件总线广播：

```
启动时查询
    │
    ├──→ gateway.get_positions(None) → POSITION_UPDATED 事件
    ├──→ gateway.get_balance() → BALANCE_UPDATED 事件
    │
    ▼
RiskEngine 接收事件
    │
    ├──→ position_limit.update_position()
    └──→ loss_limit.update_equity()
```

这样进程重启后，风控和策略引擎能立即知道当前真实状态。

### 11. 策略与风控交互流程

```
Strategy.on_bar(bar)
    │
    ├──→ self.buy/sell(...) 
    │         │
    │         ▼
    │    MainEngine.send_order(request)
    │         │
    │         ▼
    │    RiskEngine.check_order(request)
    │         │              │
    │         │ 通过          │ 拒绝
    │         ▼              ▼
    │    gateway.send_order  RISK_BREACH 事件
    │         │
    │         ▼
    │    ORDER_SUBMITTED 事件
    │         │
    ▼         ▼
   ...      日志告警
```

---

## 常见问题

### 回测相关

**Q: 回测中的手续费如何计算？**

A: 双边计费，每次开平各扣一次：
```
开仓：price * qty * taker_fee
平仓：price * qty * taker_fee
总费用 = 开仓费 + 平仓费
```

OKX 现货费率：Taker 0.05%，Maker 0.02%。

**Q: 回测默认滑点是多少？如何调整？**

A: 默认 `slippage=0.0001`（0.01%），在 `params.yaml` 中设置：
```yaml
backtest:
  slippage: 0.0001   # 0.01% 滑点
```
或 CLI：`--slippage 0.001`（0.1%）。

**Q: 为什么 HTML 报告没有生成？**

A: 检查以下条件：
```
1. 是否使用了 --no-report？
   python -m strategy_runners.double_ma.backtest --no-report  ← 禁用

2. output/ 目录是否可写？
   ls -ld output/  → 检查权限
   chmod 755 output/

3. 是否有异常？
   查看控制台输出或 logs/
```

**Q: 网格搜索为什么很慢？**

A: 参数组合数量过多。例如：
```
fast_period: [5, 10, 15, 20]        4 个
slow_period: [20, 30, 40, 50]       4 个
→ 16 组参数 × 1000 根 K 线 × 撮合开销 = 耗时
```

优化方案：
```yaml
# 粗粒度搜索
grid:
  fast_period: [5, 10, 20]       # 减少
  slow_period: [20, 30]

# 或减少 K 线数
python -m strategy_runners.double_ma.backtest --grid-search --data-limit 500
```

**Q: 回测结果与实盘不符？**

A: 常见原因：
```
1. 滑点未配置 → 实盘有滑点，回测没有
   加上滑点参数：--slippage 0.001

2. 手续费不同 → 不同交易对费率不同
   检查 OKX 实际费率

3. K 线周期不同 → 1H 回测但实盘交易 4H
   确保参数统一

4. 时间差异 → 回测数据可能不完整
   用 --data-limit 3000 拉取更多数据

5. 预热不足 → 指标未收敛就开始交易
   增加 warmup_bars（双均线用 slow_period，RSI 用 rsi_period+5）
```

**Q: 预热根数如何确定？**

A: 预热根数应覆盖指标所需最大历史长度：
- 双均线：`warmup_bars = slow_period`（默认 30）
- RSI：`warmup_bars = rsi_period + 5`（默认 19）
- 布林带：`warmup_bars = 20`（周期）
- MACD：`warmup_bars = 26`（慢线周期）

**Q: on_init 期间能获取多少根历史K线？**

A: `warmup_bars - 1` 根。这是 `[P1-4]` 修复：防止指标前视。

### 实盘相关

**Q: 连接超时，无法启动？**

A: 检查网络和配置：
```bash
# 1. 测试网络
ping api.okx.com

# 2. 检查 API Key
cat config/okx_config.yaml | grep api_key

# 3. 查看日志
grep ERROR logs/strategy.log

# 4. 用模拟盘模式测试
编辑 config/okx_config.yaml：flag: "1"  ← 模拟盘
```

**Q: 实盘总是下单失败？**

A: 逐一排查：
```
1. 账户是否有余额？
   OKX 网页 → 账户余额

2. API Key 权限是否正确？
   OKX 网页 → API 管理 → 编辑权限
   需要：spot_api_trade（现货交易）

3. 下单量是否过小？
   BTC-USDT 最小下单量可能是 0.0001 BTC
   调整 --lot-size 0.0001

4. 价格是否超出范围？
   市价单应该没问题

5. 是否触发了风控？
   检查日志中是否有 [WARNING] 风控告警
```

**Q: 风控触发，无法继续交易？**

A: 检查今日亏损和连续亏损：
```bash
# 查看风控告警
grep "\[WARNING\]" logs/strategy.log | tail -10

# 恢复方法
1. 等待次日 00:00 UTC（日亏损重置）
2. 或手动调用 risk_engine.loss_limit.reset_halt()
3. 或修改风控参数后重启
```

**Q: 实盘与回测的止盈止损行为不同？**

A: 回测的止损是策略主动平仓，实盘的止损可以通过 OKX 算法订单（TP/SL）实现：
```python
# 实盘使用 OKX 算法订单
gateway.send_algo_order(...)  # TP/SL、Trigger、TrailingStop 等
```

**Q: 如何确认实盘使用了正确的 OKX 模式（实盘vs模拟盘）？**

A: 查看启动日志中的 flag 配置：
```
OKX flag   : 0  (0=实盘  1=模拟盘)
```
或检查 `config/okx_config.yaml` 中 `flag` 字段。

### 风控相关

**Q: 风控检查链的执行顺序是什么？**

A: 按成本从低到高：
```
① OKXRateLimiter     — API 请求频率（最快）
② OrderValidator     — 精度、最小量、价格偏离
③ PositionLimitChecker — 仓位上限
④ LossLimitChecker   — 亏损限制（最慢）
```
任一检查失败则拒绝订单。

**Q: 单日亏损是如何计算的？**

A: UTC 自然日结算，包含已实现盈亏 + 未实现盈亏：
```python
combined_pnl = _daily_pnl + _total_unrealized_pnl
if abs(combined_pnl) / _initial_equity >= max_daily_loss_pct:
    trigger_halt()
```

**Q: 连续亏损是如何统计的？**

A: 每次平仓时检查：
- 盈利交易：`consecutive_losses` 重置为 0
- 亏损交易：`consecutive_losses += 1`
- 达到 `max_consecutive_losses` 时触发风控

**Q: 最大回撤保护是如何工作的？**

A: 跟踪历史最高净值 `_peak_equity`，每次 `BALANCE_UPDATED` 时检查：
```python
drawdown = (peak_equity - current_equity) / peak_equity
if drawdown >= max_drawdown_pct:
    emergency_stop()
```
默认 `max_drawdown_pct=0.0`（禁用）。

### 开发相关

**Q: 如何创建自己的策略？**

A: 继承 `BaseStrategy` 并实现两个方法：
```python
from strategy_core.base_strategy import BaseStrategy
from strategy_core.array_manager import ArrayManager

class MyStrategy(BaseStrategy):
    def __init__(self, name, strategy_engine, inst_id, config=None):
        super().__init__(name, strategy_engine, inst_id, config)
        self._am = ArrayManager(size=100)

    def on_init(self):
        """加载历史数据"""
        bars = self.get_klines(self.inst_id, "1H", limit=100)
        for bar in bars:
            self._am.update_bar(bar)

    def on_bar(self, bar):
        """生成信号"""
        self._am.update_bar(bar)
        if self._am.inited:
            # ... 你的交易逻辑 ...
            self.buy(price=bar.close, quantity=qty)
```

然后在回测中使用：
```python
from backtest.engine import BacktestEngine

engine = BacktestEngine(
    strategy_class=MyStrategy,
    strategy_config={"param1": 10, ...},
    ...
)
metrics = engine.run()
```

**Q: 如何添加新的技术指标？**

A: 在 `ArrayManager` 中添加方法：
```python
def myrsi(self, period: int, array: bool = False):
    """自定义 RSI"""
    if len(self.close) < period:
        return 0 if not array else np.zeros(period)

    # 计算逻辑
    ...

    return result
```

**Q: 如何调试策略？**

A: 使用日志和断点：
```python
def on_bar(self, bar):
    self._am.update_bar(bar)

    if self._am.inited:
        fast_ma = self._am.sma(10)
        slow_ma = self._am.sma(30)

        # 日志输出
        self.write_log(f"close={bar.close} fast={fast_ma} slow={slow_ma}")

        # IDE 断点
        import pdb; pdb.set_trace()  # 暂停执行
```

运行回测时启用 DEBUG 日志：
```bash
python -m strategy_runners.double_ma.backtest --log-level DEBUG
```

**Q: 如何创建新的策略运行器（如 MyStrategy）？**

A: 参考 `strategy_runners/double_ma/` 目录结构：

```
strategy_runners/my_strategy/
├── __init__.py
├── backtest.py      # 回测入口（参考 double_ma/backtest.py）
├── live.py          # 实盘入口（参考 double_ma/live.py）
└── params.yaml      # 默认参数
```

---

## 重要修复记录

本节记录系统开发过程中的关键问题修复，帮助理解当前行为的由来。

### 回测引擎修复

| ID | 文件 | 问题 | 修复 |
|----|------|------|------|
| `[P0-1]` | `broker.py` | 做空时资金/保证金检查缺失 | `send_order` 新增现金和保证金充足性检查 |
| `[P1-1]` | `broker.py` | 买入平空时无法反转为多仓 | 处理 `close_qty < order.qty` 时开立剩余多头 |
| `[P1-2]` | `broker.py` | 卖出平多时无法反转为空仓 | 处理 `close_qty < order.qty` 时开立剩余空头 |
| `[P1-3]` | `broker.py` | `force_close` 手续费重复扣除 | 强制平仓只扣一次 fee |
| `[P1-4]` | `engine.py` | `on_init` 暴露全量历史数据 | 只注入 `warmup_bars - 1` 根 K 线 |
| `[P1-5]` | `engine.py` | warmup 阶段策略仍可产生订单 | `warmup` 期间 `strategy.trading=False`，结束后 `clear_pending_orders()` |
| `[P2-1]` | `broker.py` | 订单使用 `datetime.now()` 而非 bar 时间 | broker 使用 `set_current_timestamp` 设置的时间 |
| `[P2-2]` | `broker.py` | 权益快照对同一时间戳重复追加 | 同一时间戳覆盖而非追加 |
| `[P2-3]` | `engine.py` | `force_close` 期间策略可能继续下单 | 平仓前先设置 `strategy.trading=False` |
| `[P2-4]` | `performance.py` | Sortino 使用非标准算法 | 采用标准算法：target=0，`sqrt(mean(min(r-0,0)²))` |
| `[P2-5]` | `performance.py` | 缺少 `round_trips` 完整轮次统计 | 新增开仓+平仓完整轮次计数 |

### 实盘引擎修复

| ID | 文件 | 问题 | 修复 |
|----|------|------|------|
| `[FIX]` | `strategy_engine.py` | `margin_mode` 硬编码为 CASH，合约交易被拒绝 | 从 config 读取，默认 CROSS |
| `[FIX]` | `strategy_engine.py` | `_positions` key 不支持双仓模式 | key 改为 `inst_id:posSide` |
| `[FIX]` | `strategy_engine.py` | 缺少 `_close_long`/`_close_short` 方法 | 新增并正确设置 `position_side`（OKX 双仓模式） |
| `[FIX]` | `order_validator.py` | 单向模式下方向检查逻辑错误 | `hedge_mode=True` 默认值（对齐 OKX 双仓） |
| `[FIX]` | `risk_engine.py` | 并发 `check_order` 竞态条件 | 新增 `_check_lock` 保证检查链原子性 |
| `[FIX]` | `loss_limit.py` | 未实现已实现+未实现综合亏损检查 | `update_unrealized_pnl` 时合并检查 |
| `[FIX]` | `engine.py` | 进程重启后风控/策略引擎状态为空 | `_sync_state_from_gateways()` 启动时同步持仓和余额 |

---

## 性能优化建议

### 回测速度

```
当前：1000 根 K 线 → 2-3 秒

瓶颈分析：
1. 数据加载 → 0.5 秒
2. K 线遍历 → 1.5 秒（主要）
3. 指标计算 → 0.5 秒
4. 报告生成 → 0.5 秒

优化方向：
→ 减少 K 线数（--data-limit 500）
→ 禁止报告生成（--no-report）
→ 关闭日志输出（--log-level WARNING）
```

### 实盘延迟

```
当前：信号生成 → 下单 → 成交 = 100-500 ms

影响因素：
1. K 线周期 → 1H 可能延迟 30 秒（到下一根 K 线）
2. 网络延迟 → 50-200 ms
3. OKX API → 100-300 ms

改进方案：
→ 缩小 K 线周期（1H → 5m）
→ 使用 WebSocket（实时性更好）
→ 添加 Tick 级别信号
```

---

## 系统检查清单

实盘前务必检查：

- [ ] **回测验证**：最少 1 个月回测数据，Sharpe ≥ 1.0
- [ ] **参数稳定性**：网格搜索结果，最优参数不应离群体太远
- [ ] **实盘准备**：
  - [ ] OKX API Key 已配置（config/okx_config.yaml）
  - [ ] flag=1（模拟盘）测试通过
  - [ ] 小仓位（5%）运行 1-2 天无异常
- [ ] **风控检查**：
  - [ ] 最大日亏损设置合理（建议 2-5%）
  - [ ] 单品种仓位不超 50%
  - [ ] 总仓位不超 90%
- [ ] **监控就位**：
  - [ ] 日志输出正常
  - [ ] 支持 Ctrl+C 安全停止
  - [ ] 有应急止损机制

---

## 文件索引

| 功能 | 文件路径 |
|------|----------|
| 主引擎 | `core/engine.py` |
| 事件总线 | `core/event_bus.py` |
| 数据模型 | `core/models.py` |
| 枚举类型 | `core/enums.py` |
| 回测引擎 | `backtest/engine.py` |
| 模拟经纪商 | `backtest/broker.py` |
| 绩效分析 | `backtest/performance.py` |
| HTML报告 | `backtest/report.py` |
| 策略基类 | `strategy_core/base_strategy.py` |
| 指标缓冲区 | `strategy_core/array_manager.py` |
| K线聚合器 | `strategy_core/bar_generator.py` |
| 策略引擎 | `strategy_core/strategy_engine.py` |
| 双均线策略 | `strategy_core/impls/double_ma_strategy.py` |
| RSI策略 | `strategy_core/impls/rsi_strategy.py` |
| OKX网关 | `gateway/okx/okx_gateway.py` |
| 风控引擎 | `risk/risk_engine.py` |
| 订单校验 | `risk/order_validator.py` |
| 仓位限制 | `risk/position_limit.py` |
| 亏损限制 | `risk/loss_limit.py` |
| 频率限制 | `risk/rate_limiter.py` |
| 双均线回测入口 | `strategy_runners/double_ma/backtest.py` |
| 双均线实盘入口 | `strategy_runners/double_ma/live.py` |
| RSI回测入口 | `strategy_runners/rsi/backtest.py` |
| RSI实盘入口 | `strategy_runners/rsi/live.py` |
| OKX配置 | `config/okx_config.yaml` |

---

**更新日期**：2026-03-23
**维护者**：量化交易团队
