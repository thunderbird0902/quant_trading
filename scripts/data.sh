#!/bin/bash
# data.sh — 拉取历史 K 线
# 用法: ./data.sh [参数...]

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
DATA_SOURCE="${DATA_SOURCE:-real}"
LIMIT="${LIMIT:-1000}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
MOCK_DAYS="${MOCK_DAYS:-180}"

python -c "
import sys
sys.path.insert(0, '.')
from strategy_core.data_utils import load_bars
from strategy_runners.cli import parse_date

bars = load_bars(
    source='$DATA_SOURCE', inst_id='$INST_ID', interval='$INTERVAL',
    limit=$LIMIT, start=parse_date('$START_DATE'), end=parse_date('$END_DATE'),
    mock_days=$MOCK_DAYS, mock_seed=42, fallback_to_mock=('$DATA_SOURCE' != 'real'),
)
print(f'品种: $INST_ID  周期: $INTERVAL  数量: {len(bars)} 根')
if bars:
    print(f'时间: {bars[0].timestamp.date()} → {bars[-1].timestamp.date()}')
"
