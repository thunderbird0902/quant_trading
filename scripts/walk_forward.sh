#!/bin/bash
# walk_forward.sh — Walk-Forward 验证
# 用法: ./walk_forward.sh <rsi|double_ma> [参数...]

STRATEGY="${1:-rsi}"
shift

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
DATA_SOURCE="${DATA_SOURCE:-real}"
N_SPLITS="${N_SPLITS:-5}"
TRAIN_RATIO="${TRAIN_RATIO:-0.7}"
METRIC="${METRIC:-sharpe_ratio}"
WINDOW_TYPE="${WINDOW_TYPE:-expanding}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
MOCK_DAYS="${MOCK_DAYS:-365}"

echo "============================================"
echo "  Walk-Forward | $STRATEGY | $INST_ID"
echo "  n_splits=$N_SPLITS train_ratio=$TRAIN_RATIO"
echo "============================================"

python -c "
import sys, importlib
sys.path.insert(0, '.')

from backtest.walk_forward import walk_forward
from strategy_core.data_utils import load_bars
from strategy_runners.cli import parse_date, load_defaults
from decimal import Decimal

strategy_map = {
    'rsi': 'strategy_core.impls.rsi_strategy.RsiStrategy',
    'double_ma': 'strategy_core.impls.double_ma_strategy.DoubleMaStrategy',
}
module_path = strategy_map['$STRATEGY']
module_name, class_name = module_path.rsplit('.', 1)
strategy_class = getattr(importlib.import_module(module_name), class_name)

defaults = load_defaults(f'strategy_runners/$STRATEGY/params.yaml')
strat_cfg = defaults.get('strategy', {})

bars = load_bars(
    source='$DATA_SOURCE', inst_id='$INST_ID', interval='$INTERVAL',
    limit=2000, start=parse_date('$START_DATE'), end=parse_date('$END_DATE'),
    mock_days=$MOCK_DAYS, mock_seed=42, fallback_to_mock=True,
)

base_config = {**{k: v for k, v in strat_cfg.items() if k != 'param_grid'},
               'param_grid': strat_cfg.get('param_grid', {})}

result = walk_forward(
    bars=bars, strategy_class=strategy_class, base_config=base_config,
    n_splits=$N_SPLITS, train_ratio=$TRAIN_RATIO,
    initial_capital=Decimal('100000'), taker_fee=0.0005, maker_fee=0.0002,
    metric_to_optimize='$METRIC', window_type='$WINDOW_TYPE',
)
print(result.summary())
"
