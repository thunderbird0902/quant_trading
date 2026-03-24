# scripts/ — 常用命令

## 统一数据管道（防过拟合关键）

```
实盘：WebSocket K线 → DataRecorder → SQLite
                ↓
回测：SQLite → DataFeed.from_recorded_data() → BacktestEngine
```

**核心优势**：回测和实盘使用完全相同的数据源，消除数据不一致导致的过拟合。

## 快速使用

```bash
# 录制实盘数据
./record.sh

# 用录制数据回测（推荐）
./backtest_recorded.sh rsi

# 普通回测（REST API 数据，可能有微小差异）
./backtest.sh rsi

./live.sh rsi                           # 实盘交易
./walk_forward.sh rsi                    # Walk-Forward 验证
./grid_search.sh rsi                     # 网格搜索
./data.sh                               # 拉取数据
./report.sh ./output/rsi.json            # 生成报告
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `INST_ID` | 交易品种 | BTC-USDT |
| `INTERVAL` | K 线周期 | 1H |
| `DATA_SOURCE` | 数据来源: real/mock | real |
| `CAPITAL` | 初始资金 | 100000 |
| `OUTPUT_DIR` | 输出目录 | ./output |
| `START_DATE` | 回测起始日期 YYYY-MM-DD | - |
| `END_DATE` | 回测结束日期 YYYY-MM-DD | - |
| `MOCK_DAYS` | 模拟数据天数 | 180 |
| `DB_PATH` | 录制数据库路径 | data/recorded.db |
| `POSITION_PCT` | 仓位比例 | 0.95 |
| `RISK_MAX_DAILY_LOSS` | 日最大亏损比例 | 0.05 |
| `RISK_MAX_POSITION` | 单品种最大仓位 | 0.5 |
| `N_SPLITS` | Walk-Forward 分割数 | 5 |
| `TRAIN_RATIO` | 训练集比例 | 0.7 |

## 示例

```bash
# 录制数据
INST_ID=BTC-USDT-SWAP INTERVAL=1H ./record.sh

# 用录制数据回测
./backtest_recorded.sh rsi
DB_PATH=data/recorded.db INST_ID=BTC-USDT-SWAP ./backtest_recorded.sh rsi

# 改品种/周期
INST_ID=ETH-USDT INTERVAL=4H ./backtest.sh rsi

# 指定回测时间
START_DATE=2024-01-01 END_DATE=2024-12-31 ./backtest.sh rsi

# Walk-Forward
N_SPLITS=5 ./walk_forward.sh rsi

# 实盘（小仓位先验证！）
POSITION_PCT=0.05 ./live.sh rsi
```
