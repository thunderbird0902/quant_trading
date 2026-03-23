"""
strategy_runners/
=================
按策略名称分目录管理回测 / 实盘脚本。

目录约定
--------
strategy_runners/
├── cli.py              # 跨策略复用的公共 CLI 构建器
├── rsi/                # RSI 均值回归策略
│   ├── params.yaml     # 默认参数（可被 CLI 覆盖）
│   ├── backtest.py     # 回测入口（支持 argparse 传参 + 网格搜索）
│   └── live.py         # 实盘入口（支持 argparse 传参）
└── <strategy_name>/    # 新策略按同样结构添加
    ├── params.yaml
    ├── backtest.py
    └── live.py

运行方式
--------
# 回测（使用默认参数）
python -m strategy_runners.rsi.backtest

# 回测（覆盖参数）
python -m strategy_runners.rsi.backtest --rsi-period 21 --oversold 25 --overbought 75

# 回测（网格搜索）
python -m strategy_runners.rsi.backtest --grid-search --data-source mock

# 实盘
python -m strategy_runners.rsi.live --rsi-period 14 --quantity 0.001
"""
