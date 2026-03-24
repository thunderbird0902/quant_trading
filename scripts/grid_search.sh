#!/bin/bash
# grid_search.sh — 网格搜索
# 用法: ./grid_search.sh <rsi|double_ma> [参数...]

STRATEGY="${1:-rsi}"
shift

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
DATA_SOURCE="${DATA_SOURCE:-real}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
MOCK_DAYS="${MOCK_DAYS:-180}"

echo "============================================"
echo "  网格搜索 | $STRATEGY | $INST_ID"
echo "============================================"

python -c "
import sys, os, importlib
from itertools import product
sys.path.insert(0, '.')

from backtest.engine import BacktestEngine
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
grid_cfg = defaults.get('backtest', {}).get('grid', {})
if not grid_cfg:
    print('错误: params.yaml 中未定义 param_grid')
    sys.exit(1)

bars = load_bars(
    source='$DATA_SOURCE', inst_id='$INST_ID', interval='$INTERVAL',
    limit=1000, start=parse_date('$START_DATE'), end=parse_date('$END_DATE'),
    mock_days=$MOCK_DAYS, mock_seed=42, fallback_to_mock=True,
)
print(f'数据: {len(bars)} 根 K线')

keys, values = list(grid_cfg.keys()), list(grid_cfg.values())
combos = list(product(*values))
print(f'搜索空间: {len(combos)} 组参数\n')

results = []
for combo in combos:
    cfg = dict(zip(keys, combo))
    cfg['interval'] = '$INTERVAL'
    try:
        engine = BacktestEngine(
            strategy_class=strategy_class, strategy_config=cfg, inst_id='$INST_ID', bars=bars,
            initial_capital=Decimal('100000'), taker_fee=Decimal('0.0005'),
            maker_fee=Decimal('0.0002'), slippage_pct=Decimal('0.0001'),
            warmup_bars=cfg.get('rsi_period', 14) + 5 if '$STRATEGY' == 'rsi' else 20,
            generate_report=False,
        )
        m = engine.run()
        results.append({**cfg, 'sharpe': round(m.get('sharpe_ratio', 0), 4),
                        'return': round(m.get('total_return_pct', 0), 2),
                        'dd': round(m.get('max_drawdown_pct', 0), 2)})
        print(f'  ✓ {cfg} -> Sharpe={results[-1][\"sharpe\"]:.4f}')
    except Exception as e:
        print(f'  ✗ {cfg} -> 失败: {e}')

results.sort(key=lambda r: r['sharpe'], reverse=True)
best = results[0]
params = {k: v for k, v in best.items() if k not in ('sharpe', 'return', 'dd')}
print(f'\n最优: Sharpe={best[\"sharpe\"]:.4f} 参数={params}')
"
