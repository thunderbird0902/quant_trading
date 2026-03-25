#!/bin/bash
# backtest.sh — 回测
# 用法: ./backtest.sh <rsi|double_ma> [参数...]

STRATEGY="${1:-rsi}"
shift

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
DATA_SOURCE="${DATA_SOURCE:-real}"
CAPITAL="${CAPITAL:-100000}"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
MOCK_DAYS="${MOCK_DAYS:-180}"
TAKER_FEE="${TAKER_FEE:-0.0005}"
MAKER_FEE="${MAKER_FEE:-0.0002}"
SLIPPAGE="${SLIPPAGE:-0.0001}"

ARGS="--inst-id $INST_ID --interval $INTERVAL --data-source $DATA_SOURCE --capital $CAPITAL --output-dir $OUTPUT_DIR --taker-fee $TAKER_FEE --maker-fee $MAKER_FEE --slippage $SLIPPAGE"

[[ -n "$START_DATE" ]] && ARGS="$ARGS --start-date $START_DATE"
[[ -n "$END_DATE" ]] && ARGS="$ARGS --end-date $END_DATE"
[[ -n "$MOCK_DAYS" ]] && ARGS="$ARGS --mock-days $MOCK_DAYS"

python -m "strategy_runners.${STRATEGY}.backtest" $ARGS "$@"
