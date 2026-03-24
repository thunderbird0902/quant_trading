#!/bin/bash
# backtest_recorded.sh — 用录制数据回测
# 用法: ./backtest_recorded.sh <rsi|double_ma> [参数...]

STRATEGY="${1:-rsi}"
shift

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
DB_PATH="${DB_PATH:-data/recorded.db}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
CAPITAL="${CAPITAL:-100000}"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"

echo "============================================"
echo "  回测（录制数据）| $STRATEGY | $INST_ID"
echo "  数据源: $DB_PATH"
echo "============================================"

python -c "
import sys
sys.path.insert(0, '.')

from data.data_feed import DataFeed
from backtest.engine import BacktestEngine
from strategy_runners.cli import parse_date
from decimal import Decimal
import importlib

strategy_map = {
    'rsi': 'strategy_core.impls.rsi_strategy.RsiStrategy',
    'double_ma': 'strategy_core.impls.double_ma_strategy.DoubleMaStrategy',
}
module_path = strategy_map['$STRATEGY']
module_name, class_name = module_path.rsplit('.', 1)
strategy_class = getattr(importlib.import_module(module_name), class_name)

feed = DataFeed.from_recorded_data('$DB_PATH', '$INST_ID', '$INTERVAL')

start = parse_date('${START_DATE:-None}')
end = parse_date('${END_DATE:-None}')
bars = feed.load_history('$INST_ID', '$INTERVAL', start, end)
print(f'加载 {len(bars)} 根 K线')

engine = BacktestEngine(
    strategy_class=strategy_class,
    strategy_config={},
    inst_id='$INST_ID',
    bars=bars,
    initial_capital=Decimal('$CAPITAL'),
    taker_fee=Decimal('0.0005'),
    maker_fee=Decimal('0.0002'),
    slippage_pct=Decimal('0.0001'),
    warmup_bars=20,
    generate_report=True,
    report_output_dir='$OUTPUT_DIR',
)
metrics = engine.run()
print(f'回测完成 | Sharpe={metrics.get(\"sharpe_ratio\", 0):.2f} | 收益率={metrics.get(\"total_return_pct\", 0):.2f}%')
" "$@"
