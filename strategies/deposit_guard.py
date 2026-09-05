"""
strategies/deposit_guard.py
Deposit protection — account-level risk guard (Вариант 3, без DCA).

Реализует (по критике: защита потока сделок -> защита всего депозита):
1. Equity-based daily loss:  daily_pnl = current_equity - day_start_equity
   (учитывает unrealized PnL, fees, funding — не только закрытые сделки).
2. UTC day snapshot:        day_start_equity фиксируется при смене UTC-дня,
   а НЕ при первом входе.
3. Open risk limit:         total_open_risk + proposed_risk <= max_open_risk.
4. Proposed trade risk:     проверка risk новой сделки перед входом.
5. Persistent state:        JSON-файл, защита от повреждения, fail-safe.

Правила fail-safe:
- guard не загрузился / повреждён -> can_open_position = False (block)
- can_close_position  -> почти всегда True (никогда не блокируем закрытие)
- Открытые позиции НЕ закрываются автоматически (у них SL/TP + Guardian).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # non-POSIX fallback (not expected on Linux)
    fcntl = None

logger = logging.getLogger("deposit_guard")

# Default paths
STATE_PATH = Path("/root/tradingos/operations/deposit_guard_state.json")
TRADING_MODE_PATH = Path("/root/tradingos/operations/trading_mode.json")

# 2026-08-27: Demo account switch — all Bybit reads here are private (signed)
# endpoints, so they route to api-demo.bybit.com when BYBIT_DEMO=true in .env.
def _demo_enabled() -> bool:
    import os as _os
    v = (_os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
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

_API_BASE = "https://api-demo.bybit.com" if _demo_enabled() else "https://api.bybit.com"


def _updated_at_is_fresh(updated_at: str, max_age_sec: int = 600) -> bool:
    """True if ISO updated_at is within max_age_sec of now (UTC)."""
    try:
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.fromisoformat(updated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        age = (_dt.now(_tz.utc) - ts).total_seconds()
        return 0 <= age <= max_age_sec
    except Exception:
        return False

# Default limits (overridden from trading_mode.json)
DEFAULT_DAILY_LOSS_PCT = 1.5          # дневной лимит % от старта дня
DEFAULT_HARD_KILL_PCT = 4.0           # hard kill % от старта дня
DEFAULT_MAX_OPEN_RISK_PCT = 3.0       # суммарный открытый риск % от equity
DEFAULT_MIN_LIMIT_USD = 0.5           # нижняя граница лимита в $ (для маленького депо)
DEFAULT_MIN_AVAILABLE_MARGIN_USD = 5.0  # min free margin для новых входов (v3.4)
DEFAULT_KILL_SWITCH = False
DEFAULT_ENABLED = True


class DepositGuard:
    """Singleton deposit-protection guard."""

    _instance: Optional["DepositGuard"] = None

    def __init__(self):
        self._state = self._load_state()
        self._reload_limits()

    @classmethod
    def instance(cls) -> "DepositGuard":
        if cls._instance is None:
            cls._instance = DepositGuard()
        return cls._instance

    # ─── Config ───────────────────────────────────────────────
    def _reload_limits(self):
        try:
            cfg = json.loads(TRADING_MODE_PATH.read_text())
        except Exception:
            cfg = {}
        self.enabled = bool(cfg.get("deposit_guard_enabled", DEFAULT_ENABLED))
        self.daily_loss_pct = float(cfg.get("max_daily_loss_pct", DEFAULT_DAILY_LOSS_PCT))
        self.hard_kill_pct = float(cfg.get("max_total_loss_pct", DEFAULT_HARD_KILL_PCT))
        self.max_open_risk_pct = float(cfg.get("max_open_risk_pct", DEFAULT_MAX_OPEN_RISK_PCT))
        self.min_limit_usd = float(cfg.get("min_limit_usd", DEFAULT_MIN_LIMIT_USD))
        self.min_available_margin_usd = float(
            cfg.get("min_available_margin_usd", DEFAULT_MIN_AVAILABLE_MARGIN_USD)
        )
        self.kill_switch = bool(cfg.get("kill_switch", DEFAULT_KILL_SWITCH))
        self.no_sl_fallback_pct = float(cfg.get("no_sl_fallback_pct", 2.0))
        self.max_losing_streak = int(cfg.get("max_losing_streak", 5))
        # Risk reduction (dry-run only by default — never real orders without manual confirm)
        rr = cfg.get("risk_reduction", {})
        self.rr_enabled = bool(rr.get("enabled", False))
        self.rr_dry_run = bool(rr.get("dry_run", True))
        self.rr_require_confirm = bool(rr.get("require_manual_confirm", True))
        self.rr_target_pct = float(rr.get("target_open_risk_pct", 2.5))
        self.rr_max_per_cycle = int(rr.get("max_reduce_per_cycle", 3))
        self.rr_min_interval_sec = float(rr.get("min_interval_between_reductions_sec", 300))
        # Critical alert re-fire cadence
        self.critical_alert_interval_sec = float(cfg.get("critical_alert_interval_sec", 900))
        # Critical open risk timeout escalation levels
        ct = cfg.get("critical_timeout", {})
        self.ct_enabled = bool(ct.get("enabled", True))
        self.ct_alert_min = int(ct.get("alert_minutes", 15))
        self.ct_review_min = int(ct.get("review_minutes", 30))
        self.ct_kill_min = int(ct.get("manual_kill_minutes", 60))

    # ─── State persistence ─────────────────────────────────────
    def _load_state(self) -> dict:
        if not STATE_PATH.exists():
            return self._default_state()
        try:
            data = json.loads(STATE_PATH.read_text())
            if not isinstance(data, dict):
                logger.warning("Deposit guard state invalid (not dict) — using defaults")
                return self._default_state()
            return data
        except Exception as e:
            logger.warning(f"Deposit guard state load failed: {e} — using defaults (fail-safe)")
            return self._default_state()

    def _sync_from_disk(self):
        """Re-read persisted state before mutating (cross-process safety).

        Multiple long-running services (reality loop, guardian, timers) each hold
        their own DepositGuard singleton with a stale in-memory snapshot. Without
        re-syncing, the last _save_state() overwrites newer fields written by other
        processes (e.g. updated_at, losing_streak, blocked flags) — losing-streak
        pause and daily accounting silently broke across processes (observed:
        fees_day stayed 0, updated_at stuck at 2026-08-12 while utc_day=08-15).
        """
        self._state = self._load_state()

    def _default_state(self) -> dict:
        return {
            "utc_day": None,              # YYYY-MM-DD
            "day_start_equity": None,     # equity на 00:00 UTC
            "realized_pnl_day": 0.0,      # реализованный PnL за день (закрытые)
            "fees_day": 0.0,
            "funding_day": 0.0,
            "last_equity": None,          # последняя измеренная equity
            "last_unrealized_pnl": 0.0,
            "available_margin": 0.0,      # свободная маржа (v3.4)
            "open_risk_total": 0.0,       # суммарный риск до SL открытых позиций
            "blocked_new_entries": False,
            "block_reason": "",
            "kill_switch": False,         # авто-kill (manual в config отдельно)
            "manual_kill": False,
            "kill_reason": "",
            "kill_switched_at": None,
            "critical_since": None,       # когда начался CRITICAL_OPEN_RISK (persistent)
            "critical_timeout_level": 0,  # 0=ok, 1=alert, 2=review, 3=manual_kill
            "requires_manual_review": False,
            "losing_streak": 0,           # подряд идущие убыточные сделки
            "auto_paused": False,          # AUTO→PAUSE от защитного слоя
            "auto_pause_reason": "",
            "updated_at": None,
        }

    def _save_state(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write tmp then rename (protects against corrupt mid-write)
        try:
            if fcntl is not None:
                lock_path = STATE_PATH.with_suffix(".lock")
                with open(lock_path, "a+") as lf:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                    try:
                        tmp = STATE_PATH.with_suffix(".json.tmp")
                        tmp.write_text(json.dumps(self._state, indent=2))
                        tmp.replace(STATE_PATH)
                    finally:
                        fcntl.flock(lf, fcntl.LOCK_UN)
            else:
                tmp = STATE_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(self._state, indent=2))
                tmp.replace(STATE_PATH)
        except Exception as e:
            logger.error(f"Deposit guard save failed: {e}")

    # ─── Day rollover (UTC snapshot) ───────────────────────────
    def _current_day(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_day_rollover(self, current_equity: Optional[float] = None):
        """Fix day_start_equity at UTC midnight, NOT first entry.

        FIX 2026-08-28: if equity is unknown (0/None) at rollover, do NOT
        snapshot 0 as the baseline — that produced day_start_equity=0.0 and a
        permanent NO_DAY_BASELINE / EQUITY_FETCH_ERROR latch for the whole
        day. Keep the previous utc_day (so rollover re-fires on the next
        successful equity read) and leave all day fields untouched.
        """
        today = self._current_day()
        if self._state.get("utc_day") != today:
            eq = current_equity if current_equity and current_equity > 0 else self._get_equity()
            if not eq or eq <= 0:
                logger.warning(
                    "Deposit guard: day rollover deferred — equity unknown, "
                    f"keeping utc_day={self._state.get('utc_day')}"
                )
                return
            # New UTC day — snapshot equity now
            self._state["utc_day"] = today
            self._state["day_start_equity"] = eq
            self._state["realized_pnl_day"] = 0.0
            self._state["fees_day"] = 0.0
            self._state["funding_day"] = 0.0
            self._state["blocked_new_entries"] = False
            self._state["block_reason"] = ""
            self._state["kill_switch"] = False  # auto-kill resets each day
            self._state["kill_reason"] = ""
            self._state["kill_switched_at"] = None
            self._state["losing_streak"] = 0
            self._state["auto_paused"] = False
            self._state["auto_pause_reason"] = ""
            self._save_state()
            logger.info(f"Deposit guard: new UTC day {today}, equity baseline = ${eq:.2f}")

    # ─── Equity helpers ────────────────────────────────────────
    def _get_equity(self) -> float:
        """Fetch current Bybit equity (wallet + unrealized).

        2026-08-27 hardening: a single transient failure (5s timeout /
        connection reset on api-demo) must NOT latch EQUITY_FETCH_ERROR and
        hard-block the whole contour. Retry once, then fall back to the last
        fresh equity from state (valid for 10 min). Only a fully stale state
        + failed fetch returns 0.0 (true fail-safe).
        """
        def _fetch_once() -> float:
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
                r = _hx.get(f"{_API_BASE}/v5/account/wallet-balance?{q}",
                            headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                                     "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}, timeout=5)
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if lst:
                        # FIX 2026-08-24: the field can come back as "" from the
                        # exchange during sync windows; float('') raises and
                        # bubbles up the loop, leaving the symbol in guardian
                        # state and producing duplicate Telegram notifications
                        # on every poll (TQQQ incident).
                        raw_eq = lst[0].get("totalEquity", 0)
                        try:
                            return float(raw_eq) if raw_eq != "" else 0.0
                        except (TypeError, ValueError):
                            return 0.0
            except Exception:
                pass
            return 0.0

        eq = _fetch_once()
        if eq > 0:
            return eq
        # Transient failure — one retry with short pause
        try:
            import time as _t2
            _t2.sleep(1.0)
        except Exception:
            pass
        eq = _fetch_once()
        if eq > 0:
            return eq
        # Both attempts failed — fall back to last known equity if fresh (< 10 min)
        try:
            from datetime import datetime as _dt, timezone as _tz
            last = self._state.get("last_equity")
            updated = self._state.get("updated_at")
            if last and updated and _updated_at_is_fresh(updated, max_age_sec=600):
                return float(last)
        except Exception:
            pass
        return 0.0

    def _fetch_wallet_account(self) -> dict | None:
        """GET /v5/account/wallet-balance (UNIFIED) → account-level dict or None.

        Shared fetcher for equity/available-margin reads (read-only).
        """
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
                return None
            ts = str(int(_t.time() * 1000))
            q = "accountType=UNIFIED&coin=USDT"
            sign = _hm.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), _hs.sha256).hexdigest()
            r = _hx.get(f"{_API_BASE}/v5/account/wallet-balance?{q}",
                        headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                                 "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                if lst:
                    return lst[0]
        except Exception:
            pass
        return None

    def _get_available_margin(self) -> float:
        """Fetch Bybit available balance for NEW order placement (UTA). v3.5.

        Primary source: account-level `totalAvailableBalance` — the UTA figure
        for order availability (net of initial margin, order margin, haircuts).
        The former source (coin-level `availableToWithdraw`) is a WITHDRAWAL
        figure, not an order-placement figure, and is routinely empty on UTA —
        reading it as 0 caused a permanent phantom MARGIN_BUFFER block
        (2026-08-17: avail=$0.00 while totalAvailableBalance=$61.28).

        Fail-safe: field missing/empty/invalid or fetch error → 0.0 (= BLOCK).
        NO computed fallback: equity − used_margin ignores collateral haircuts,
        frozen orders and order margin (Bybit UTA docs), so it must never be
        used as a production substitute. The snapshot for the diagnostic log
        is stored in self._last_margin_snapshot.
        """
        snap = {"equity": None, "margin_balance": None, "available_balance": None,
                "used_margin": None, "source_field": "totalAvailableBalance"}
        try:
            acc = self._fetch_wallet_account()
            if not acc:
                snap["source_field"] = "FETCH_ERROR"
                self._last_margin_snapshot = snap
                return 0.0
            snap.update({
                "equity": acc.get("totalEquity"),
                "margin_balance": acc.get("totalMarginBalance"),
                "available_balance": acc.get("totalAvailableBalance"),
                "used_margin": acc.get("totalInitialMargin"),
            })
            raw = acc.get("totalAvailableBalance")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                snap["source_field"] = "totalAvailableBalance:MISSING"
                self._last_margin_snapshot = snap
                return 0.0
            try:
                val = float(raw)
            except (TypeError, ValueError):
                snap["source_field"] = "totalAvailableBalance:INVALID"
                self._last_margin_snapshot = snap
                return 0.0
            self._last_margin_snapshot = snap
            return val
        except Exception:
            self._last_margin_snapshot = {"source_field": "FETCH_ERROR"}
            return 0.0

    def _get_open_risk(self) -> tuple[float, int]:
        """Sum of risk-to-SL for all open positions + count of positions without SL.

        Positions without SL are counted conservatively (fallback risk), because
        "no stop" must never be treated as zero risk (critique point #5).
        """
        missing_sl_count = 0
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
                return self._state.get("open_risk_total", 0.0), missing_sl_count
            ts = str(int(_t.time() * 1000))
            q = "category=linear&settleCoin=USDT"
            sign = _hm.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), _hs.sha256).hexdigest()
            r = _hx.get(f"{_API_BASE}/v5/position/list?{q}",
                        headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                                 "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                total = 0.0
                for p in lst:
                    size = float(p.get("size", 0))
                    if size <= 0:
                        continue
                    entry = float(p.get("avgPrice", 0))
                    sl = float(p.get("stopLoss", 0) or 0)
                    if entry > 0 and sl > 0:
                        total += abs(entry - sl) * abs(size)
                    else:
                        # No SL — conservative fallback: notional * max_adverse_pct
                        missing_sl_count += 1
                        mark = float(p.get("markPrice", entry) or entry)
                        notional = abs(size) * mark
                        total += notional * self._no_sl_fallback_pct / 100.0
                return total, missing_sl_count
        except Exception:
            pass
        return self._state.get("open_risk_total", 0.0), missing_sl_count

    # ─── Limit computation (% with $ floor) ────────────────────
    def _daily_limit_usd(self) -> float:
        start = self._state.get("day_start_equity") or self._get_equity() or 100.0
        return max(self.min_limit_usd, start * self.daily_loss_pct / 100.0)

    def _hard_kill_usd(self) -> float:
        start = self._state.get("day_start_equity") or self._get_equity() or 100.0
        return max(self.min_limit_usd * 2, start * self.hard_kill_pct / 100.0)

    def _max_open_risk_usd(self) -> float:
        eq = self._get_equity() or 100.0
        return max(self.min_limit_usd, eq * self.max_open_risk_pct / 100.0)

    def pending_limits_risk(self) -> float:
        """P1.5b (2026-08-29): риск, зарезервированный под НЕисполненные
        WAIT-лимитки (manual_wait_limits.json, status=PLACED).

        Открытые позиции считает _get_open_risk(). Но лимитки ещё не позиции:
        если висит 4 штуки и рынок резко идёт в зону — они могут исполниться
        почти одновременно, обходя открытый risk-cap. Здесь суммируем
        qty × dist(цена-лимита → SL) для всех PLACED — это максимальная
        реализация риска, если все лимитки разом станут позициями.
        """
        try:
            import json as _json
            p = Path("/root/tradingos/operations/manual_wait_limits.json")
            if not p.exists():
                return 0.0
            data = _json.loads(p.read_text())
            total = 0.0
            for sym, rec in data.items():
                if rec.get("status") != "PLACED":
                    continue
                qty = float(rec.get("qty", 0) or 0)
                wl = float(rec.get("price", 0) or 0)
                sl = float(rec.get("sl", 0) or 0)
                if qty > 0 and wl > 0 and sl > 0:
                    total += abs(wl - sl) * qty
            return total
        except Exception:
            return 0.0

    # ─── Position detail + risk-reduction planning (dry-run) ──
    def _get_positions_detail(self) -> list[dict]:
        """Fetch positions with per-position risk, for reduction planning."""
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
                return []
            ts = str(int(_t.time() * 1000))
            q = "category=linear&settleCoin=USDT"
            sign = _hm.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), _hs.sha256).hexdigest()
            r = _hx.get(f"{_API_BASE}/v5/position/list?{q}",
                        headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                                 "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}, timeout=5)
            d = r.json()
            out = []
            if d.get("retCode") == 0:
                for p in d.get("result", {}).get("list", []):
                    size = float(p.get("size", 0))
                    if size <= 0:
                        continue
                    entry = float(p.get("avgPrice", 0))
                    mark = float(p.get("markPrice", entry) or entry)
                    sl = float(p.get("stopLoss", 0) or 0)
                    notional = abs(size) * mark
                    if entry > 0 and sl > 0:
                        risk = abs(entry - sl) * abs(size)
                        missing_sl = False
                    else:
                        risk = notional * self.no_sl_fallback_pct / 100.0
                        missing_sl = True
                    out.append({
                        "symbol": p.get("symbol", ""),
                        "side": p.get("side", ""),
                        "qty": abs(size),
                        "entry": entry,
                        "mark": mark,
                        "sl": sl,
                        "notional": notional,
                        "risk_usd": risk,
                        "unrealized_pnl": float(p.get("unrealisedPnl", 0)),
                        "missing_sl": missing_sl,
                    })
            return out
        except Exception:
            return []

    def _alert_repeat(self, text: str, interval_sec: float = 900):
        """Re-fire alert only if last alert for this status is older than interval."""
        now = time.time()
        key = text.splitlines()[0][:40]
        last = self._state.get("_last_alert_repeat", {}).get(key, 0)
        if now - last < interval_sec:
            return
        self._state.setdefault("_last_alert_repeat", {})[key] = now
        self._save_state()
        self._alert(text)

    def plan_risk_reduction(self) -> dict:
        """Plan which positions to reduce/close to bring open_risk under target.

        DRY-RUN ONLY: computes + logs + alerts the plan, does NOT execute orders.
        Returns plan dict for callers to review.
        """
        if not self.rr_enabled:
            return {"enabled": False, "actions": []}
        positions = self._get_positions_detail()
        if not positions:
            return {"enabled": True, "actions": [], "current_risk": 0.0}
        total_risk = sum(p["risk_usd"] for p in positions)
        target = self._get_equity() or 100.0
        target_risk = max(self.min_limit_usd, target * self.rr_target_pct / 100.0)
        need_reduce = max(0.0, total_risk - target_risk)

        # Priority: missing_sl first, then highest risk_usd
        positions_sorted = sorted(
            positions,
            key=lambda p: (not p["missing_sl"], -p["risk_usd"]),
        )
        actions = []
        reduced = 0.0
        for p in positions_sorted:
            if reduced >= need_reduce or len(actions) >= self.rr_max_per_cycle:
                break
            actions.append({
                "symbol": p["symbol"],
                "side": p["side"],
                "qty": p["qty"],
                "risk_usd": round(p["risk_usd"], 2),
                "notional": round(p["notional"], 2),
                "missing_sl": p["missing_sl"],
                "unrealized_pnl": round(p["unrealized_pnl"], 4),
                "action": "close" if p["missing_sl"] or p["risk_usd"] > (total_risk - reduced - target_risk) else "reduce",
            })
            reduced += p["risk_usd"]

        plan = {
            "enabled": True,
            "dry_run": self.rr_dry_run,
            "require_manual_confirm": self.rr_require_confirm,
            "current_risk": round(total_risk, 2),
            "target_risk": round(target_risk, 2),
            "need_reduce_risk": round(need_reduce, 2),
            "actions": actions,
            # Post-cycle projection (v3.3+): after this cycle's reductions
            "expected_risk_after": round(total_risk - reduced, 2),
            "hard_kill_usd": round(self._hard_kill_usd(), 2),
            "open_risk_limit_usd": round(self._max_open_risk_usd(), 2),
            "below_hard_kill": (total_risk - reduced) <= self._hard_kill_usd(),
            "below_open_limit": (total_risk - reduced) <= self._max_open_risk_usd(),
            "remaining_to_target": round(max(0.0, (total_risk - reduced) - target_risk), 2),
            "next_cycle_needed": (total_risk - reduced) > target_risk,
        }
        # Log + alert plan (dry-run: no orders placed)
        exp_after = total_risk - reduced
        logger.warning(
            f"🔧 RISK REDUCTION PLAN (dry_run={self.rr_dry_run}): "
            f"current ${total_risk:.2f} target ${target_risk:.2f} "
            f"need -${need_reduce:.2f} in {len(actions)} action(s) "
            f"→ expected ${exp_after:.2f}"
        )
        status_after = (
            "ниже hard kill" if exp_after <= self._hard_kill_usd()
            else "ВЫШЕ hard kill"
        )
        self._alert(
            f"🔧 <b>План снижения риска</b> (dry-run)\n"
            f"Текущий риск: ${total_risk:.2f}\n"
            f"Hard kill: ${self._hard_kill_usd():.2f} | Лимит: ${self._max_open_risk_usd():.2f}\n"
            f"Цель: ${target_risk:.2f}\n"
            f"Нужно убрать: ${need_reduce:.2f}\n"
            f"Действий: {len(actions)}\n"
            + "\n".join(
                f"{a['action'].upper()} {a['symbol']} {a['side']} "
                f"риск=${a['risk_usd']} pnl={a['unrealized_pnl']:+.3f}"
                + (" [НЕТ SL!]" if a["missing_sl"] else "")
                for a in actions
            )
            + f"\n\nПосле цикла: ${exp_after:.2f} ({status_after})\n"
            + (f"Осталось до цели: ${plan['remaining_to_target']:.2f} — нужен следующий цикл"
               if plan["next_cycle_needed"] else "Цель достигнута")
        )
        return plan

    # ─── Entry checks (called by Reality before opening) ───────
    def can_open_position(self, proposed_risk_usd: Optional[float] = None,
                          current_equity: Optional[float] = None) -> tuple[bool, str]:
        """Return (allowed, reason). Equity-based + open-risk + proposed-risk checks.

        v3.2: returns explicit status strings, cancels opening orders on block,
        and distinguishes CRITICAL_OPEN_RISK (risk > hard_kill).
        """
        if not self.enabled:
            return True, "OK"
        self._reload_limits()
        self._sync_from_disk()
        equity = current_equity if current_equity and current_equity > 0 else self._get_equity()

        # 0. Equity fetch failure (API error → equity=0) — block entries,
        #    but do NOT trigger a false "equity_drop" kill switch.
        if not equity or equity <= 0:
            self._state["blocked_new_entries"] = True
            self._state["block_reason"] = "EQUITY_FETCH_ERROR"
            self._save_state()
            return False, "EQUITY_FETCH_ERROR"

        self._check_day_rollover(equity)
        self._state["last_equity"] = equity
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 1. Manual kill switch (config)
        if self.kill_switch:
            return False, "MANUAL_KILL"

        # 1b. AUTO safety guard pause (losing streak etc.) — block new entries
        if self._state.get("auto_paused"):
            return False, f"AUTO_PAUSE:{self._state.get('auto_pause_reason','')}"

        # 2. Auto kill switch (triggered earlier) OR manual_kill via critical timeout
        if self._state.get("kill_switch") or self._state.get("manual_kill"):
            return False, "HARD_KILL"

        # 3. day_start_equity must be known. FIX 2026-08-28: a zero/missing
        #    baseline (rollover ran while API was down, or state frozen by a
        #    service stop across midnight) used to hard-block the whole day
        #    (NO_DAY_BASELINE, limit $1.50 vs fallback $100). We have a fresh
        #    positive equity in hand here — adopt it as the day baseline
        #    instead of blocking. Safe: baseline=current equity ⇒ counted loss
        #    today starts at 0; the hard-kill/daily-limit checks below still
        #    protect everything after this point.
        start = self._state.get("day_start_equity")
        if not start or start <= 0:
            self._state["day_start_equity"] = equity
            self._state["realized_pnl_day"] = 0.0
            self._state["fees_day"] = 0.0
            self._state["funding_day"] = 0.0
            self._state["blocked_new_entries"] = False
            self._state["block_reason"] = ""
            self._save_state()
            logger.info(
                f"Deposit guard: day baseline recovered from {start!r} → ${equity:.2f} "
                f"(rollover missed / zero baseline)"
            )
            start = equity

        # 4. Hard kill: equity drop from day start
        daily_pnl = equity - start  # negative = loss
        if -daily_pnl >= self._hard_kill_usd():
            self._trigger_kill("equity_drop", -daily_pnl)
            return False, "HARD_KILL"

        # 5. Daily loss limit (equity-based, includes unrealized)
        # CRITICAL FIX 2026-08-05: was blocking all entries when day already in loss,
        # even if proposed_risk=0 → trade was blocked for the rest of the day.
        # Correct: block only if a NEW trade would push past the limit. If daily_pnl
        # is already past limit AND no proposed risk, allow (don't double-punish).
        # The hard_kill check (#4) already handles catastrophic drop.
        proposed = proposed_risk_usd or 0.0
        if -daily_pnl >= self._daily_limit_usd() and proposed > 0:
            self._block("DAILY_LOSS_LIMIT")
            return False, "DAILY_LOSS_LIMIT"

        # 6. Proposed trade would break daily limit
        # Block ONLY if we're already in loss AND the new trade risk would push
        # us further past the daily limit. On a clean day (pnl >= 0), a single
        # trade whose risk is under daily_limit is allowed.
        if daily_pnl < 0 and (-daily_pnl + proposed >= self._daily_limit_usd()):
            self._block("PROPOSED_RISK_TOO_HIGH")
            return False, "PROPOSED_RISK_TOO_HIGH"

        # 7. Open risk limit: total_open_risk + proposed <= max
        open_risk, missing_sl = self._get_open_risk()
        self._state["open_risk_total"] = open_risk
        self._state["missing_sl_count"] = missing_sl

        # 7a. CRITICAL: open risk already exceeds hard_kill (positions could
        #     lose more than the hard-kill allowance) — needs attention.
        if open_risk > self._hard_kill_usd():
            # Track critical_since persistently (survives restart so escalation
            # doesn't restart from zero after a service bounce).
            if not self._state.get("critical_since"):
                self._state["critical_since"] = time.time()
            since = self._state["critical_since"]
            elapsed_min = (time.time() - since) / 60.0

            self._block("CRITICAL_OPEN_RISK")
            self._alert_repeat(
                f"🚨 CRITICAL OPEN RISK\n"
                f"Открытый риск ${open_risk:.2f} > hard kill ${self._hard_kill_usd():.2f}\n"
                f"Позиции без SL: {missing_sl}\n"
                f"Критично уже {elapsed_min:.0f} мин.\n"
                f"Новые входы заблокированы. Нужно снизить риск.",
                self.critical_alert_interval_sec,
            )

            # Escalation: critical persists too long → level up.
            level = 0
            if self.ct_enabled and elapsed_min >= self.ct_kill_min:
                level = 3
            elif self.ct_enabled and elapsed_min >= self.ct_review_min:
                level = 2
            elif self.ct_enabled and elapsed_min >= self.ct_alert_min:
                level = 1
            self._state["critical_timeout_level"] = max(self._state.get("critical_timeout_level", 0), level)

            if level >= 3:
                self._state["manual_kill"] = True
                self._state["requires_manual_review"] = True
                self._alert_repeat(
                    f"🚨 CRITICAL OPEN RISK — MANUAL KILL\n"
                    f"Критично {elapsed_min:.0f} мин. Торговля до ручного сброса.\n"
                    f"Риск ${open_risk:.2f} > hard kill ${self._hard_kill_usd():.2f}",
                    self.critical_alert_interval_sec,
                )
            elif level >= 2:
                self._state["requires_manual_review"] = True
                self._alert_repeat(
                    f"⚠️ CRITICAL OPEN RISK — ТРЕБУЕТ РУЧНОГО ВМЕШАТЕЛЬСТВА\n"
                    f"Критично {elapsed_min:.0f} мин. Уменьшите legacy-позиции.",
                    self.critical_alert_interval_sec,
                )
            self._save_state()

            # Plan risk reduction (dry-run by default — alerts plan, no orders)
            try:
                self.plan_risk_reduction()
            except Exception as e:
                logger.error(f"Risk reduction plan failed: {e}")
            return False, "CRITICAL_OPEN_RISK"

        # 7b. Open risk above soft limit — block new entries.
        # P1.5b: к открытому риску добавляем резерв под PLACED-лимитки
        # (этот вход может быть как раз одной из них → proposed участвует).
        reserved = self.pending_limits_risk()
        self._state["pending_limits_reserved_risk"] = reserved
        if open_risk + reserved + proposed > self._max_open_risk_usd():
            # Not critical, but over limit — clear critical tracking
            self._clear_critical()
            self._block("OPEN_RISK_OVER_LIMIT")
            return False, "OPEN_RISK_OVER_LIMIT"

        # Risk within limits — clear any critical tracking
        self._clear_critical()

        # 8. Available (free) margin buffer — NO NEW ENTRY without headroom.
        # v3.4: when margin is nearly fully utilized the account cannot absorb
        # adverse moves; new entries need min free margin. Boundary: block when
        # available < min_available_margin_usd (strictly below) → exactly the
        # threshold is allowed. Existing positions are NEVER touched here.
        # v3.5: source = totalAvailableBalance (UTA order-placement figure);
        # missing/invalid source fails safe to 0.0 (BLOCK), no computed fallback.
        available = self._get_available_margin()
        self._state["available_margin"] = available
        snap = getattr(self, "_last_margin_snapshot", None) or {}
        blocked = available < self.min_available_margin_usd
        logger.info(
            "MARGIN_BUFFER diag: equity=%s | margin_balance=%s | available_balance=%s | "
            "used_margin=%s | source_field=%s | margin_buffer_decision=%s "
            "(avail=$%.2f vs min=$%.2f)",
            snap.get("equity"), snap.get("margin_balance"),
            snap.get("available_balance"), snap.get("used_margin"),
            snap.get("source_field"),
            "BLOCK" if blocked else "ALLOW",
            available, self.min_available_margin_usd,
        )
        if blocked:
            self._block(
                f"MARGIN_BUFFER:avail=${available:.2f}<${self.min_available_margin_usd:.2f}"
            )
            return False, self._state["block_reason"]

        self._state["blocked_new_entries"] = False
        self._state["block_reason"] = "OK"
        self._save_state()
        return True, "OK"

    def _block(self, reason: str):
        """Set blocked state + cancel opening orders."""
        self._state["blocked_new_entries"] = True
        self._state["block_reason"] = reason
        self._save_state()
        # Cancel any pending opening (non-reduce-only) orders
        try:
            self._cancel_opening_orders()
        except Exception as e:
            logger.error(f"Deposit guard cancel opening orders failed: {e}")

    def _cancel_opening_orders(self):
        """Cancel all non-reduce-only open orders (entry orders must not fill post-block)."""
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
            return
        # Fetch open orders, cancel those that are not reduce-only
        ts = str(int(_t.time() * 1000))
        q = "category=linear&settleCoin=USDT"
        sign = _hm.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), _hs.sha256).hexdigest()
        headers = {"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                   "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}
        r = _hx.get(f"{_API_BASE}/v5/order/realtime?{q}", headers=headers, timeout=5)
        d = r.json()
        if d.get("retCode") != 0:
            return
        orders = d.get("result", {}).get("list", [])
        cancelled = 0
        for o in orders:
            reduce_only = o.get("reduceOnly", False)
            status = o.get("orderStatus", "")
            if reduce_only or status in ("Filled", "Cancelled", "Rejected"):
                continue
            # Cancel opening order
            try:
                oid = o.get("orderId", "")
                sym = o.get("symbol", "")
                if oid and sym:
                    body = f"category=linear&symbol={sym}&orderId={oid}"
                    ts2 = str(int(_t.time() * 1000))
                    s2 = _hm.new(as_.encode(), f"{ts2}{ak}5000{body}".encode(), _hs.sha256).hexdigest()
                    _hx.post(f"{_API_BASE}/v5/order/cancel",
                             headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts2,
                                      "X-BAPI-SIGN": s2, "X-BAPI-RECV-WINDOW": "5000",
                                      "Content-Type": "application/x-www-form-urlencoded"},
                             data=body, timeout=5)
                    cancelled += 1
            except Exception:
                pass
        if cancelled:
            logger.warning(f"Deposit guard: cancelled {cancelled} opening order(s)")

    def _clear_critical(self):
        """Reset critical-state tracking when open risk is back within limits."""
        if self._state.get("critical_since") or self._state.get("requires_manual_review"):
            self._state["critical_since"] = None
            self._state["critical_timeout_level"] = 0
            self._state["requires_manual_review"] = False
            self._save_state()
            logger.info("Deposit guard: critical open risk cleared")

    def can_close_position(self) -> tuple[bool, str]:
        """Closing is ALWAYS allowed (never blocked by guard)."""
        return True, "OK"

    def can_reduce_position(self) -> tuple[bool, str]:
        """Reducing risk is ALWAYS allowed."""
        return True, "OK"

    # ─── Telegram alerting (self-contained, cross-process safe) ──
    _last_alert_ts = 0.0

    def _alert(self, text: str):
        """Send a Telegram alert directly via HTTPX (no cross-process notifier
        dependency). Rate-limited: no more than 1 alert per 60s."""
        now = time.time()
        if now - self._last_alert_ts < 60:
            return
        self._last_alert_ts = now
        try:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat:
                logger.warning(f"Deposit guard: no TG creds — event: {text[:120]}")
                return
            import httpx as _hx
            payload = {
                "chat_id": chat,
                "text": f"🛡️ <b>DepositGuard</b>\n{text}",
                "parse_mode": "HTML",
            }
            proxy = os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
            kwargs = {"json": payload, "timeout": 10}
            if proxy:
                kwargs["proxy"] = proxy
            _hx.post(f"https://api.telegram.org/bot{token}/sendMessage", **kwargs)
            logger.info(f"Deposit guard alert sent: {text[:80]}")
        except Exception as e:
            logger.warning(f"Deposit guard alert send failed: {e}")

    # ─── Trade lifecycle hooks ──────────────────────────────────
    def on_position_opened(self, opening_balance: float):
        """Deprecated — day_start_equity now fixed at UTC rollover, not first entry."""
        self._check_day_rollover(opening_balance)

    def on_trade_closed(self, realized_pnl: float, fees: float = 0.0):
        """Accumulate realized PnL + fees for the day (for audit/telegram)."""
        self._sync_from_disk()
        self._check_day_rollover()
        self._state["realized_pnl_day"] = self._state.get("realized_pnl_day", 0.0) + realized_pnl
        self._state["fees_day"] = self._state.get("fees_day", 0.0) + fees

        # Losing streak tracking (AUTO safety guard)
        if realized_pnl < 0:
            self._state["losing_streak"] = self._state.get("losing_streak", 0) + 1
        else:
            self._state["losing_streak"] = 0

        # If streak threshold reached -> pause new entries (AUTO→PAUSE)
        streak = self._state.get("losing_streak", 0)
        if streak >= self.max_losing_streak and not self._state.get("auto_paused"):
            self._state["auto_paused"] = True
            self._state["auto_pause_reason"] = f"LOSING_STREAK:{streak}"
            self._state["blocked_new_entries"] = True
            self._state["block_reason"] = f"auto_pause_losing_streak_{streak}"
            self._alert(f"🛑 <b>AUTO PAUSE: серия убытков</b>\n"
                        f"{streak} убыточных сделок подряд.\n"
                        f"Новые входы заблокированы. Сброс: /unfreeze или ручной.")
            self._log_safety_event("AUTO_PAUSE", "LOSING_STREAK", streak)

        self._save_state()
        logger.info(
            f"Deposit guard: realized day PnL = ${self._state['realized_pnl_day']:.2f} "
            f"(fees ${self._state['fees_day']:.2f}, losing_streak={streak})"
        )

    def on_equity_sample(self, equity: float):
        """Periodic equity sample — updates last_equity/last_unrealized.

        B-fix: proactively sets the daily-loss block MID-DAY, so the guard does
        not depend on the next entry attempt. Existing positions are NEVER
        touched here (no close/reduce/SL-TP changes) — only new entries blocked.
        """
        self._sync_from_disk()
        self._check_day_rollover(equity)
        start = self._state.get("day_start_equity")
        self._state["last_equity"] = equity
        if start:
            self._state["last_unrealized_pnl"] = equity - start
            daily_pnl = equity - start  # negative = loss
            loss = -daily_pnl
            limit = self._daily_limit_usd()
            if loss >= limit:
                if self._state.get("block_reason") != "DAILY_LOSS_LIMIT":
                    self._state["blocked_new_entries"] = True
                    self._state["block_reason"] = "DAILY_LOSS_LIMIT"
                    loss_pct = (loss / start * 100.0) if start > 0 else 0.0
                    limit_pct = self.daily_loss_pct
                    over_usd = max(loss - limit, 0.0)
                    over_pct = (over_usd / start * 100.0) if start > 0 else 0.0
                    import datetime as _dt
                    _now = _dt.datetime.utcnow()
                    _next_midnight = (_now + _dt.timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0)
                    _hours_to_reset = (_next_midnight - _now).total_seconds() / 3600.0
                    self._alert_repeat(
                        f"🚫 <b>DAILY LOSS LIMIT достигнут</b>\n"
                        f"Убыток за день: <b>${loss:.2f} ({loss_pct:.2f}% от старта)</b>\n"
                        f"Лимит: ${limit:.2f} ({limit_pct:.1f}%, старт ${start:.2f})\n"
                        f"Превышение: ${over_usd:.2f} ({over_pct:.2f}%)"
                        f"{'  — лимит превышен' if over_usd > 0 else '  — ровно лимит'}\n"
                        f"⏰ Сброс через ~{_hours_to_reset:.1f} ч (UTC 00:00).\n"
                        f"Новые входы (AUTO и MANUAL) заблокированы.\n"
                        f"Открытые позиции не трогаем (SL/TP без изменений).",
                        self.critical_alert_interval_sec,
                    )
            else:
                # Day recovered above limit — clear only the block WE set here.
                # Entry-time checks (can_open_position) re-evaluate all other reasons.
                if self._state.get("block_reason") == "DAILY_LOSS_LIMIT":
                    self._state["blocked_new_entries"] = False
                    self._state["block_reason"] = "OK"
        self._save_state()

    def on_funding_paid(self, amount: float):
        """Accumulate funding paid/received for the day (accounting completeness).

        Funding PnL is implicitly included in equity-based daily loss checks, but
        the audit split (gross -> fees -> funding -> slippage -> NET) needs it
        explicitly. Wire this hook from wherever position-level funding PnL is
        observed (derivatives collector has rates, not per-position PnL yet).
        """
        self._sync_from_disk()
        self._check_day_rollover()
        self._state["funding_day"] = self._state.get("funding_day", 0.0) + amount
        self._save_state()

    def _trigger_kill(self, reason: str, loss: float):
        """Auto-trigger kill switch (blocks new entries until next UTC day or manual)."""
        if not self._state.get("kill_switch"):
            self._state["kill_switch"] = True
            self._state["kill_reason"] = reason
            self._state["kill_switched_at"] = time.time()
            self._state["blocked_new_entries"] = True
            self._state["block_reason"] = f"kill_switch_{reason}"
            self._save_state()
            logger.error(
                f"🚨 KILL SWITCH TRIGGERED: {reason} loss=${loss:.2f} "
                f"— new entries blocked until UTC reset or manual clear"
            )
            self._alert(f"🚨 <b>KILL SWITCH АКТИВИРОВАН</b>\n"
                        f"Причина: {reason}\n"
                        f"Убыток: ${loss:.2f}\n"
                        f"Новые входы заблокированы до UTC-сброса или ручного сброса.")

    def _log_safety_event(self, event: str, reason: str, value: float):
        """Append a safety event to safety_events.jsonl (AUTO safety guard)."""
        try:
            from pathlib import Path as _P
            sp = _P("/root/tradingos/operations/safety_events.jsonl")
            sp.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "event": event,
                "reason": reason,
                "value": value,
                "time": datetime.now(timezone.utc).isoformat(),
                "action": "BLOCK_NEW_ENTRIES",
                "realized_pnl_day": self._state.get("realized_pnl_day", 0.0),
                "losing_streak": self._state.get("losing_streak", 0),
                "equity": self._state.get("last_equity"),
            }
            with open(sp, "a") as _f:
                _f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def manual_kill(self, enabled: bool):
        """Manual kill switch via config (kill_switch in trading_mode.json)."""
        self._reload_limits()

    def reset(self):
        """Manual reset of guard state (e.g. via admin)."""
        self._state = self._default_state()
        self._save_state()
        logger.info("Deposit guard state manually reset")

    # ─── Status / monitoring ───────────────────────────────────
    def status(self) -> dict:
        self._reload_limits()
        self._sync_from_disk()
        equity = self._get_equity()
        self._check_day_rollover(equity)
        start = self._state.get("day_start_equity")
        daily_pnl = (equity - start) if start else None
        open_risk, missing_sl = self._get_open_risk()
        self._state["open_risk_total"] = open_risk
        self._state["missing_sl_count"] = missing_sl
        self._save_state()
        return {
            "enabled": self.enabled,
            "day": self._state.get("utc_day"),
            "day_start_equity": start,
            "last_equity": equity,
            "available_margin": round(self._get_available_margin(), 2),
            "min_available_margin_usd": self.min_available_margin_usd,
            "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
            "realized_pnl_day": round(self._state.get("realized_pnl_day", 0.0), 2),
            "open_risk_total": round(open_risk, 2),
            "missing_sl_count": missing_sl,
            "daily_loss_limit_usd": round(self._daily_limit_usd(), 2),
            "hard_kill_usd": round(self._hard_kill_usd(), 2),
            "max_open_risk_usd": round(self._max_open_risk_usd(), 2),
            "kill_switch_manual": self.kill_switch,
            "kill_switch_auto": self._state.get("kill_switch", False),
            "kill_reason": self._state.get("kill_reason", ""),
            "blocked_new_entries": self._state.get("blocked_new_entries", False),
            "block_reason": self._state.get("block_reason", ""),
            "can_open": self.can_open_position()[0],
            "block_reason_detail": self.can_open_position()[1],
        }


# Module-level singleton accessor
def get_guard() -> DepositGuard:
    return DepositGuard.instance()
