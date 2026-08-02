"""
strategies/arc/live_scanner.py
ARC Live Scanner v0.2 — Validation Loop.
Every cycle logs NO_SIGNAL or PROPOSED to Decision Journal.
PAPER_MODE=true by default — no real orders.
"""
import asyncio, json, logging, hashlib, sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("arc.scanner")

from strategies.base import MarketSnapshot
from strategies.arc.rules import evaluate as arc_evaluate
from strategies.trade_executor import TradeProposal, save_proposal
from evidence.decision_journal import record_decision, record_no_signal, count_decisions

SCANNER_STATE_PATH = Path("/root/tradingos/scanner_state.json")
SIGNAL_CACHE = set()
BRIDGE_HOST = "192.168.1.77"
BRIDGE_PORT = 5555
SCAN_INTERVAL = 10
XAUUSD_SYMBOL = "XAUUSD"
PAPER_MODE = True  # No real orders

SCANNER_STATE = {
    "status": "INIT",
    "last_scan": "",
    "signals_found_today": 0,
    "last_signal_time": "",
    "current_signal": None,
    "total_decisions": 0,
    "paper_mode": PAPER_MODE,
}


def get_session(hour: int) -> str:
    if 0 <= hour < 8: return "ASIA"
    if 8 <= hour < 14: return "LONDON"
    if 14 <= hour < 22: return "NEW_YORK"
    return "ASIA"


async def bridge_request(payload: dict, timeout: float = 5) -> Optional[dict]:
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(BRIDGE_HOST, BRIDGE_PORT), timeout=timeout
        )
        w.write((json.dumps(payload) + "\n").encode())
        await w.drain()
        data = await asyncio.wait_for(r.readline(), timeout=timeout)
        w.close()
        return json.loads(data.decode().strip())
    except Exception as e:
        logger.warning(f"Bridge error: {e}")
        return None


async def fetch_data() -> Optional[list]:
    price = await bridge_request({"action": "get_price", "symbol": XAUUSD_SYMBOL})
    if price:
        bid = price.get("bid", 0)
        ask = price.get("ask", 0)
        if bid and ask:
            mid = (bid + ask) / 2
            return [{"open": bid, "high": ask, "low": bid, "close": mid, "time": datetime.now().isoformat()}]
    return None


import uuid

def generate_decision_id() -> str:
    """Generates a unique ID for a decision: D-YYYYMMDD-SHORTID"""
    date_str = datetime.now().strftime("%Y%m%d")
    unique_part = uuid.uuid4().hex[:6].upper()
    return f"D-{date_str}-{unique_part}"

