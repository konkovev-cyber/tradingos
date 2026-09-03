"""
strategies/trade_executor.py
Trade Executor — connects signals to exchange execution.
- ARC signals → MT5 Bridge (existing)
- Reality Discovery → BybitAdapter (new)
Human approval required for all paths.
"""
import asyncio, json, logging, math, os, sys, time
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

# 2026-08-27: Demo account switch ($100k Bybit Demo). Private endpoints go to
# api-demo.bybit.com when BYBIT_DEMO=true in the execution .env. Public market
# data stays on mainnet (identical prices, verified 2026-08-27).
def _demo_enabled() -> bool:
    v = (os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        with open("/root/trading_brain_v4/research/execution/.env") as f:
            for l in f:
                l = l.strip()
                if l.startswith("BYBIT_DEMO="):
                    return l.split("=", 1)[1].strip().lower() in ("1", "true", "yes", "on")
    except FileNotFoundError:
        pass
    return False


def _ensure_api_credentials() -> None:
    """2026-08-27: load BYBIT_API_KEY/SECRET from the execution .env when the
    caller didn't provide them via environ (standalone runs, cron scripts).
    Production services get them from EnvironmentFile / run_observation preload;
    this only fills the gap when environ is empty."""
    if os.environ.get("BYBIT_API_KEY") and os.environ.get("BYBIT_API_SECRET"):
        return
    try:
        with open("/root/trading_brain_v4/research/execution/.env") as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k in ("BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_DEMO") and v:
                        os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


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
        # F8 hardening: невалидный side отклоняется на уровне proposal,
        # не доходя до executor (defense-in-depth перед side_map).
        if self.side not in ("BUY", "SELL"):
            return False, f"Invalid side {self.side!r}"
        # Reality Mode: relaxed rules for controlled discovery
        if self.strategy in ("REALITY_DISCOVERY", "TRADFI_DISCOVERY", "MEME_SHORTS_D1"):
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


# Module-level cache: symbol -> exchange symbolType (category=linear). One real
# API request per symbol for the whole process, regardless of how many proposals
# reference it — same pattern as manual_scanner._stock_symbol_type. Only
# definitive answers are stored; transient failures are re-tried next call.
_SYMBOL_TYPES: dict[str, Optional[str]] = {}


def _symbol_type(symbol: str) -> Optional[str]:
    """Real symbolType from Bybit /v5/market/instruments-info (public, no auth).

    Returns the exchange's OWN taxonomy: 'stock' (tokenized equity incl. any
    newly listed one), 'commodity', 'innovation', or '' (regular crypto perp).
    Returns None when the symbol is unknown to the API or the request failed.
    None is NEVER cached as a fallback default ('crypto_perp'): an unknown
    symbol must not silently become CRYPTO in the correlation filter.
    """
    if symbol in _SYMBOL_TYPES:
        return _SYMBOL_TYPES[symbol]
    try:
        import httpx
        resp = httpx.get(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            timeout=10,
        )
        data = resp.json()
        if data.get("retCode") != 0:
            return None  # API error — NOT cached, next call retries
        items = (data.get("result") or {}).get("list") or []
        if not items:
            _SYMBOL_TYPES[symbol] = None  # definitively unknown — cached as "not crypto"
            return None
        st = str(items[0].get("symbolType") or "")
        _SYMBOL_TYPES[symbol] = st
        return st
    except Exception:
        return None  # network/parse failure — NOT cached, next call retries


def market_domain(symbol: str) -> str:
    """Correlation domain of an instrument, from the EXCHANGE's real symbolType
    (via _symbol_type, module-cached — one request per symbol per process) fed
    into the existing classify_instrument taxonomy.

    Priority: (1) real symbolType from API; (2) classify_instrument(symbol,
    symbol_type); (3) safe fallback → OTHER. Each class is its own domain so a
    MANUAL TradFi position (e.g. NFLXUSDT, tokenized_stock) never consumes the
    AUTO crypto same-side budget — and vice versa. NEITHER an unknown symbol
    NOR an API/classifier failure may become CRYPTO: that would let a manual
    TradFi position block auto crypto entries. Unknown/failed → OTHER, and the
    correlation filter never crashes the trade path.
    """
    if not symbol:
        return "OTHER"
    try:
        st = _symbol_type(symbol)
        if st is None:
            return "OTHER"  # unknown to exchange or request failed — not CRYPTO
        from tradingos.data.reality_universe import classify_instrument
        cls = classify_instrument(symbol, st)
    except Exception:
        return "OTHER"
    return {
        "crypto_perp": "CRYPTO",
        "tokenized_stock": "TRADFI",
        "commodity_token": "COMMODITY",
        "blocked_type": "OTHER",
    }.get(cls, "OTHER")


def _same_side_in_domain(proposal_symbol: str, bybit_side: str, open_pos) -> int:
    """Count open positions on the same side within the proposal's market domain.

    Source (MANUAL/AUTO) is intentionally NOT a criterion — open-position dicts
    carry no distinguishable source field. Domain equality is the only filter.
    """
    domain = market_domain(proposal_symbol)
    return sum(
        1 for p in open_pos
        if p.get("side") == bybit_side and market_domain(p.get("symbol", "")) == domain
    )


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
    # ─── KILL SWITCH (fail-closed, applies to ALL execution paths) ────────
    # 2026-08-24: kill_switch must be checked BEFORE routing, not only inside
    # _execute_reality. The MT5 bridge path and any future path must also
    # respect the freeze. If config is unreadable, fail-closed (block).
    try:
        with open("/root/tradingos/operations/trading_mode.json") as _f:
            _cfg = json.load(_f)
        if _cfg.get("kill_switch", False):
            logger.warning(f"🛑 BLOCKED kill_switch: {proposal.symbol} — manual kill_switch ON")
            return {"status": "BLOCKED", "error": "kill_switch ON (manual halt)"}
    except Exception as e:
        logger.error(f"🛑 BLOCKED kill_switch: config read failed: {e} — FAIL-CLOSED")
        return {"status": "BLOCKED", "error": f"kill_switch config unreadable: {e}"}

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


async def _verify_position_opened(adapter, proposal) -> tuple:
    """Verify a position for (symbol, side) actually exists on the exchange.

    Polls get_positions() a few times to allow for market-order latency.
    Returns (ok: bool, message: str).
    """
    target_side = "Buy" if proposal.side == "BUY" else "Sell"
    for _ in range(3):
        try:
            pos = await adapter.get_positions()
            for p in pos:
                if p.symbol == proposal.symbol and p.side == target_side and p.size > 0:
                    return True, f"position {p.symbol} {p.side} size={p.size}"
        except Exception as e:
            logger.warning(f"verify poll failed: {e}")
        await asyncio.sleep(2)
    return False, f"no open position found for {proposal.symbol} {target_side} after order send"


async def _execute_reality(proposal: TradeProposal) -> dict:
    """Execute a Reality Discovery trade via BybitAdapter."""
    # Safety checks
    if proposal.status != "APPROVED":
        return {"status": "ERROR", "error": f"Proposal not approved: {proposal.status}"}

    # ─── KILL SWITCH ────────────────────────────────────────
    # 2026-08-06: FIX — kill_switch from trading_mode.json was declared in config
    # but NEVER enforced. Reading it here so manual toggle actually halts entries.
    # 2026-08-24: FAIL-CLOSED — if config read fails, treat as kill_switch=true.
    # A corrupted/missing config must NOT allow orders through.
    try:
        _mode_path = "/root/tradingos/operations/trading_mode.json"
        with open(_mode_path) as _f:
            _cfg = json.load(_f)
        if _cfg.get("kill_switch", False):
            logger.warning(f"🛑 BLOCKED kill_switch: {proposal.symbol} — manual kill_switch ON")
            return {"status": "BLOCKED", "error": "kill_switch ON (manual halt)"}
    except Exception as e:
        logger.error(f"🛑 BLOCKED kill_switch: config read failed: {e} — FAIL-CLOSED (no order)")
        return {"status": "BLOCKED", "error": f"kill_switch config unreadable: {e}"}

    # ─── DEPOSIT PROTECTION (equity-based daily loss + open risk + kill switch) ──
    # Блокирует НОВЫЕ входы, если: kill switch, дневной лимит (equity-based),
    # proposed risk сломает дневной лимит, или превышен суммарный открытый риск.
    try:
        from tradingos.strategies.deposit_guard import get_guard
        # 2026-08-27 (owner): proposed_risk also percentage-based — works from
        # any deposit. Priority: risk_per_trade_pct (% equity) over the
        # absolute risk_per_trade ($). Clamps by min/max_risk_usd.
        proposed_risk = 1.5
        try:
            with open("/root/tradingos/operations/trading_mode.json") as f:
                _cfg = json.load(f)
            _pct = float(_cfg.get("risk_per_trade_pct", 0) or 0)
            if _pct > 0:
                _eq = _get_balance_or_zero()
                if _eq > 0:
                    proposed_risk = _eq * _pct / 100.0
                    proposed_risk = max(proposed_risk, float(_cfg.get("min_risk_usd", 0.5) or 0.5))
                    proposed_risk = min(proposed_risk, float(_cfg.get("max_risk_usd", 1e12) or 1e12))
                else:
                    proposed_risk = float(_cfg.get("risk_per_trade", 1.5))
            else:
                proposed_risk = float(_cfg.get("risk_per_trade", 1.5))
        except Exception:
            pass
        allowed, reason = get_guard().can_open_position(proposed_risk_usd=proposed_risk)
        if not allowed:
            logger.warning(f"🛑 BLOCKED deposit guard: {proposal.symbol} — {reason}")
            return {"status": "BLOCKED", "error": f"Deposit guard: {reason}"}
    except Exception as e:
        # Fail-safe: if guard can't load, block entry (defensive)
        logger.error(f"Deposit guard check failed — blocking: {e}")
        return {"status": "BLOCKED", "error": f"Deposit guard error: {e}"}

    # ─── SHADOW ENFORCEMENT ────────────────────────────────
    # SHADOW ENFORCEMENT for SELL — ENABLED (proven by 24h validation)
    try:
        mode_path = "/root/tradingos/operations/trading_mode.json"
        if os.path.exists(mode_path):
            with open(mode_path) as f:
                cfg = json.load(f)
            if cfg.get("sell_disabled", False) and proposal.side == "SELL":
                logger.warning(f"🛑 BLOCKED SELL: {proposal.symbol} (shadow enforcement)")
                return {"status": "BLOCKED", "error": "SELL disabled by shadow enforcement"}
    except:
        pass

    # ─── BATCH THROTTLE (SHADOW) ────────────────────────────
    # 2026-08-30: берём из trading_mode.json (throttle_seconds=60), не хардкодим 120.
    THROTTLE_SEC = 60  # FIX 2026-09-02: default = config value (60), не 120
    try:
        with open("/root/tradingos/operations/trading_mode.json") as f:
            _cfg = json.load(f)
        THROTTLE_SEC = int(_cfg.get("throttle_seconds", 60) or 60)
    except Exception:
        pass
    _last_trade_time = getattr(_execute_reality, "_last_trade_time", 0)
    if time.time() - _last_trade_time < THROTTLE_SEC:
        wait = THROTTLE_SEC - (time.time() - _last_trade_time)
        logger.warning(f"🛑 BLOCKED throttle: {proposal.symbol} — wait {wait:.0f}s")
        return {"status": "BLOCKED", "error": f"Throttle: {wait:.0f}s since last trade"}

    # ─── IDEMPOTENCY / SIGNAL DEDUP (2026-08-24) ─────────────
    # Prevents the same decision_id from producing 2 real orders across
    # restarts/retries. Persistent log survives process death.
    try:
        from tradingos.core.order_dedup import is_already_executed, mark_executed
        if is_already_executed(getattr(proposal, "decision_id", "")):
            logger.warning(f"🛑 BLOCKED dedup: {proposal.symbol} — decision_id={proposal.decision_id} already executed")
            return {"status": "BLOCKED", "error": "Duplicate signal (already executed)"}
    except Exception as e:
        logger.warning(f"order_dedup check failed (non-fatal): {e}")

    # ─── NIGHT BAN ──────────────────────────────────────────
    # DISABLED 2026-08-06 by operator request
    # utc_hour = datetime.now(timezone.utc).hour
    # if 0 <= utc_hour < 6:
    #     logger.warning(f"🛑 BLOCKED night-ban: {proposal.symbol} {proposal.side} at {utc_hour}:00 UTC")
    #     return {"status": "BLOCKED", "error": f"Night ban ({utc_hour}:00-06:00 UTC)"}

    # ─── SYMBOL BLACKLIST ──────────────────────────────────
    try:
        blacklist_path = "/root/tradingos/operations/symbol_blacklist.json"
        if os.path.exists(blacklist_path):
            with open(blacklist_path) as f:
                blacklist = json.load(f)
            if proposal.symbol in blacklist:
                logger.warning(f"🛑 BLOCKED blacklist: {proposal.symbol}")
                return {"status": "BLOCKED", "error": f"Symbol blacklisted: {proposal.symbol}"}
    except:
        pass

    # ─── CORRELATION FILTER (no over-concentration in one direction) ──
    try:
        from tradingos.strategies.bybit_position_check import get_open_positions_with_side
        open_pos = get_open_positions_with_side()
        # Count same-direction positions in the proposal's market domain ONLY.
        # MANUAL TradFi positions (e.g. NFLXUSDT) live in a different domain and
        # must NOT consume the AUTO crypto same-side budget (and vice versa).
        proposal_side = proposal.side.upper()
        bybit_side = "Buy" if proposal_side == "BUY" else "Sell"
        same_side_count = _same_side_in_domain(proposal.symbol, bybit_side, open_pos)
        max_same = 2  # from trading_mode.json max_same_side_positions
        try:
            with open("/root/tradingos/operations/trading_mode.json") as f:
                cfg = json.load(f)
            max_same = int(cfg.get("max_same_side_positions", 2))
        except Exception:
            pass
        if same_side_count >= max_same:
            logger.warning(
                f"🛑 BLOCKED correlation: {same_side_count} {bybit_side} positions already open "
                f"(max {max_same}) — would over-concentrate in one direction"
            )
            return {"status": "BLOCKED", "error": f"Too many {bybit_side} positions ({same_side_count}/{max_same})"}
    except ImportError:
        pass

    # ─── MAX LEVERAGE GUARD (5x for mini-depo) ───────────────
    # FIX 2026-08-03: proposal has no leverage field (hasattr always False),
    # so the old filter was dead code and positions opened at account default 10x.
    # Now we enforce 5x via set_leverage() right before the order.
    MAX_LEVERAGE = 5
    try:
        with open("/root/tradingos/operations/trading_mode.json") as f:
            _cfg = json.load(f)
        MAX_LEVERAGE = int(_cfg.get("max_leverage", 5))
    except Exception:
        pass
    # Risk budget from config
    # 2026-08-27 (owner): sizing must work from ANY deposit size — percentage
    # of equity is primary, absolute $ is a fallback. risk_per_trade_pct=0.5
    # means $500 on $100k and $0.40 on $80 — same relative exposure.
    # Absolute clamps: min_risk_usd (exchange minimums) / max_risk_usd (sanity).
    # FIX 2026-09-02 (audit BUG3): default risk = fail-closed 0 (block trade),
    # а не $0.25 — иначе config-read failure открывает микропозиции.
    risk = 0.0  # fail-closed default
    try:
        mode_path = "/root/tradingos/operations/trading_mode.json"
        if os.path.exists(mode_path):
            with open(mode_path) as f:
                cfg = json.load(f)
            risk_pct = float(cfg.get("risk_per_trade_pct", 0) or 0)
            if risk_pct > 0:
                equity = _get_balance_or_zero()
                if equity <= 0:
                    equity = _get_balance_or_zero.__wrapped__() if hasattr(_get_balance_or_zero, "__wrapped__") else 0.0
                if equity > 0:
                    risk = equity * risk_pct / 100.0
                    # ADAPTIVE SIZING (T6): уменьшать риск после серии лузов
                    try:
                        adaptive = cfg.get("adaptive_sizing", {})
                        if adaptive.get("enabled"):
                            from pathlib import Path
                            log_path = Path("/root/tradingos/logs/trades/trade_results.jsonl")
                            lookback = int(adaptive.get("lookback_trades", 5))
                            base_mult = float(adaptive.get("base_risk_mult", 1.0))
                            loss_mult = float(adaptive.get("loss_risk_mult", 0.5))
                            if log_path.exists():
                                recent = []
                                with open(log_path) as f:
                                    for line in f:
                                        try:
                                            t = json.loads(line.strip())
                                            if t.get("net_pnl") is not None:
                                                recent.append(t["net_pnl"] > 0)
                                        except:
                                            pass
                                recent = recent[-lookback:]
                                if len(recent) >= 2:
                                    losses = sum(1 for r in recent if not r)
                                    wins = sum(1 for r in recent if r)
                                    if losses >= 2 and losses > wins:
                                        risk *= loss_mult
                                        logger.info(f"📉 ADAPTIVE SIZING: последние {len(recent)} сделок ({losses}L/{wins}W) → риск ×{loss_mult:.0%} = ${risk:.2f}")
                                    else:
                                        risk *= base_mult
                                else:
                                    risk *= base_mult
                    except Exception as e:
                        logger.debug(f"adaptive sizing failed: {e}")
                    risk = max(risk, float(adaptive.get("min_risk_usd", cfg.get("min_risk_usd", 0.5)) or cfg.get("min_risk_usd", 0.5)))
                    risk = min(risk, float(adaptive.get("max_risk_usd", cfg.get("max_risk_usd", 1e12)) or cfg.get("max_risk_usd", 1e12)))
                else:
                    # Equity unknown → fall back to absolute $ risk (fail-safe,
                    # conservative: use the absolute value, it's small by design)
                    risk = float(cfg.get("risk_per_trade", 0.25))
            else:
                risk = float(cfg.get("risk_per_trade", 0.25))
    except Exception:
        pass
    risk_per_unit = abs(proposal.entry - proposal.stop_loss)
    if risk_per_unit <= 0:
        return {"status": "ERROR", "error": "Invalid SL (zero risk distance)"}

    # 2026-08-27 (T1.4 conformal): compute conformal SL/TP from per-symbol
    # quantile table. Logged as SHADOW alongside current SL/TP for comparison.
    # When validated via OOS WR on demo, promote to override (replace SL/TP).
    conformal_sl = conformal_tp = None
    try:
        from ml.conformal_v1.conformal_v1_sl_tp import compute_sl_tp_for_side
        # Use 4h horizon (16 bars) for SL (covers typical hold), 1h (4 bars) for TP
        conformal_sl, conformal_tp = compute_sl_tp_for_side(
            proposal.symbol, proposal.entry, proposal.side, horizon_bars=16
        )
        logger.info(
            f"📐 CONFORMAL SL/TP (shadow, 16-bar SL / 4-bar TP horizon): "
            f"symbol={proposal.symbol} side={proposal.side} entry={proposal.entry:.6g} "
            f"current SL={proposal.stop_loss:.6g} TP={proposal.take_profit:.6g} "
            f"conformal SL={conformal_sl:.6g} TP={conformal_tp:.6g}"
        )
    except Exception as e:
        logger.debug(f"conformal SL/TP failed: {e}")

    raw_quantity = risk / risk_per_unit

    # ─── EXCHANGE LOT SIZE VALIDATION (2026-08-06) ────────────────
    # FIX: orders below the symbol's lotSizeFilter are rejected by the
    # exchange (BybitError 10001 "The number of contracts exceeds minimum
    # limit"). Validate minOrderQty / qtyStep / minNotionalValue BEFORE
    # sending any order, and record the skipped opportunity.
    lot = await _get_lot_size(proposal.symbol)
    qty_step = lot["qty_step"] if lot["qty_step"] > 0 else 1.0
    entry_px = proposal.entry or 0.0
    notional_qty = 0.0
    if entry_px > 0 and lot["min_notional"] > 0:
        # qty needed to satisfy minNotionalValue, rounded UP to the lot step
        notional_qty = math.ceil((lot["min_notional"] / entry_px) / qty_step) * qty_step
    required_qty = max(lot["min_order_qty"], notional_qty)

    # Round DOWN to the exchange lot step
    # FIX 2026-09-01: float-ошибка (13216.9 % 0.1 = 0.0999) → Bybit отклонял ордер.
    # Округляем к шагу через Decimal. Для целого шага (1.0) quantize к целому.
    from decimal import Decimal, ROUND_DOWN
    _step_dec = Decimal(str(qty_step))
    # Нормализуем: 1.0 → 1, 0.1 → 0.1 (убираем лишние нули)
    _step_dec = _step_dec.normalize()
    quantity = float(Decimal(str(raw_quantity)).quantize(_step_dec, rounding=ROUND_DOWN))

    # ─── NOTIONAL CAP (2026-08-06) ─────────────────────────────
    # FIX: position size was unbounded relative to capital. On cheap coins
    # (price ~0.004) SL=2*ATR is tiny -> risk_per_unit ~0 -> quantity explodes
    # to notional 76-167% of the deposit (VETUSDT $140 on $84). Cap notional
    # at MAX_NOTIONAL_PCT of equity so a single position can never dominate.
    # 2026-08-27: max_position_size_pct from trading_mode.json — percentage of
    # equity (works from any deposit). Absolute max_position_size_usd still
    # honored when set (>0) as an extra clamp.
    MAX_NOTIONAL_PCT = 0.20  # 20% of equity — absolute ceiling
    _max_pos_usd = 0.0
    _max_pos_pct = 0.0
    try:
        with open("/root/tradingos/operations/trading_mode.json") as _f:
            _cfg = json.load(_f)
        _max_pos_usd = float(_cfg.get("max_position_size_usd", 0) or 0)
        _max_pos_pct = float(_cfg.get("max_position_size_pct", 0) or 0)
    except Exception:
        pass
    try:
        equity = _get_balance_or_zero()
        if equity > 0 and entry_px > 0:
            max_notional = equity * MAX_NOTIONAL_PCT
            if _max_pos_pct > 0:
                max_notional = min(max_notional, equity * _max_pos_pct / 100.0)
            if _max_pos_usd > 0:
                max_notional = min(max_notional, _max_pos_usd)
            max_qty = math.floor(max_notional / entry_px / qty_step) * qty_step
            if quantity > max_qty:
                logger.warning(
                    f"🛑 NOTIONAL CAP: {proposal.symbol} qty {quantity} -> {max_qty} "
                    f"(notional ${quantity*entry_px:.2f} > {MAX_NOTIONAL_PCT*100:.0f}% of ${equity:.2f})"
                )
                quantity = max_qty
    except Exception as e:
        logger.warning(f"Notional cap check failed (proceeding): {e}")

    if quantity < required_qty:
        # SKIP BEFORE ORDER: the exchange cannot physically accept this size.
        risk_required = required_qty * risk_per_unit
        logger.warning(
            f"⏭️ REALITY SKIP (pre-order): {proposal.symbol} {proposal.side} "
            f"raw_qty={raw_quantity:.4f} required={required_qty:.4f} "
            f"(minOrderQty={lot['min_order_qty']} minNotional=${lot['min_notional']} "
            f"qtyStep={qty_step}) risk_required=${risk_required:.2f} vs risk=${risk:.2f}"
        )
        try:
            sys.path.insert(0, "/root/tradingos")
            from guardian.opportunity_loss import record_rejection
            record_rejection(
                proposal.symbol, proposal.side, proposal.confidence, 0, "",
                "MIN_ORDER_QTY", proposal.entry, time.time(),
                atr=risk_per_unit / 2,
                raw_qty=round(raw_quantity, 6),
                required_qty=round(required_qty, 6),
                risk_required=round(risk_required, 2),
                qty_step=qty_step,
                min_order_qty=lot["min_order_qty"],
                min_notional=lot["min_notional"],
            )
        except Exception as e:
            logger.warning(f"opportunity loss record failed: {e}")
        return {
            "status": "SKIP",
            "reason": "MIN_ORDER_QTY",
            "symbol": proposal.symbol,
            "raw_qty": round(raw_quantity, 6),
            "required_qty": round(required_qty, 6),
            "risk_required": round(risk_required, 2),
            "min_order_qty": lot["min_order_qty"],
            "min_notional": lot["min_notional"],
            "qty_step": qty_step,
        }

    # 2026-08-27: the legacy "quantity < 1" check was written for BTC-sized
    # mini-depo tests and breaks every altcoin (qty 1 of a $0.01 coin is $0.01).
    # Exchange-side lotSizeFilter (min_order_qty / qty_step, validated above)
    # is the real minimum — keep only a sanity floor above zero.
    if quantity <= 0:
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

    # Import adapter based on exchange
    try:
        sys.path.insert(0, "/root/trading_brain_v4")
        _ensure_api_credentials()
        exchange = getattr(proposal, "exchange", "bybit")
        if exchange == "bingx":
            from exchange.bingx.adapter import BingXAdapter
            adapter = BingXAdapter(
                api_key=os.environ.get("BINGX_API_KEY", ""),
                api_secret=os.environ.get("BINGX_API_SECRET", ""),
                testnet=False,
            )
        else:
            from exchange.bybit.adapter import BybitAdapter
            adapter = BybitAdapter(
                api_key=os.environ.get("BYBIT_API_KEY", ""),
                api_secret=os.environ.get("BYBIT_API_SECRET", ""),
                testnet=False,
                demo=_demo_enabled(),
            )
        await adapter.initialize()
    except Exception as e:
        logger.error(f"Adapter init failed ({exchange}): {e}")
        return {"status": "ERROR", "error": f"Adapter init ({exchange}): {e}"}

    # Place order — enforce max leverage first (FIX 2026-08-03)
    # FIX 2026-08-04: set_leverage failure is now FAIL-CLOSED. If we can't enforce
    # the leverage cap, abort the order instead of opening at account default.
    # SL direction check (CRITICAL): BUY → SL below entry; SELL → SL above entry.
    sl_dir_ok = (proposal.side == "BUY" and proposal.stop_loss < proposal.entry) or \
                (proposal.side == "SELL" and proposal.stop_loss > proposal.entry)
    if not sl_dir_ok:
        msg = f"SL direction invalid: {proposal.side} {proposal.symbol} entry={proposal.entry} SL={proposal.stop_loss}"
        logger.error(msg)
        adapter.close()
        return {"status": "ERROR", "error": msg}
    # TP direction check (CRITICAL, added 2026-08-24): BUY → TP above entry;
    # SELL → TP below entry. Without this, a malformed proposal could attach
    # a TP on the wrong side and the position would never hit TP (or hit
    # instantly if TP is behind entry relative to the fill direction).
    tp = getattr(proposal, "take_profit", None) or getattr(proposal, "tp", None)
    if tp and float(tp) > 0:
        tp_dir_ok = (proposal.side == "BUY" and float(tp) > proposal.entry) or \
                    (proposal.side == "SELL" and float(tp) < proposal.entry)
        if not tp_dir_ok:
            msg = (f"TP direction invalid: {proposal.side} {proposal.symbol} "
                   f"entry={proposal.entry} TP={tp}")
            logger.error(msg)
            adapter.close()
            return {"status": "ERROR", "error": msg}
    try:
        ok = await adapter.set_leverage(proposal.symbol, MAX_LEVERAGE)
        if not ok:
            msg = f"set_leverage({proposal.symbol}, {MAX_LEVERAGE}x) returned False — aborting to avoid opening at default leverage"
            logger.error(msg)
            adapter.close()
            return {"status": "ERROR", "error": msg}
    except Exception as e:
        msg = f"set_leverage({proposal.symbol}, {MAX_LEVERAGE}x) failed: {e} — aborting to avoid opening at default leverage"
        logger.error(msg)
        adapter.close()
        return {"status": "ERROR", "error": msg}
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

    # FIX 2026-08-30: adapter.close() перенесён ПОСЛЕ верификации. Раньше клиент
    # закрывался до _verify_position_opened — get_positions() на закрытом клиенте
    # молча возвращал [] и реально исполненная позиция помечалась VERIFY FAILED.
    if order and order.order_id:
        # FIX (audit explore-1 HIGH): throttle + dedup ФИКСИРУЮТСЯ сразу при
        # получении order_id — ДО медленной верификации (3×2s поллов). Раньше:
        # две конкурентные сопрограммы проходили throttle-проверку, разъезжались
        # на await и обе отправляли ордер; а при провале верификации mark_executed
        # не писался → повторное исполнение того же decision_id открывало дубль.
        _execute_reality._last_trade_time = time.time()
        try:
            from tradingos.core.order_dedup import mark_executed
            mark_executed(getattr(proposal, "decision_id", ""),
                          symbol=proposal.symbol, side=proposal.side,
                          ticket=str(order.order_id))
        except Exception:
            pass  # non-fatal
        # 2026-08-06 FIX: Bybit V5 orderId is a UUID string (NOT numeric), so an
        # isdigit() check would reject valid fills. Verify the fill is REAL by
        # polling open positions on the exchange instead of trusting the response.
        try:
            verify_ok, verify_msg = await _verify_position_opened(adapter, proposal)
            if not verify_ok:
                logger.error(f"❌ REALITY ORDER VERIFY FAILED (order_id={order.order_id} жив — дедуп уже записан): "
                             f"{proposal.symbol} {proposal.side} — {verify_msg}")
                adapter.close()
                return {"status": "ERROR", "error": verify_msg}
        except Exception as e:
            logger.error(f"❌ REALITY ORDER VERIFY exception: {proposal.symbol} {proposal.side} — {e}")
            adapter.close()
            return {"status": "ERROR", "error": f"verify exception: {e}"}
        adapter.close()
        logger.info(f"✅ REALITY ORDER FILLED+VERIFIED: {proposal.symbol} {proposal.side} "
                     f"id={order.order_id} price={order.fill_price}")
        # Register with deposit guard (set day-start balance if first of day)
        try:
            from tradingos.strategies.deposit_guard import get_guard
            # Fetch current balance to anchor day-start
            import asyncio as _aio
            get_guard().on_position_opened(_get_balance_or_zero())
        except Exception as e:
            logger.error(f"Deposit guard on_open failed: {e}")
        return {
            "status": "FILLED",
            "ticket": order.order_id,
            "price": order.fill_price or proposal.entry,
        }
    else:
        logger.error(f"❌ REALITY ORDER REJECTED: {proposal.symbol} {proposal.side}")
        adapter.close()  # FIX (audit explore-1 MEDIUM): утечка клиента в этой ветке
        return {"status": "ERROR", "error": "Order rejected by exchange"}


# Cache: symbol -> (fetch_time, lot_info) — instruments-info is static per symbol,
# refetch at most once per hour.
_LOT_CACHE: dict[str, tuple[float, dict]] = {}
_LOT_CACHE_TTL = 3600


async def _get_lot_size(symbol: str) -> dict:
    """Fetch Bybit lotSizeFilter (minOrderQty / qtyStep / minNotionalValue) for a symbol.

    Public endpoint, no auth. Falls back to conservative defaults
    (min=1, step=1, no min notional) if the request fails — matches the
    pre-fix behaviour so nothing silently becomes tradeable.
    """
    now = time.time()
    cached = _LOT_CACHE.get(symbol)
    if cached and now - cached[0] < _LOT_CACHE_TTL:
        return cached[1]
    try:
        import httpx
        resp = httpx.get(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            timeout=10,
        )
        data = resp.json()
        items = data.get("result", {}).get("list", [])
        if data.get("retCode") == 0 and items:
            lot = items[0].get("lotSizeFilter", {})
            info = {
                "min_order_qty": float(lot.get("minOrderQty", 1) or 1),
                "qty_step": float(lot.get("qtyStep", 1) or 1),
                "min_notional": float(lot.get("minNotionalValue", 0) or 0),
            }
            _LOT_CACHE[symbol] = (now, info)
            return info
    except Exception as e:
        logger.warning(f"lot size fetch failed for {symbol}: {e}")
    return {"min_order_qty": 1.0, "qty_step": 1.0, "min_notional": 0.0}


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


def _get_balance_or_zero() -> float:
    """Fetch Bybit USDT equity, or 0 on failure."""
    try:
        import time as _t, hmac as _hm, hashlib as _hs, httpx as _hx
        ak, as_ = "", ""
        env = "/root/trading_brain_v4/research/execution/.env"
        if os.path.exists(env):
            for l in open(env):
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY": ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET": as_ = v.strip()
        if not ak or not as_:
            return 0.0
        ts = str(int(_t.time() * 1000))
        q = "accountType=UNIFIED&coin=USDT"
        sign = _hm.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), _hs.sha256).hexdigest()
        _base = "https://api-demo.bybit.com" if _demo_enabled() else "https://api.bybit.com"
        r = _hx.get(f"{_base}/v5/account/wallet-balance?{q}",
                    headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                             "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}, timeout=5)
        d = r.json()
        if d.get("retCode") == 0:
            lst = d.get("result", {}).get("list", [])
            if lst:
                return float(lst[0].get("totalEquity", 0))
    except Exception:
        pass
    return 0.0
