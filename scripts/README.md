# scripts/ — 常用命令

## 快速使用

```bash
./backtest.sh rsi                        # RSI 回测
./backtest.sh double_ma                 # 双均线回测
./live.sh rsi                           # RSI 实盘
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
| `TAKER_FEE` | Taker 费率 | 0.0005 |
| `MAKER_FEE` | Maker 费率 | 0.0002 |
| `SLIPPAGE` | 滑点 | 0.0001 |
| `POSITION_PCT` | 仓位比例 | 0.95 |
| `RISK_MAX_DAILY_LOSS` | 日最大亏损比例 | 0.05 |
| `RISK_MAX_POSITION` | 单品种最大仓位 | 0.5 |
| `RISK_MAX_CONSECUTIVE_LOSSES` | 最大连续亏损次数 | 5 |
| `N_SPLITS` | Walk-Forward 分割数 | 5 |
| `TRAIN_RATIO` | 训练集比例 | 0.7 |
| `METRIC` | 优化目标 | sharpe_ratio |
| `WINDOW_TYPE` | 窗口类型 | expanding |
| `LIMIT` | K 线数量 | 1000 |

## 示例

```bash
# 改品种/周期
INST_ID=ETH-USDT INTERVAL=4H ./backtest.sh rsi

# 改数据源
DATA_SOURCE=mock ./backtest.sh rsi

# 指定回测时间
START_DATE=2024-01-01 END_DATE=2024-12-31 ./backtest.sh rsi

# 改手续费/滑点
TAKER_FEE=0.001 SLIPPAGE=0.0005 ./backtest.sh rsi

# 网格搜索
./grid_search.sh rsi
START_DATE=2024-01-01 END_DATE=2024-06-30 ./grid_search.sh rsi

# Walk-Forward
./walk_forward.sh rsi
N_SPLITS=10 METRIC=calmar_ratio ./walk_forward.sh rsi

# 实盘（小仓位先验证！）
POSITION_PCT=0.05 ./live.sh rsi

# 实盘改风控
RISK_MAX_DAILY_LOSS=0.03 RISK_MAX_POSITION=0.3 ./live.sh rsi

# 拉取数据
./data.sh
INST_ID=ETH-USDT LIMIT=500 ./data.sh
```