async def scan_cycle():
    data = await fetch_data()
    if not data:
        logger.debug("Bridge unavailable")
        return

    c = data[0]
    now = datetime.now()
    session = get_session(now.hour)
    spread_pts = abs(c.get("high", 0) - c.get("low", 0))

    snap = MarketSnapshot(
        symbol=XAUUSD_SYMBOL, timeframe="M5",
        open=c.get("open", 0), high=c.get("high", 0),
        low=c.get("low", 0), close=c.get("close", 0),
        volume=c.get("volume", 0),
        previous_day_high=c.get("high", 0) * 1.01,
        previous_day_low=c.get("low", 0) * 0.99,
        swing_high=c.get("high", 0) * 1.005,
        swing_low=c.get("low", 0) * 0.995,
    )

    # Generate unique ID for this market moment
    decision_id = generate_decision_id()
    
    # 1. Primary Evaluation (STRICT_070)
    signal, explain = arc_evaluate(snap)
    
    # 2. Shadow Evaluation (CANDIDATE_050)
    shadow_candle = None
    shadow_explain = []
    
    from strategies.arc.detector import detect_areas, detect_range, detect_candle
    areas, area_expl = detect_areas(snap.previous_day_high, snap.previous_day_low, snap.swing_high, snap.swing_low, snap.close)
    range_zone, range_expl = detect_range(areas, snap.close)
    
    if range_zone:
        shadow_candle, shadow_candle_expl = detect_candle(
            snap.open, snap.high, snap.low, snap.close, range_zone.size_pips,
            break_pct=0.5  # OUR EXPERIMENT: 50% instead of 70%
        )
        shadow_explain = [area_expl, range_expl, shadow_candle_expl]
    else:
        shadow_explain = [area_expl, range_expl, "✖ No range"]

    # Log explain output for primary
    for line in explain:
        logger.info(f"  {line}")

    if not signal:
        # Primary Decision: NO_SIGNAL
        record_no_signal(
            decision_id=decision_id,
            strategy="ARC_v0.2",
            symbol=XAUUSD_SYMBOL,
            reason=explain[-1] if explain else "No pattern",
            session=session,
            atr=spread_pts,
            spread=spread_pts,
            decision_role="PRIMARY",
            parameter_profile="STRICT_070"
        )
        
        # Record Shadow's opinion on this same moment
        if shadow_candle:
            record_decision(
                decision_id=decision_id,
                decision="APPROVED",
                strategy="ARC_v0.2",
                symbol=XAUUSD_SYMBOL,
                direction=shadow_candle.direction,
                entry=shadow_candle.price,
                reason="SHADOW_CANDIDATE: " + shadow_candle_expl,
                session=session,
                atr=spread_pts,
                spread=spread_pts,
                decision_role="SHADOW",
                parameter_profile="CANDIDATE_050",
                experiment_id="ARC_BREAKOUT_050"
            )
        else:
            record_no_signal(
                decision_id=decision_id,
                strategy="ARC_v0.2",
                symbol=XAUUSD_SYMBOL,
                reason="Shadow also no signal",
                session=session,
                atr=spread_pts,
                spread=spread_pts,
                decision_role="SHADOW",
                parameter_profile="CANDIDATE_050"
            )

        SCANNER_STATE["total_decisions"] += 1
        return

    # Deduplicate
    sig_id = hashlib.md5(f"{signal.direction}{signal.entry:.1f}".encode()).hexdigest()[:8]
    if sig_id in SIGNAL_CACHE:
        return
    SIGNAL_CACHE.add(sig_id)
    if len(SIGNAL_CACHE) > 1000:
        SIGNAL_CACHE.clear()

    session = get_session(datetime.now().hour)
    proposal = TradeProposal(
        symbol=signal.symbol, side=signal.direction,
        entry=signal.entry, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, rr=signal.risk_reward,
        confidence=signal.confidence, strategy=signal.strategy,
        decision_id=decision_id,
        decision_version=1,
        reason=signal.reasons, session=session,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    valid, msg = proposal.validate()
    if not valid:
        decision_status = "REJECTED"
        if any(err in msg.lower() for err in ["missing", "nan", "none", "error"]):
            decision_status = "INVALID"

        record_decision(
            decision_id=decision_id,
            decision=decision_status,
            strategy="ARC_v0.2",
            symbol=proposal.symbol,
            reason=msg,
            session=session,
            atr=spread_pts,
            spread=spread_pts,
            decision_role="PRIMARY",
            parameter_profile="STRICT_070"
        )
        SCANNER_STATE["total_decisions"] += 1
        return

    # Record APPROVED decision
    record_decision(
        decision_id=decision_id,
        decision="APPROVED",
        strategy="ARC_v0.2",
        symbol=proposal.symbol,
        direction=proposal.side,
        entry=proposal.entry,
        sl=proposal.stop_loss,
        tp=proposal.take_profit,
        confidence=proposal.confidence,
        rr=proposal.rr,
        reason="; ".join(proposal.reason),
        session=session,
        atr=spread_pts,
        spread=spread_pts,
        paper=PAPER_MODE,
        decision_role="PRIMARY",
        parameter_profile="STRICT_070"
    )

    signal, explain = arc_evaluate(snap)

    # Generate Decision ID at the start of the decision process
    decision_id = generate_decision_id()

    # Log explain output every cycle
    for line in explain:
        logger.info(f"  {line}")

    if not signal:
        record_no_signal(
            decision_id=decision_id,
            strategy="ARC_v0.2",
            symbol=XAUUSD_SYMBOL,
            reason=explain[-1] if explain else "No pattern",
            session=session,
            atr=spread_pts,
            spread=spread_pts,
        )
        SCANNER_STATE["total_decisions"] += 1
        return

    # Deduplicate
    sig_id = hashlib.md5(f"{signal.direction}{signal.entry:.1f}".encode()).hexdigest()[:8]
    if sig_id in SIGNAL_CACHE:
        return
    SIGNAL_CACHE.add(sig_id)
    if len(SIGNAL_CACHE) > 1000:
        SIGNAL_CACHE.clear()

    session = get_session(datetime.now().hour)
    proposal = TradeProposal(
        symbol=signal.symbol, side=signal.direction,
        entry=signal.entry, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, rr=signal.risk_reward,
        confidence=signal.confidence, strategy=signal.strategy,
        decision_id=decision_id,
        reason=signal.reasons, session=session,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    valid, msg = proposal.validate()
    if not valid:
        # Distinguish between a business REJECT and a technical INVALID
        decision_status = "REJECTED"
        if any(err in msg.lower() for err in ["missing", "nan", "none", "error"]):
            decision_status = "INVALID"

        record_decision(
            decision_id=decision_id,
            decision=decision_status,
            strategy="ARC_v0.2",
            symbol=proposal.symbol,
            reason=msg,
            session=session,
            atr=spread_pts,
            spread=spread_pts,
        )
        SCANNER_STATE["total_decisions"] += 1
        return

    # Record APPROVED decision
    record_decision(
        decision_id=decision_id,
        decision="APPROVED",
        strategy="ARC_v0.2",
        symbol=proposal.symbol,
        direction=proposal.side,
        entry=proposal.entry,
        sl=proposal.stop_loss,
        tp=proposal.take_profit,
        confidence=proposal.confidence,
        rr=proposal.rr,
        reason="; ".join(proposal.reason),
        session=session,
        atr=spread_pts,
        spread=spread_pts,
        paper=PAPER_MODE,
    )

    if PAPER_MODE:
        logger.info(f"📋 PAPER SIGNAL: {proposal.side} @ {proposal.entry:.2f} "
                     f"RR={proposal.rr:.1f} conf={proposal.confidence:.2f}")
        SCANNER_STATE["signals_found_today"] += 1
        SCANNER_STATE["last_signal_time"] = proposal.timestamp
        SCANNER_STATE["current_signal"] = {
            "side": proposal.side, "entry": proposal.entry,
            "rr": proposal.rr, "confidence": proposal.confidence,
        }
        SCANNER_STATE["total_decisions"] += 1
    else:
        save_proposal(proposal)
        logger.info(f"🔥 SIGNAL: {proposal.side} @ {proposal.entry:.2f}")


async def run_scanner():
    logger.info(f"ARC Validation Loop started — XAUUSD M5")
    logger.info(f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'} | Interval: {SCAN_INTERVAL}s")
    SCANNER_STATE["status"] = "RUNNING"

    while True:
        try:
            SCANNER_STATE["last_scan"] = datetime.now(timezone.utc).isoformat()
            await scan_cycle()

            SCANNER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with SCANNER_STATE_PATH.open("w") as f:
                json.dump(SCANNER_STATE, f, indent=2)

            # Show stats every 10 cycles
            if SCANNER_STATE["total_decisions"] % 10 == 0 and SCANNER_STATE["total_decisions"] > 0:
                stats = count_decisions()
                logger.info(f"📊 Journal: {stats['total']} total | {stats['decisions']}")

        except Exception as e:
            logger.error(f"Scan error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run_scanner())
    except KeyboardInterrupt:
        logger.info("Scanner stopped")
        SCANNER_STATE["status"] = "STOPPED"
        with SCANNER_STATE_PATH.open("w") as f:
            json.dump(SCANNER_STATE, f, indent=2)
