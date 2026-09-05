#!/bin/bash
# Auto-restart wrapper for Reality Pilot observation
cd /root
export PYTHONPATH=/root
while true; do
    echo "[$(date)] Starting run_observation..."
    python3 -m tradingos.data.run_observation
    echo "[$(date)] Process exited. Restarting in 10s..."
    sleep 10
done