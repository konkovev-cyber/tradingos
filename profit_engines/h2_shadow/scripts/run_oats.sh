#!/usr/bin/env bash
# run_oats.sh — H2 shadow OAT suite (offline + live-read-only).
# Usage: ./run_oats.sh [--live]   (--live also runs the real-API paused scan)
set -u
cd "$(dirname "$0")"

echo "=== Gate #0 sign test ==="
python3 tests/gate0_sign_test.py || { echo "GATE0 FAIL"; exit 1; }

echo
echo "=== Detector synthetic test ==="
python3 tests/test_detector_synthetic.py || { echo "SYNTH FAIL"; exit 1; }

echo
echo "=== Kill-switch OAT (dry-run, fake client) ==="
python3 tests/test_killswitch.py || { echo "KILLSWITCH FAIL"; exit 1; }

echo
echo "=== Detector replay vs replication (474 events) ==="
python3 h2_detector.py --mode replay || { echo "REPLAY FAIL"; exit 1; }

if [ "${1:-}" == "--live" ]; then
    echo
    echo "=== LIVE READ-ONLY OAT: paused scan (state.json must be paused=true) ==="
    python3 -c "import json; s=json.load(open('../state.json')); assert s.get('paused'), 'state.json must be paused for live OAT'; print('paused OK')"
    python3 h2_detector.py --mode once
    echo "--- ledger handoff outcomes (must be all NO_TRADE paused) ---"
    python3 h2_gates.py
fi

echo
echo "OAT SUITE DONE"
