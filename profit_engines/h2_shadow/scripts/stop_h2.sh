#!/usr/bin/env bash
# stop_h2.sh — stop the H2 shadow detector+executor process.
# NOTE: stopping the process does NOT touch state.json; the fail-closed kill-switch
# stays at whatever the operator last set. If an order/position was mid-flight the
# next start reconciles it (cancel/close) before scanning.
set -u
ROOT="/root/tradingos/profit_engines/h2_shadow"
PIDFILE="$ROOT/logs/h2_watch.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "H2 shadow stopped (pid $PID)"
    else
        echo "pid $PID not running"
    fi
    rm -f "$PIDFILE"
else
    echo "no pidfile; nothing to stop"
fi
