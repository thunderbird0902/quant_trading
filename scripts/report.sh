#!/bin/bash
# report.sh — 生成 HTML 报告
# 用法: ./report.sh <json_file> [标题]

if [[ $# -lt 1 ]]; then
    echo "用法: $0 <json_file> [标题]"
    exit 1
fi

JSON_FILE="$1"
TITLE="${2:-回测报告}"

cd "$(dirname "$0")/.."
python -c "
import sys, json
sys.path.insert(0, '.')
from backtest.report import generate_report

with open('$JSON_FILE') as f:
    data = json.load(f)

metrics = data.get('metrics', data)
path = generate_report(
    metrics=metrics,
    equity_curve=data.get('equity_curve', []),
    trades=data.get('trades', []),
    filled_orders=data.get('filled_orders', []),
    bars=data.get('bars', []),
    title='$TITLE',
    output_dir='$(dirname "$JSON_FILE")',
)
print(f'报告已生成: {path}')
"
