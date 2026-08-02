#!/bin/bash
# /root/tradingos/scripts/disable_filters.sh
# Rollback: отключает все фильтры за 1 секунду

cat > /root/tradingos/operations/trading_mode.json << 'EOF'
{
  "sell_disabled": false,
  "risk_per_trade": 0.25,
  "max_positions": 4,
  "throttle_seconds": 0,
  "night_ban_start": 25,
  "night_ban_end": 24,
  "blacklist_min_trades": 99,
  "blacklist_max_pf": 0.0
}
EOF

echo "✅ All filters disabled"
echo "   sell_disabled=false, throttle=0, night_ban=off, blacklist=off"
