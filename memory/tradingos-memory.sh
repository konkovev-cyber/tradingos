#!/bin/bash
# tradingos-memory — Quick system status for TradingOS
# Usage: tradingos-memory

MEMORY_DIR="/root/tradingos/memory"
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  TradingOS Memory System${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

# 1. Current Executor
echo -e "${CYAN}▶ EXECUTOR STATUS${NC}"
EXEC_PID=$(pgrep -f "executor_v0.py" 2>/dev/null)
if [ -n "$EXEC_PID" ]; then
    echo -e "  Executor v0:  ${GREEN}RUNNING${NC} (PID $EXEC_PID)"
else
    echo -e "  Executor v0:  ${RED}OFFLINE${NC}"
fi

LEGACY_PID=$(pgrep -f "ubot.*main.py" 2>/dev/null)
if [ -n "$LEGACY_PID" ]; then
    echo -e "  ubot legacy:  ${YELLOW}RUNNING${NC} (PID $LEGACY_PID) — SHOULD BE STOPPED"
else
    echo -e "  ubot legacy:  ${GREEN}DOWN${NC} (correct)"
fi
echo ""

# 2. Active Services
echo -e "${CYAN}▶ SERVICES${NC}"
for svc in tradingos.service position-guard.service trading-control.service market-agent-dashboard.service; do
    status=$(systemctl is-active $svc 2>/dev/null)
    if [ "$status" = "active" ]; then
        echo -e "  $svc: ${GREEN}RUNNING${NC}"
    else
        echo -e "  $svc: ${RED}$status${NC}"
    fi
done
echo ""

# 3. Positions
echo -e "${CYAN}▶ POSITIONS${NC}"
# Try BingX (known working)
BINGX_POS=$(curl -s --max-time 5 "https://open-api.bingx.com/openApi/swap/v2/user/positions?timestamp=$(date +%s)000" 2>/dev/null)
if echo "$BINGX_POS" | grep -q '"code":0'; then
    echo -e "  BingX: ${GREEN}API OK${NC}"
else
    echo -e "  BingX: ${YELLOW}unknown${NC}"
fi

# Bybit
BYBIT_TIME=$(curl -s --max-time 5 "https://api.bybit.com/v5/market/time" 2>/dev/null)
if echo "$BYBIT_TIME" | grep -q '"retCode":0'; then
    echo -e "  Bybit: ${YELLOW}Public OK, Auth 401${NC}"
else
    echo -e "  Bybit: ${RED}UNREACHABLE${NC}"
fi
echo ""

# 4. Memory Files
echo -e "${CYAN}▶ MEMORY FILES${NC}"
for f in AI_CONTEXT.md CURRENT_STATE.md DECISIONS.md INCIDENTS.md OPERATIONS.md OWNERSHIP.md; do
    if [ -f "$MEMORY_DIR/$f" ]; then
        mod=$(stat -c %y "$MEMORY_DIR/$f" 2>/dev/null | cut -d. -f1)
        echo -e "  $f: ${GREEN}EXISTS${NC} (modified: $mod)"
    else
        echo -e "  $f: ${RED}MISSING${NC}"
    fi
done
echo ""

# 5. Active Executor (from OWNERSHIP.md)
echo -e "${CYAN}▶ ACTIVE EXECUTOR${NC}"
if [ -f "$MEMORY_DIR/OWNERSHIP.md" ]; then
    grep -A1 "NAME:" "$MEMORY_DIR/OWNERSHIP.md" | head -2
    grep "CAN TRADE:" "$MEMORY_DIR/OWNERSHIP.md" | head -1
fi
echo ""

# 6. Last Decision
echo -e "${CYAN}▶ LAST DECISION${NC}"
if [ -f "$MEMORY_DIR/DECISIONS.md" ]; then
    tail -5 "$MEMORY_DIR/DECISIONS.md" | grep -E "^\*\*Decision:" | tail -1
fi
echo ""

# 6. Last Incident
echo -e "${CYAN}▶ LAST INCIDENT${NC}"
if [ -f "$MEMORY_DIR/INCIDENTS.md" ]; then
    tail -10 "$MEMORY_DIR/INCIDENTS.md" | grep -E "^## INC-" | tail -1
fi
echo ""

echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "  Full state: cat $MEMORY_DIR/CURRENT_STATE.md"
echo -e "  AI context: cat $MEMORY_DIR/AI_CONTEXT.md"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
