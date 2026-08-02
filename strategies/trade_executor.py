"""
strategies/trade_executor.py
Trade Executor — connects signals to exchange execution.
- ARC signals → MT5 Bridge (existing)
- Reality Discovery → BybitAdapter (new)
Human approval required for all paths.
"""
import asyncio, json, logging, os, sys
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from .base import Signal

logger = logging.getLogger("trade_executor")

LOG_DIR = Path("/root/tradingos/logs/trades")
LOG_DIR.mkdir(parents=True, exist_ok=True)

PROPOSAL_FILE = Path("/root/tradingos/trade_proposal.json")
RESULT_FILE = Path("/root/tradingos/logs/trades/trade_results.jsonl")

MAX_RISK_PCT = 0.5
MAX_POSITIONS = 1
APPROVED_SYMBOLS = ["XAUUSD"]


@dataclass
class TradeProposal:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    rr: float
    confidence: float
    strategy: str
    decision_id: str  # Unique ID for the decision that created this proposal
    decision_version: int = 1
    parent_decision_id: Optional[str] = None
    session: str = ""
    reason: list = field(default_factory=list)
    status: str = "PENDING"  # PENDING → APPROVED → REJECTED → EXECUTED → CLOSED
    timestamp: str = ""

    def to_dict(self):
        return asdict(self)

    def validate(self) -> tuple[bool, str]:
        # Reality Mode: relaxed rules for controlled discovery
        if self.strategy == "REALITY_DISCOVERY":
            if "USDT" not in self.symbol and self.symbol not in APPROVED_SYMBOLS:
                return False, f"Symbol {self.symbol} not supported"
            if self.confidence < 0.40:
                return False, f"Reality confidence {self.confidence} < 0.40"
            if not self.stop_loss or self.stop_loss == 0:
                return False, "Missing SL"
            if not self.take_profit or self.take_profit == 0:
                return False, "Missing TP"
            return True, "OK"

        # Research Mode: strict rules
        if self.symbol not in APPROVED_SYMBOLS:
            return False, f"Symbol {self.symbol} not in whitelist"
        if self.rr < 2.0:
            return False, f"RR {self.rr} < 2.0"
        if self.confidence < 0.75:
            return False, f"Confidence {self.confidence} < 0.75"
        if not self.stop_loss or self.stop_loss == 0:
            return False, "Missing SL"
        if not self.take_profit or self.take_profit == 0:
            return False, "Missing TP"
        sl_pct = abs(self.entry - self.stop_loss) / self.entry * 100
        if sl_pct > MAX_RISK_PCT * 3:
            return False, f"SL {sl_pct:.2f}% too wide"
        return True, "OK"

@dataclass
class RealityTradeProposal(TradeProposal):
    """
    Loose validation for Reality Discovery mode.
    Allows Bybit symbols and lower confidence.
    """
    def validate(self) -> tuple[bool, str]:
        # 1. Symbol: Allow Bybit USDT perpetuals (Basic check: contains USDT)
        if "USDT" not in self.symbol and self.symbol not in APPROVED_SYMBOLS:
             return False, f"Symbol {self.symbol} not supported in Reality Mode"
        
        # 2. Confidence: Lower threshold for discovery
        if self.confidence < 0.40:
            return False, f"Reality confidence {self.confidence} < 0.40"
        
        # 3. Strategy check
        if self.strategy != "REALITY_DISCOVERY":
            return False, "Invalid strategy for RealityTradeProposal"
            
        # 4. Hard constraints (inherited from safety)
        if not self.stop_loss or self.stop_loss == 0:
            return False, "Missing SL"
        if not self.take_profit or self.take_profit == 0:
            return False, "Missing TP"
            
        return True, "OK"


