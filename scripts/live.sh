#!/bin/bash
# live.sh — 实盘
# 用法: ./live.sh <rsi|double_ma> [参数...]

STRATEGY="${1:-rsi}"
shift

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT}"
INTERVAL="${INTERVAL:-1H}"
POSITION_PCT="${POSITION_PCT:-0.95}"
LOT_SIZE="${LOT_SIZE:-0.001}"
RISK_MAX_DAILY_LOSS="${RISK_MAX_DAILY_LOSS:-0.05}"
RISK_MAX_POSITION="${RISK_MAX_POSITION:-0.5}"
RISK_MAX_CONSECUTIVE_LOSSES="${RISK_MAX_CONSECUTIVE_LOSSES:-5}"

ARGS="--inst-id $INST_ID --interval $INTERVAL --position-pct $POSITION_PCT --risk-max-daily-loss $RISK_MAX_DAILY_LOSS --risk-max-position $RISK_MAX_POSITION --risk-max-consecutive-losses $RISK_MAX_CONSECUTIVE_LOSSES"
[[ -n "$LOT_SIZE" ]] && ARGS="$ARGS --lot-size $LOT_SIZE"

echo "============================================"
echo "  实盘 | $STRATEGY | $INST_ID | 仓位=$POSITION_PCT"
echo "============================================"

python -m "strategy_runners.${STRATEGY}.live" $ARGS "$@"
