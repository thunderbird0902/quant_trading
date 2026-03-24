#!/bin/bash
# record.sh — 启动实盘数据录制
# 用法: ./record.sh [参数...]

cd "$(dirname "$0")/.."

INST_ID="${INST_ID:-BTC-USDT-SWAP}"
INTERVAL="${INTERVAL:-1H}"
DB_PATH="${DB_PATH:-data/recorded.db}"

echo "============================================"
echo "  数据录制 | $INST_ID | $INTERVAL"
echo "  数据库: $DB_PATH"
echo "============================================"

python -c "
import sys
sys.path.insert(0, '.')

from core.engine import MainEngine
from core.enums import Exchange
from gateway.okx.okx_gateway import OKXGateway
from data.database import Database
from data.data_recorder import DataRecorder

okx_cfg = {'api_key': 'your_key', 'secret_key': 'your_secret', 'passphrase': 'your_passphrase'}

engine = MainEngine({'system': {'log_level': 'INFO'}})
gateway = OKXGateway(engine.event_bus, okx_cfg)
engine.add_gateway(gateway)

db = Database('$DB_PATH')
recorder = DataRecorder(engine.event_bus, db, batch_size=10)
recorder.start()

engine.connect(Exchange.OKX)
gateway.subscribe_kline('$INST_ID', '$INTERVAL')

print('录制中... 按 Ctrl+C 停止')
import time
while True:
    time.sleep(1)
" "$@"