async def check_current_positions() -> int:
    """Check how many XAUUSD positions are open via bridge."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("192.168.1.77", 5555), timeout=5
        )
        writer.write((json.dumps({"action": "get_positions"}) + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=10)
        positions = json.loads(data.decode().strip()).get("positions", [])
        writer.close()
        return len([p for p in positions if p.get("symbol") == "XAUUSD"])
    except Exception as e:
        logger.error(f"Bridge error: {e}")
        return -1


async def execute_trade(proposal: TradeProposal, volume: float = 0.01) -> dict:
    """Execute approved trade. Routes to Bybit for Reality Discovery, MT5 bridge otherwise."""
    # ------------------------------------------------------------------
    # REALITY DISCOVERY → BybitAdapter
    # ------------------------------------------------------------------
    if proposal.strategy == "REALITY_DISCOVERY":
        return await _execute_reality(proposal)

    # ------------------------------------------------------------------
    # RESEARCH / LEGACY → MT5 Bridge
    # ------------------------------------------------------------------
    result = {"status": "ERROR", "ticket": 0, "price": 0}

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("192.168.1.77", 5555), timeout=5
        )
        payload = json.dumps({
            "action": "open",
            "symbol": proposal.symbol,
            "side": proposal.side,
            "volume": volume,
            "sl": proposal.stop_loss,
            "tp": proposal.take_profit,
            "magic": 123456,
        })
        writer.write((payload + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=10)
        response = json.loads(data.decode().strip())
        writer.close()

        if response.get("status") == "ok":
            result = {
                "status": "FILLED",
                "ticket": response.get("ticket", 0),
                "price": response.get("price", proposal.entry),
            }
            logger.info(f"✅ Trade executed: {result}")
        else:
            result["error"] = response.get("error", "Unknown error")
            logger.error(f"❌ Trade rejected: {result}")
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"❌ Bridge error: {e}")

    return result


async def _execute_reality(proposal: TradeProposal) -> dict:
    """Execute a Reality Discovery trade via BybitAdapter."""
    # Safety checks
    if proposal.status != "APPROVED":
        return {"status": "ERROR", "error": f"Proposal not approved: {proposal.status}"}

    # Risk budget from config
    risk = 0.25  # default
    try:
        mode_path = "/root/tradingos/operations/trading_mode.json"
        if os.path.exists(mode_path):
            with open(mode_path) as f:
                cfg = json.load(f)
            risk = float(cfg.get("risk_per_trade", 0.25))
    except:
        pass
    risk_per_unit = abs(proposal.entry - proposal.stop_loss)
    if risk_per_unit <= 0:
        return {"status": "ERROR", "error": "Invalid SL (zero risk distance)"}

    quantity = risk / risk_per_unit
    # Round to valid lot size (step_qty=1 for DOGEUSDT)
    quantity = int(quantity)
    if quantity < 1:
        return {"status": "ERROR", "error": f"Quantity {quantity} below minimum (1)"}

    # Side mapping
    side_map = {"BUY": "Buy", "SELL": "Sell"}
    bybit_side = side_map.get(proposal.side)
    if not bybit_side:
        return {"status": "ERROR", "error": f"Invalid side: {proposal.side}"}

    # Log pre-execution check
    logger.info(
        f"REALITY_EXECUTION_CHECK: {proposal.symbol} {proposal.side} "
        f"qty={quantity:.6f} risk=${risk:.2f} "
        f"SL={proposal.stop_loss:.6f} TP={proposal.take_profit:.6f}"
    )

    # Import BybitAdapter
    try:
        sys.path.insert(0, "/root/trading_brain_v4")
        from exchange.bybit.adapter import BybitAdapter

        adapter = BybitAdapter(
            api_key=os.environ.get("BYBIT_API_KEY", ""),
            api_secret=os.environ.get("BYBIT_API_SECRET", ""),
            testnet=False,
        )
        await adapter.initialize()
    except Exception as e:
        logger.error(f"BybitAdapter init failed: {e}")
        return {"status": "ERROR", "error": f"BybitAdapter init: {e}"}

    # Place order
    try:
        order = await adapter.create_order(
            symbol=proposal.symbol,
            side=bybit_side,
            order_type="Market",
            quantity=quantity,
            market="futures",
            take_profit=proposal.take_profit,
            stop_loss=proposal.stop_loss,
        )
    except Exception as e:
        logger.error(f"Bybit order failed: {e}")
        adapter.close()
        return {"status": "ERROR", "error": f"Bybit order: {e}"}

    adapter.close()

    if order and order.order_id:
        logger.info(f"✅ REALITY ORDER FILLED: {proposal.symbol} {proposal.side} "
                     f"id={order.order_id} price={order.fill_price}")
        return {
            "status": "FILLED",
            "ticket": order.order_id,
            "price": order.fill_price or proposal.entry,
        }
    else:
        logger.error(f"❌ REALITY ORDER REJECTED: {proposal.symbol} {proposal.side}")
        return {"status": "ERROR", "error": "Order rejected by exchange"}


def save_proposal(proposal: TradeProposal):
    """Save trade proposal to disk for human review."""
    PROPOSAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROPOSAL_FILE.open("w") as f:
        json.dump(proposal.to_dict(), f, indent=2)
    print(f"\n📋 TRADE PROPOSAL: {proposal.symbol} {proposal.side}")
    print(f"   Entry: ${proposal.entry:.2f}")
    print(f"   SL:    ${proposal.stop_loss:.2f}")
    print(f"   TP:    ${proposal.take_profit:.2f}")
    print(f"   RR:    {proposal.rr:.1f}")
    print(f"   Conf:  {proposal.confidence:.2f}")
    print(f"   Status: {proposal.status}")
    print(f"   File: {PROPOSAL_FILE}\n")


def save_trade_result(trade: dict):
    """Append trade result to result log."""
    with RESULT_FILE.open("a") as f:
        f.write(json.dumps(trade) + "\n")


def render_proposal_status() -> str:
    """Show current trade proposal status for operator."""
    if not PROPOSAL_FILE.exists():
        return "No pending proposal"

    with PROPOSAL_FILE.open() as f:
        props = json.load(f)

    icon = {"PENDING": "⏳", "APPROVED": "✅", "REJECTED": "❌", "EXECUTED": "🟢", "CLOSED": "✅"}
    lines = [
        f"\n{'='*50}",
        f"  TRADE PROPOSAL — {icon.get(props.get('status',''),'?')} {props.get('status','UNKNOWN')}",
        f"{'='*50}",
        f"  {props.get('symbol')} {props.get('side')}",
        f"  Entry: ${props.get('entry',0):.2f}",
        f"  SL:    ${props.get('stop_loss',0):.2f}",
        f"  TP:    ${props.get('take_profit',0):.2f}",
        f"  RR:    {props.get('rr',0):.1f}",
        f"  Conf:  {props.get('confidence',0):.2f}",
        f"  Strat: {props.get('strategy','?')}",
    ]
    if props.get("reason"):
        lines.append(f"  Reason: {'; '.join(props['reason'])}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)
