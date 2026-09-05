#!/usr/bin/env bash
# start_h2.sh — start the H2 shadow detector+executor service (foreground via nohup).
#
# SAFETY: the service does NOT place real orders while state.json has "paused": true.
# To enable live placement an operator MUST explicitly set  "paused": false  in
# /root/tradingos/profit_engines/h2_shadow/state.json FIRST (the kill-switch is the
# only gate between scanning and real PostOnly orders).
set -u
ROOT="/root/tradingos/profit_engines/h2_shadow"
PIDFILE="$ROOT/logs/h2_watch.pid"
LOG="$ROOT/logs/h2_watch.log"

cd "$ROOT/scripts"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "H2 shadow already running (pid $(cat "$PIDFILE"))"
    exit 1
fi

ST_PAUSED=$(python3 -c "import json;print(json.load(open('$ROOT/state.json')).get('paused'))" 2>/dev/null)
echo "state.json paused=$ST_PAUSED  (false => live PostOnly orders WILL be placed)"
echo "unpaused live placement requires an explicit operator decision, not a script flag."

nohup python3 h2_detector.py --mode watch >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "H2 shadow started: pid $(cat "$PIDFILE"), log $LOG"
