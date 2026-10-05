#!/usr/bin/env python3
"""
TradingOS Control Center — профессиональный trading terminal.

Доступ: http://localhost:8765  или  http://<IP>:8765

Концепция: STATUS → MONEY → POSITIONS → ORDERS → SIGNALS → ACTIVITY
Плотная информация. Никаких огромных пустых карточек.
Тёмная/светлая тема. Иконки Lucide. Tabular numbers.
"""
from __future__ import annotations

import hashlib
import hmac
import httpx
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")
sys.path.insert(0, "/root/tradingos/diagnostics")

import price_monitor as pm

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
logger = logging.getLogger("trading_control_center")

PORT = int(os.getenv("DASHBOARD_PORT", "8765"))

# State files
MANUAL_STATE = Path("/root/tradingos/operations/manual_state.json")
TRADING_MODE = Path("/root/tradingos/operations/bingx_trading_mode.json")
DEPOSIT_GUARD = Path("/root/tradingos/operations/deposit_guard_state.json")
SESSION_CFG = Path("/root/tradingos/operations/manual_session.json")
ACTIVITY_LOG = Path("/root/tradingos/logs/activity_feed.jsonl")

# ── Activity feed ──────────────────────────────────────────────────────

def _log_activity(event: str, detail: str = "", level: str = "info"):
    try:
        ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail,
            "level": level,
        }
        with open(ACTIVITY_LOG, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _get_activity(limit: int = 30) -> list[dict]:
    try:
        if not ACTIVITY_LOG.exists():
            return []
        lines = ACTIVITY_LOG.read_text().strip().split("\n")[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


# ── Control state ──────────────────────────────────────────────────────

def _get_control_state() -> dict:
    """Читает текущее состояние управления: pause, kill, mode."""
    state = {
        "paused": True,  # fail-safe
        "reason": "",
        "mode": "MANUAL",
        "kill_switch": False,
        "manual_orders_disabled": True,
        "sell_disabled": False,
        "heartbeat_age": None,
    }

    try:
        if MANUAL_STATE.exists():
            d = json.loads(MANUAL_STATE.read_text())
            state["paused"] = bool(d.get("paused", False))
            state["reason"] = d.get("reason", "")
            updated = d.get("updated_at", "")
            if updated:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                state["heartbeat_age"] = int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        pass

    try:
        if TRADING_MODE.exists():
            d = json.loads(TRADING_MODE.read_text())
            state["mode"] = d.get("mode", "MANUAL")
            state["sell_disabled"] = bool(d.get("sell_disabled", False))
    except Exception:
        pass

    try:
        if DEPOSIT_GUARD.exists():
            d = json.loads(DEPOSIT_GUARD.read_text())
            state["kill_switch"] = bool(d.get("kill_switch", False))
    except Exception:
        pass

    try:
        if SESSION_CFG.exists():
            d = json.loads(SESSION_CFG.read_text())
            state["manual_orders_disabled"] = bool(d.get("manual_orders_disabled", False))
    except Exception:
        pass

    # Overall status
    if state["kill_switch"]:
        state["overall"] = "KILL_SWITCH_ACTIVE"
        state["overall_class"] = "critical"
    elif state["paused"]:
        state["overall"] = "PAUSED"
        state["overall_class"] = "warning"
    elif state["mode"] == "AUTO":
        state["overall"] = "ONLINE"
        state["overall_class"] = "ok"
    else:
        state["overall"] = "MANUAL"
        state["overall_class"] = "ok"

    return state


def _set_control(action: str, value: dict = None) -> tuple[bool, str]:
    """Обрабатывает действия контроля: pause/resume/kill/mode."""
    if action == "pause":
        try:
            st = json.loads(MANUAL_STATE.read_text()) if MANUAL_STATE.exists() else {}
            st["paused"] = True
            st["reason"] = "dashboard: manual pause"
            st["updated_at"] = datetime.now(timezone.utc).isoformat()
            st["updated_by"] = "dashboard"
            MANUAL_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))
            _log_activity("Trading paused", "from dashboard", "warning")
            return True, "Trading paused"
        except Exception as e:
            return False, str(e)

    elif action == "resume":
        try:
            st = json.loads(MANUAL_STATE.read_text()) if MANUAL_STATE.exists() else {}
            st["paused"] = False
            st["reason"] = "dashboard: resumed"
            st["updated_at"] = datetime.now(timezone.utc).isoformat()
            st["updated_by"] = "dashboard"
            MANUAL_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))
            _log_activity("Trading resumed", "from dashboard", "ok")
            return True, "Trading resumed"
        except Exception as e:
            return False, str(e)

    elif action == "manual_mode":
        try:
            st = json.loads(TRADING_MODE.read_text())
            st["mode"] = "MANUAL"
            st["updated_at"] = datetime.now(timezone.utc).isoformat()
            st["updated_by"] = "dashboard"
            TRADING_MODE.write_text(json.dumps(st, indent=2, ensure_ascii=False))
            _log_activity("Mode changed to MANUAL", "from dashboard", "warning")
            return True, "Mode = MANUAL"
        except Exception as e:
            return False, str(e)

    elif action == "auto_mode":
        try:
            st = json.loads(TRADING_MODE.read_text())
            st["mode"] = "AUTO"
            st["updated_at"] = datetime.now(timezone.utc).isoformat()
            st["updated_by"] = "dashboard"
            TRADING_MODE.write_text(json.dumps(st, indent=2, ensure_ascii=False))
            _log_activity("Mode changed to AUTO", "from dashboard", "ok")
            return True, "Mode = AUTO"
        except Exception as e:
            return False, str(e)

    return False, "unknown action"


# ── Data collection ────────────────────────────────────────────────────

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 8.0


def _refresh() -> dict:
    global _cache, _cache_ts
    now = time.time()
    if now - _cache_ts < _CACHE_TTL and _cache:
        return _cache

    ak, as_ = pm._load_keys()
    with pm.httpx.Client(timeout=10) as client:
        cache = pm._bulk_tickers(client)

    equity = free = 0.0
    positions: list[dict] = []
    orders: list[dict] = []
    if ak and as_:
        try:
            bal = pm.bingx_balance(ak, as_)
            equity = bal.get("equity", 0)
            free = bal.get("free", 0)
        except Exception:
            pass
        try:
            positions = pm.bingx_positions(ak, as_)
        except Exception:
            pass
        try:
            orders = pm.bingx_open_orders(ak, as_)
        except Exception:
            pass

    # Compute open risk (simple: sum |entry-mark| / entry * notional for losing positions)
    open_risk = 0.0
    for p in positions:
        entry = p.get("entry", 0)
        mark = p.get("mark", 0)
        qty = p.get("qty", 0)
        lev = max(1, p.get("leverage", 1))
        notional = entry * qty
        if entry and mark:
            loss_pct = abs((mark - entry) / entry) if p.get("side") == "LONG" else abs((entry - mark) / entry)
            open_risk += notional * loss_pct / lev

    btc = cache.get("BTCUSDT", {})
    eth = cache.get("ETHUSDT", {})
    eth_alt = cache.get("SOLUSDT", {})

    _cache = {
        "money": {
            "equity": equity,
            "available": free,
            "pnl_unrealised": equity - free if equity > 0 else 0,
            "open_risk": open_risk,
            "margin_used": max(0, equity - free) if equity > 0 else 0,
        },
        "positions": positions,
        "orders": orders,
        "market": [
            {"symbol": "BTCUSDT", "price": btc.get("price", 0), "chg": btc.get("change", 0)},
            {"symbol": "ETHUSDT", "price": eth.get("price", 0), "chg": eth.get("change", 0)},
            {"symbol": "SOLUSDT", "price": eth_alt.get("price", 0), "chg": eth_alt.get("change", 0)},
        ],
        "candidates": pm.CANDIDATES,
        "control": _get_control_state(),
        "activity": _get_activity(30),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }
    _cache_ts = time.time()
    return _cache


def _collect_setups() -> list[dict]:
    """Собираем все проходящие сетапы со сканера."""
    try:
        import httpx as _httpx
        from tradingos.signals.manual_scanner import score_symbol
        with _httpx.Client(timeout=15) as client:
            results = []
            for sym in pm.CANDIDATES:
                try:
                    sig = score_symbol(client, sym, min_score=50)
                    if not sig:
                        continue
                    if sig.get("score", 0) < 65:
                        continue
                    results.append({
                        "symbol": sig["symbol"],
                        "short": sig["symbol"].replace("USDT", ""),
                        "side": sig.get("side", ""),
                        "score": sig.get("score", 0),
                        "rr": sig.get("rr", 0),
                        "price": sig.get("price", 0),
                        "sl": sig.get("sl", 0),
                        "tp": sig.get("final_tp", 0),
                        "rsi": sig.get("rsi", 0),
                        "momentum": sig.get("parts", {}).get("momentum", 0),
                        "vol_ratio": sig.get("vol_ratio", 0),
                        "contour": sig.get("contour", "NO_TRADE"),
                        "setup_type": _setup_type(sig),
                        "squeeze": sig.get("squeeze_on", False),
                        "smc": sig.get("smc_sweep", ""),
                    })
                except Exception:
                    continue
            results.sort(key=lambda x: (-x["score"], -x["rr"]))
            return results[:15]
    except Exception as e:
        logger.warning(f"setups scan error: {e}")
        return []


def _setup_type(sig: dict) -> str:
    chg = 0
    try:
        c = pm.get_24h(sig["symbol"])
        chg = c.get("change", 0)
    except Exception:
        pass
    if chg <= -5:
        return "DIP"
    if chg <= -2:
        return "CORRECTION"
    if chg > 6:
        return "IMPULSE"
    return "ACCUMULATION"


def _get_coin_analysis(sym: str) -> dict | None:
    sym = sym.upper().replace("-", "").replace("/", "")
    if not sym.endswith("USDT"):
        sym += "USDT"

    with pm.httpx.Client(timeout=10) as client:
        cache = pm._bulk_tickers(client)
        t = cache.get(sym, {})
        price = t.get("price", 0)
        if not price:
            return None
        chg = t.get("change", 0)
        vol = t.get("volume", 0)
        atr = pm._get_atr(sym, client)
        atr_pct = atr / price * 100 if price else 0

        from tradingos.signals.manual_scanner import score_symbol
        sig = score_symbol(client, sym, min_score=40)

        r7h = sig.get("range_7d_high", 0) if sig else 0
        r7l = sig.get("range_7d_low", 0) if sig else 0
        r30h = sig.get("range_30d_high", 0) if sig else 0
        r30l = sig.get("range_30d_low", 0) if sig else 0
        dist_7h = (r7h - price) / price * 100 if r7h else 0
        dist_7l = (price - r7l) / price * 100 if r7l else 0

        rsi = sig.get("rsi", 50) if sig else 50
        mom = sig.get("parts", {}).get("momentum", 0) if sig else 0
        squeeze = sig.get("squeeze_on", False) if sig else False
        smc = sig.get("smc_sweep", "") if sig else ""
        if rsi < 35 and mom >= 5:
            forecast, fc_class = "Oversold — potential bounce", "bullish"
        elif rsi > 65 and mom >= 5:
            forecast, fc_class = "Overbought — correction likely", "bearish"
        elif squeeze and mom >= 6:
            forecast, fc_class = "Squeeze — breakout imminent", "squeeze"
        elif smc == "LONG":
            forecast, fc_class = "SMC: liquidity sweep LONG", "bullish"
        elif smc == "SHORT":
            forecast, fc_class = "SMC: liquidity sweep SHORT", "bearish"
        elif r7l and price < r7l * 1.02:
            forecast, fc_class = "Near 7d low — DIP zone", "dip"
        elif r7h and price > r7h * 0.98:
            forecast, fc_class = "Near 7d high — breakout zone", "breakout"
        else:
            forecast, fc_class = "Neutral — waiting", "neutral"

        return {
            "symbol": sym,
            "short": sym.replace("USDT", ""),
            "price": price, "chg": chg, "vol": vol,
            "atr": atr, "atr_pct": round(atr_pct, 2),
            "score": sig.get("score", 0) if sig else 0,
            "side": sig.get("side", "") if sig else "",
            "rr": sig.get("rr", 0) if sig else 0,
            "sl": sig.get("sl", 0) if sig else 0,
            "tp": sig.get("final_tp", 0) if sig else 0,
            "rsi": rsi,
            "mom": mom,
            "vol_ratio": sig.get("vol_ratio", 0) if sig else 0,
            "dist_e20": sig.get("dist_e20_pct", 0) if sig else 0,
            "squeeze": squeeze,
            "smc": smc, "smc_str": sig.get("smc_sweep_strength", "") if sig else "",
            "stoch_k": sig.get("stoch_k", 0) if sig else 0,
            "stoch_d": sig.get("stoch_d", 0) if sig else 0,
            "h1_trend": sig.get("parts", {}).get("h1_trend", 0) if sig else 0,
            "m15_trend": sig.get("parts", {}).get("m15_structure", 0) if sig else 0,
            "mtf_agree": sig.get("mtf_agree") if sig else None,
            "h4_trend": sig.get("h4_trend", "") if sig else "",
            "d1_trend": sig.get("d1_trend", "") if sig else "",
            "dist_to_7h": round(dist_7h, 2),
            "dist_to_7l": round(dist_7l, 2),
            "range_7d": f"{r7l:.4f}–{r7h:.4f}" if r7h and r7l else "N/A",
            "range_30d": f"{r30l:.4f}–{r30h:.4f}" if r30h and r30l else "N/A",
            "tp_reach": sig.get("tp_reachability_pct", 0) if sig else 0,
            "h1_notional": sig.get("h1_notional_med", 0) if sig else 0,
            "forecast": forecast,
            "forecast_class": fc_class,
            "ts": datetime.now().strftime("%H:%M:%S"),
        }


# ══════════════════════════════════════════════════════════════════════
# HTTP SERVER
# ══════════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logging noise

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == '/' or path == '/index.html':
            html = HTML.replace("{CSS}", CSS)
            self._send_html(html)
        elif path == '/api':
            self._send_json(200, _refresh())
        elif path == '/api/setups':
            self._send_json(200, _collect_setups())
        elif path.startswith('/api/coin/'):
            sym = path.split('/')[-1].upper()
            d = _get_coin_analysis(sym)
            if d:
                self._send_json(200, d)
            else:
                self._send_json(404, {"error": f"Coin {sym} not found"})
        elif path == '/api/candidates':
            q = qs.get("q", [""])[0].upper()
            self._send_json(200, [s for s in pm.CANDIDATES if q in s])
        elif path == '/api/activity':
            self._send_json(200, _get_activity(50))
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/control':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            action = body.get('action', '')
            ok, msg = _set_control(action)
            _cache_ts = 0  # force refresh
            self._send_json(200, {"ok": ok, "msg": msg})
        elif path == '/api/action':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            action = body.get('action', '')
            symbol = body.get('symbol', '')
            side = body.get('side', 'LONG')
            result = {"msg": "", "error": ""}
            if action == 'close' and symbol:
                try:
                    import bingx_signal as bx
                    res = bx.bx_action(symbol, side, 'close')
                    result['msg'] = res.get('msg', 'Closed')
                    _log_activity(f"Position closed {symbol}", f"side={side}", "warning")
                except Exception as e:
                    result['error'] = str(e)
            else:
                result['error'] = 'unknown action'
            _cache_ts = 0
            self._send_json(200, result)
        else:
            self._send_json(404, {"error": "Not found"})

    def _send_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


CSS = """
:root{
 --bg:#0b0e14;--bg-elev:#111620;--bg-elev2:#161c27;--bg-hover:#1c2330;
 --border:#1e2636;--border-hover:#2d394f;
 --text:#e5e9f0;--text-2:#94a3b8;--text-3:#64748b;
 --accent:#3b82f6;--accent-dim:rgba(59,130,246,.12);
 --green:#22c55e;--green-dim:rgba(34,197,94,.12);
 --red:#ef4444;--red-dim:rgba(239,68,68,.12);
 --amber:#f59e0b;--amber-dim:rgba(245,158,11,.12);
 --purple:#a78bfa;--purple-dim:rgba(167,139,250,.12);
 --radius:6px;--radius-lg:10px;
 --font:'SF Mono',ui-monospace,'Cascadia Mono','Segoe UI Mono',Menlo,Consolas,monospace;
 --font-ui:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Inter',sans-serif;
}
html.light{
 --bg:#f8f9fb;--bg-elev:#ffffff;--bg-elev2:#f1f3f7;--bg-hover:#e8ecf1;
 --border:#e2e5ea;--border-hover:#cdd2da;
 --text:#111827;--text-2:#4b5563;--text-3:#9ca3af;
 --accent:#2563eb;--accent-dim:rgba(37,99,235,.08);
 --green:#16a34a;--green-dim:rgba(22,163,74,.08);
 --red:#dc2626;--red-dim:rgba(220,38,38,.08);
 --amber:#d97706;--amber-dim:rgba(217,119,6,.08);
 --purple:#7c3aed;--purple-dim:rgba(124,58,237,.08);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);font-family:var(--font-ui);font-size:13px;line-height:1.45;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
code,mono,pre,.mono{font-family:var(--font);font-variant-numeric:tabular-nums}
.num{font-family:var(--font);font-variant-numeric:tabular-nums}

/* ── Top Bar ── */
.topbar{
 display:flex;align-items:center;gap:16px;
 height:44px;padding:0 16px;
 background:var(--bg-elev);border-bottom:1px solid var(--border);
 position:sticky;top:0;z-index:100;
}
.brand{font-weight:700;font-size:14px;color:var(--text);display:flex;align-items:center;gap:8px;letter-spacing:-.02em}
.status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot-ok{background:var(--green);box-shadow:0 0 8px rgba(34,197,94,.4)}
.dot-warn{background:var(--amber);box-shadow:0 0 8px rgba(245,158,11,.4)}
.dot-crit{background:var(--red);box-shadow:0 0 8px rgba(239,68,68,.4);animation:pulse 1.5s infinite}
@keyframes pulse{50%{opacity:.5}}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;letter-spacing:.02em}
.pill-ok{background:var(--green-dim);color:var(--green)}
.pill-warn{background:var(--amber-dim);color:var(--amber)}
.pill-crit{background:var(--red-dim);color:var(--red)}
.pill-info{background:var(--accent-dim);color:var(--accent)}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:12px}
.topbar-time{font-family:var(--font);font-size:12px;color:var(--text-2);font-variant-numeric:tabular-nums}
.theme-toggle{padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:var(--bg-elev2);color:var(--text-2);font-size:11px;cursor:pointer}
.theme-toggle:hover{border-color:var(--border-hover);color:var(--text)}

/* ── Nav ── */
.nav{display:flex;gap:2px;margin-left:16px}
.nav-link{padding:6px 12px;border-radius:4px;color:var(--text-2);font-size:12px;font-weight:500;cursor:pointer;transition:all .1s}
.nav-link:hover{background:var(--bg-hover);color:var(--text)}
.nav-link.active{background:var(--accent-dim);color:var(--accent);font-weight:600}

/* ── Layout ── */
.wrap{max-width:1600px;margin:0 auto;padding:16px}
.grid{display:grid;gap:12px}
.g-2{grid-template-columns:1fr 1fr}
.g-3{grid-template-columns:1fr 1fr 1fr}
.g-4{grid-template-columns:repeat(4,1fr)}
.g-5{grid-template-columns:repeat(5,1fr)}
.g-master-detail{grid-template-columns:2fr 1fr}
@media(max-width:1024px){.g-2,.g-master-detail{grid-template-columns:1fr}.g-3,.g-4,.g-5{grid-template-columns:1fr 1fr}}
@media(max-width:768px){.g-2,.g-3,.g-4,.g-5,.g-master-detail{grid-template-columns:1fr}.nav{display:none}}

/* ── Panels ── */
.panel{background:var(--bg-elev);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden}
.panel-header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border)}
.panel-title{font-size:11px;font-weight:600;color:var(--text-2);text-transform:uppercase;letter-spacing:.08em}
.panel-count{font-family:var(--font);font-size:11px;color:var(--text-3);font-variant-numeric:tabular-nums}
.panel-body{padding:8px 0}
.panel-body.flush{padding:0}

/* ── Control Block ── */
.control-block{
 background:var(--bg-elev);border:1px solid var(--border);border-radius:var(--radius-lg);
 padding:14px 16px;display:flex;align-items:center;gap:20px;flex-wrap:wrap;
}
.control-block.critical{border-color:var(--red);background:linear-gradient(90deg,var(--red-dim),transparent 40%)}
.control-block.warning{border-color:var(--amber);background:linear-gradient(90deg,var(--amber-dim),transparent 40%)}
.control-main{display:flex;align-items:center;gap:10px}
.control-status{font-size:16px;font-weight:700;letter-spacing:-.01em}
.control-sub{font-size:11px;color:var(--text-2);font-family:var(--font)}
.control-actions{display:flex;gap:6px;margin-left:auto}
.btn{
 padding:6px 14px;border-radius:5px;border:1px solid var(--border);background:var(--bg-elev2);
 color:var(--text-2);font-size:12px;font-weight:500;cursor:pointer;transition:all .1s;
 display:inline-flex;align-items:center;gap:5px;font-family:var(--font-ui)
}
.btn:hover{border-color:var(--border-hover);color:var(--text)}
.btn-primary{background:var(--accent-dim);border-color:transparent;color:var(--accent)}
.btn-danger{background:var(--red-dim);border-color:transparent;color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}
.btn-success{background:var(--green-dim);border-color:transparent;color:var(--green)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-sm{padding:4px 10px;font-size:11px}
.btn-icon{padding:5px 8px}
.btn.confirm{background:var(--red);color:#fff;border-color:var(--red)}

/* ── Financial strip ── */
.money-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden}
.money-cell{background:var(--bg-elev);padding:12px 16px}
.money-label{font-size:10px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}
.money-value{font-family:var(--font);font-size:20px;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.money-sub{font-size:10px;color:var(--text-3);font-family:var(--font);font-variant-numeric:tabular-nums;margin-top:2px}
.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:var(--text-2)}.info{color:var(--accent)}
@media(max-width:1024px){.money-strip{grid-template-columns:repeat(3,1fr)}}
@media(max-width:768px){.money-strip{grid-template-columns:repeat(2,1fr)}}

/* ── Table ── */
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{
 text-align:left;padding:6px 14px;font-size:10px;font-weight:600;color:var(--text-3);
 text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border);
 background:var(--bg-elev2);
}
tbody td{padding:8px 14px;border-bottom:1px solid var(--border);font-family:var(--font);font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--bg-hover)}
td .sym{font-weight:600;color:var(--text);font-family:var(--font)}
td .sub{font-size:11px;color:var(--text-3)}
tr.pos-long{border-left:2px solid var(--green)}
tr.pos-short{border-left:2px solid var(--red)}
.action-btn{padding:3px 8px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--text-3);font-size:10px;cursor:pointer;font-family:var(--font-ui)}
.action-btn:hover{border-color:var(--border-hover);color:var(--text)}
.action-btn.close:hover{border-color:var(--red);color:var(--red)}

/* ── Setup card (compact) ── */
.setup-row{display:grid;grid-template-columns:90px 60px 1fr 100px 70px;gap:10px;align-items:center;padding:8px 14px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s}
.setup-row:hover{background:var(--bg-hover)}
.setup-row:last-child{border-bottom:none}
.setup-symbol{font-weight:600;font-size:13px}
.setup-side{font-size:11px;font-weight:600}
.setup-details{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;font-family:var(--font);color:var(--text-2)}
.setup-score{text-align:right;font-family:var(--font);font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.tag-dip{background:var(--accent-dim);color:var(--accent)}
.tag-correction{background:var(--amber-dim);color:var(--amber)}
.tag-impulse{background:var(--red-dim);color:var(--red)}
.tag-accumulation{background:var(--purple-dim);color:var(--purple)}

/* ── Empty state ── */
.empty{padding:20px 14px;text-align:center;color:var(--text-3)}
.empty .icon{font-size:20px;margin-bottom:4px;opacity:.5}
.empty .title{font-size:12px;color:var(--text-2);font-weight:500}
.empty .sub{font-size:11px;color:var(--text-3);margin-top:2px}
.empty.compact{padding:12px;text-align:left}

/* ── Loading/Error ── */
.state{padding:20px 14px;text-align:center;font-size:12px;color:var(--text-3)}
.state.error{color:var(--red)}
.loading-dots::after{content:'';animation:dots 1.4s infinite}
@keyframes dots{0%,20%{content:'.'}40%{content:'..'}60%,100%{content:'...'}}

/* ── Activity feed ── */
.activity-item{display:flex;gap:10px;padding:5px 14px;font-size:11px;border-bottom:1px solid var(--border)}
.activity-item:last-child{border-bottom:none}
.activity-time{font-family:var(--font);color:var(--text-3);font-variant-numeric:tabular-nums;flex-shrink:0;width:60px}
.activity-event{color:var(--text);font-weight:500}
.activity-detail{color:var(--text-3);margin-left:auto;text-align:right}
.activity-item .event-critical{color:var(--red)}
.activity-item .event-warning{color:var(--amber)}
.activity-item .event-ok{color:var(--green)}

/* ── Market row ── */
.market-row{display:flex;justify-content:space-between;align-items:center;padding:6px 14px;border-bottom:1px solid var(--border)}
.market-row:last-child{border-bottom:none}
.market-sym{font-weight:600;font-size:12px}
.market-price{font-family:var(--font);font-variant-numeric:tabular-nums;font-size:13px}
.market-chg{font-family:var(--font);font-size:11px;font-variant-numeric:tabular-nums}

/* ── Search ── */
.search-wrap{position:relative}
.search{
 width:100%;padding:8px 12px;border-radius:6px;border:1px solid var(--border);
 background:var(--bg-elev2);color:var(--text);font-size:13px;outline:none;
 font-family:var(--font)
}
.search:focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-dim)}
.search::placeholder{color:var(--text-3)}
.dropdown{position:absolute;top:100%;left:0;right:0;background:var(--bg-elev);border:1px solid var(--border);border-radius:6px;margin-top:4px;max-height:240px;overflow-y:auto;z-index:200;display:none;box-shadow:0 4px 16px rgba(0,0,0,.2)}
.dropdown.show{display:block}
.dropdown-item{padding:8px 12px;cursor:pointer;display:flex;justify-content:space-between;font-family:var(--font);font-size:12px}
.dropdown-item:hover{background:var(--bg-hover)}

/* ── Analysis ── */
.metric{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}
.metric:last-child{border-bottom:none}
.metric-label{color:var(--text-2)}
.metric-value{font-family:var(--font);font-variant-numeric:tabular-nums;font-weight:600}
.metric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}
.metric-tile{background:var(--bg-elev2);border-radius:6px;padding:10px;text-align:center}
.metric-tile .v{font-family:var(--font);font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.metric-tile .l{font-size:10px;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
.forecast{padding:12px;border-radius:6px;margin-top:10px;border-left:3px solid}
.fc-bullish{background:var(--green-dim);border-color:var(--green)}
.fc-bearish{background:var(--red-dim);border-color:var(--red)}
.fc-neutral{background:var(--bg-elev2);border-color:var(--text-3)}
.fc-squeeze{background:var(--purple-dim);border-color:var(--purple)}
.fc-dip{background:var(--accent-dim);border-color:var(--accent)}
.fc-breakout{background:var(--green-dim);border-color:var(--green)}
.fc-title{font-size:14px;font-weight:600}
.fc-sub{font-size:11px;color:var(--text-2);margin-top:2px;font-family:var(--font)}
.page{display:none}.page.active{display:block}

::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:var(--border-hover)}
"""

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingOS — Control Center</title>
<style>{CSS}</style>
</head>
<body>
<nav class="topbar">
 <div class="brand"><span class="status-dot" id="status-dot"></span>TradingOS</div>
 <div class="nav">
   <div class="nav-link active" data-page="dashboard">Dashboard</div>
   <div class="nav-link" data-page="analysis">Analysis</div>
   <div class="nav-link" data-page="scanner">Scanner</div>
 </div>
 <div class="topbar-right">
   <span class="pill" id="mode-pill">—</span>
   <span class="pill" id="kill-pill">—</span>
   <span class="topbar-time" id="clock">—</span>
   <button class="theme-toggle" id="themeBtn">◐</button>
 </div>
</nav>

<div class="wrap">

<!-- ═══════════ DASHBOARD ═══════════ -->
<div class="page active" id="page-dashboard">

 <!-- Control block -->
 <div class="control-block" id="control-block" style="margin-bottom:12px">
   <div class="control-main">
     <div>
       <div class="control-status" id="control-status">—</div>
       <div class="control-sub" id="control-sub">—</div>
     </div>
   </div>
   <div class="control-actions">
     <button class="btn" id="btn-pause" onclick="controlAction('pause')">Pause</button>
     <button class="btn" id="btn-resume" onclick="controlAction('resume')">Resume</button>
     <button class="btn btn-primary" id="btn-auto" onclick="controlAction('auto_mode')">Set AUTO</button>
     <button class="btn" id="btn-manual" onclick="controlAction('manual_mode')">Set MANUAL</button>
   </div>
 </div>

 <!-- Money strip -->
 <div class="money-strip" style="margin-bottom:12px">
   <div class="money-cell"><div class="money-label">Equity</div><div class="money-value num" id="equity">—</div></div>
   <div class="money-cell"><div class="money-label">Available</div><div class="money-value num" id="available">—</div></div>
   <div class="money-cell"><div class="money-label">PnL Unrealised</div><div class="money-value num" id="pnl">—</div></div>
   <div class="money-cell"><div class="money-label">Open Risk</div><div class="money-value num" id="risk">—</div></div>
   <div class="money-cell"><div class="money-label">Margin Used</div><div class="money-value num" id="margin">—</div></div>
 </div>

 <!-- Positions + Orders -->
 <div class="grid g-master-detail" style="margin-bottom:12px">
   <div class="panel">
     <div class="panel-header"><span class="panel-title">Positions</span><span class="panel-count" id="pos-count">0 open</span></div>
     <div class="panel-body flush" id="pos-table"><div class="state">Loading<span class="loading-dots"></span></div></div>
   </div>
   <div class="panel">
     <div class="panel-header"><span class="panel-title">Open Orders</span><span class="panel-count" id="ord-count">0</span></div>
     <div class="panel-body flush" id="ord-table"><div class="state">Loading<span class="loading-dots"></span></div></div>
   </div>
 </div>

 <!-- Setups + Market -->
 <div class="grid g-master-detail" style="margin-bottom:12px">
   <div class="panel">
     <div class="panel-header"><span class="panel-title">Trading Setups</span><span class="panel-count" id="setup-count">0</span></div>
     <div id="setup-list"><div class="state">Loading<span class="loading-dots"></span></div></div>
   </div>
   <div class="panel">
     <div class="panel-header"><span class="panel-title">Market</span></div>
     <div id="market-list"><div class="state">—</div></div>
   </div>
 </div>

 <!-- Activity -->
 <div class="panel">
   <div class="panel-header"><span class="panel-title">System Activity</span></div>
   <div id="activity-list"><div class="state">—</div></div>
 </div>

</div>

<!-- ═══════════ ANALYSIS ═══════════ -->
<div class="page" id="page-analysis">
 <div class="panel" style="margin-bottom:12px">
   <div class="search-wrap" style="padding:14px">
     <input class="search" id="coinSearch" placeholder="Search coin… (BTC, ETH, APT) — press Enter" autocomplete="off">
     <div class="dropdown" id="coinDropdown"></div>
   </div>
 </div>
 <div id="analysis-result">
   <div class="empty" style="padding:60px 20px">
     <div class="icon">🔬</div>
     <div class="title">Search a coin for full analysis</div>
     <div class="sub">Scanner signal, RSI, ATR, SMC, Stochastic, MTF trend and forecast</div>
   </div>
 </div>
</div>

<!-- ═══════════ SCANNER ═══════════ -->
<div class="page" id="page-scanner">
 <div class="panel">
   <div class="panel-header">
     <span class="panel-title">Scanner — Candidates</span>
     <span style="display:flex;gap:8px;align-items:center">
       <span class="panel-count" id="scan-total">0 scanned</span>
       <span class="pill pill-info" id="scan-pass">0 active</span>
     </span>
   </div>
   <div id="scan-list"><div class="state">Loading<span class="loading-dots"></span></div></div>
 </div>
</div>

</div><!-- /wrap -->

<script>
let theme = localStorage.getItem('theme') || 'dark';
let coinData = null;
let lastCtl = null;
let confirmPending = null;

function applyTheme(t) {
  document.documentElement.className = t === 'light' ? 'light' : '';
  localStorage.setItem('theme', t);
  theme = t;
}

function fmt(n, d=2) {
  if (n == null) return '—';
  n = parseFloat(n);
  if (isNaN(n)) return '—';
  const opts = {minimumFractionDigits:d, maximumFractionDigits:d};
  if (Math.abs(n) >= 1000) opts.minimumFractionDigits = d;
  return n.toLocaleString('en-US', opts);
}

function pct(n, d=2) {
  if (n == null) return '—';
  const v = parseFloat(n);
  return (v>=0?'+':'') + v.toFixed(d) + '%';
}

function chgCls(v) { return v >= 0 ? 'positive' : 'negative'; }

async function refresh() {
  try {
    const [mainRes, setupRes] = await Promise.all([fetch('/api'), fetch('/api/setups')]);
    const d = await mainRes.json();
    const setups = await setupRes.json();
    render(d, setups);
  } catch (e) {
    console.error('refresh failed', e);
  }
}

function render(d, setups) {
  // ── Control state ──
  const ctl = d.control || {};
  lastCtl = ctl;
  const dot = document.getElementById('status-dot');
  const overall = ctl.overall || 'OFFLINE';
  const cls = ctl.overall_class || 'warning';
  dot.className = 'status-dot dot-' + (cls === 'ok' ? 'ok' : cls === 'critical' ? 'crit' : 'warn');

  document.getElementById('control-status').textContent = overall;
  const heartbeatTxt = ctl.heartbeat_age != null ? `heartbeat ${ctl.heartbeat_age}s ago` : 'state file missing';
  const modeTxt = ctl.manual_orders_disabled ? 'orders disabled' : 'orders enabled';
  document.getElementById('control-sub').textContent = `${heartbeatTxt} · ${modeTxt} · sell_${ctl.sell_disabled ? 'off' : 'on'}`;

  const block = document.getElementById('control-block');
  block.className = 'control-block' + (cls === 'critical' ? ' critical' : cls === 'warning' ? ' warning' : '');

  document.getElementById('mode-pill').textContent = ctl.mode || '—';
  document.getElementById('mode-pill').className = 'pill ' + (ctl.mode === 'AUTO' ? 'pill-ok' : 'pill-info');
  document.getElementById('kill-pill').textContent = 'KILL ' + (ctl.kill_switch ? 'ON' : 'OFF');
  document.getElementById('kill-pill').className = 'pill ' + (ctl.kill_switch ? 'pill-crit' : 'pill-ok');

  document.getElementById('btn-pause').disabled = !!ctl.paused;
  document.getElementById('btn-resume').disabled = !ctl.paused;
  document.getElementById('btn-manual').disabled = ctl.mode === 'MANUAL';
  document.getElementById('btn-auto').disabled = ctl.mode === 'AUTO';

  // ── Money ──
  const m = d.money || {};
  document.getElementById('equity').textContent = '$' + fmt(m.equity, 2);
  document.getElementById('available').textContent = '$' + fmt(m.available, 2);
  const pnlEl = document.getElementById('pnl');
  pnlEl.textContent = (m.pnl_unrealised >= 0 ? '+' : '') + '$' + fmt(m.pnl_unrealised, 2);
  pnlEl.className = 'money-value num ' + (m.pnl_unrealised >= 0 ? 'positive' : 'negative');
  document.getElementById('risk').textContent = '$' + fmt(m.open_risk, 2);
  document.getElementById('risk').className = 'money-value num ' + (m.open_risk > 5 ? 'negative' : '');
  document.getElementById('margin').textContent = '$' + fmt(m.margin_used, 2);

  // ── Positions ──
  const pe = document.getElementById('pos-table');
  document.getElementById('pos-count').textContent = `${(d.positions||[]).length} open`;
  if (!d.positions?.length) {
    pe.innerHTML = `<div class="empty compact"><div class="title">0 open positions</div><div class="sub">All clear</div></div>`;
  } else {
    pe.innerHTML = `<table><thead><tr>
      <th>Symbol</th><th>Side</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Liq</th><th></th>
    </tr></thead><tbody>` + d.positions.map((p, i) => {
      const pnl = p.upnl || 0;
      const pnlCls = pnl >= 0 ? 'positive' : 'negative';
      const liqDist = p.liq && p.mark ? Math.abs((p.liq - p.mark)/p.mark*100) : 0;
      const liqCls = liqDist > 50 ? '' : liqDist > 20 ? 'style="color:var(--amber)"' : 'style="color:var(--red)"';
      return `<tr class="pos-${p.side.toLowerCase()}">
        <td><div class="sym">${p.symbol}</div></td>
        <td><span class="pill ${p.side==='LONG'?'pill-ok':'pill-crit'}">${p.side}</span> <span class="sub">x${p.leverage||'-'}</span></td>
        <td>${fmt(p.entry, p.entry>=100?2:4)}</td>
        <td>${fmt(p.mark, p.mark>=100?2:4)}</td>
        <td class="${pnlCls}" style="font-weight:600">${pnl>=0?'+':''}$${fmt(pnl,2)}</td>
        <td ${liqCls}>${liqDist.toFixed(0)}%</td>
        <td><button class="action-btn close" onclick="closePosition('${p.symbol}','${p.side}')">Close</button></td>
      </tr>`;
    }).join('') + '</tbody></table>';
  }

  // ── Orders ──
  const oe = document.getElementById('ord-table');
  document.getElementById('ord-count').textContent = (d.orders||[]).length;
  if (!d.orders?.length) {
    oe.innerHTML = `<div class="empty compact"><div class="title">No active orders</div><div class="sub">Exchange clear</div></div>`;
  } else {
    oe.innerHTML = `<table><thead><tr>
      <th>Symbol</th><th>Type</th><th>Price</th><th>Qty</th>
    </tr></thead><tbody>` + d.orders.map(o => `
      <tr>
        <td><div class="sym">${o.symbol}</div></td>
        <td><span class="pill ${o.side==='BUY'?'pill-ok':'pill-crit'}">${o.side}</span></td>
        <td>${fmt(o.price, o.price>=100?2:4)}</td>
        <td>${fmt(o.qty, o.qty>=1000?0:3)}</td>
      </tr>`).join('') + '</tbody></table>';
  }

  // ── Setups ──
  const se = document.getElementById('setup-list');
  document.getElementById('setup-count').textContent = setups?.length || 0;
  if (!setups?.length) {
    se.innerHTML = `<div class="empty compact"><div class="title">0 active setups</div><div class="sub">Market currently does not meet entry conditions</div></div>`;
  } else {
    se.innerHTML = setups.map(s => `
      <div class="setup-row" onclick="switchPage('analysis');showAnalysis('${s.symbol}')">
        <div class="setup-symbol">${s.short}</div>
        <div><span class="pill ${s.side==='LONG'?'pill-ok':'pill-crit'}">${s.side||'—'}</span> <span class="tag tag-${(s.setup_type||'').toLowerCase()}">${s.setup_type||'SETUP'}</span></div>
        <div class="setup-details">
          <span>Entry <b>${fmt(s.price, s.price>=100?2:4)}</b></span>
          <span>SL ${fmt(s.sl, s.sl>=100?2:4)}</span>
          <span>TP ${fmt(s.tp, s.tp>=100?2:4)}</span>
          ${s.smc ? `<span style="color:var(--accent)">SMC ${s.smc}</span>` : ''}
          ${s.squeeze ? `<span style="color:var(--purple)">⚡ squeezе</span>` : ''}
        </div>
        <div>R:R <b class="positive">${(s.rr||0).toFixed(1)}</b></div>
        <div class="setup-score">
          <div class="metric-value" style="color:${s.score>=80?'var(--green)':s.score>=70?'var(--amber)':'var(--text-2)'}">${s.score}</div>
          <div class="sub" style="font-size:9px">RSI ${s.rsi?.toFixed(0) ?? '—'}</div>
        </div>
      </div>
    `).join('');
  }

  // ── Market ──
  const me = document.getElementById('market-list');
  if (d.market?.length) {
    me.innerHTML = d.market.map(mk => `
      <div class="market-row">
        <span class="market-sym">${mk.symbol.replace('USDT','')}</span>
        <span class="market-price">$${fmt(mk.price, mk.price>=100?2:4)}</span>
        <span class="market-chg ${chgCls(mk.chg)}">${pct(mk.chg)}</span>
      </div>
    `).join('');
  }

  // ── Activity ──
  const ae = document.getElementById('activity-list');
  const acts = d.activity || [];
  if (!acts.length) {
    ae.innerHTML = `<div class="empty compact"><div class="title">No recent activity</div><div class="sub">Actions will appear here as they happen</div></div>`;
  } else {
    ae.innerHTML = acts.slice(0, 10).map(a => {
      const t = new Date(a.ts).toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit'});
      const evCls = a.level === 'critical' ? 'event-critical' : a.level === 'warning' ? 'event-warning' : a.level === 'ok' ? 'event-ok' : '';
      return `<div class="activity-item">
        <span class="activity-time">${t}</span>
        <span class="activity-event ${evCls}">${a.event}</span>
        <span class="activity-detail">${a.detail || ''}</span>
      </div>`;
    }).join('');
  }
}

// ═══ ANALYSIS PAGE ═══
async function showAnalysis(sym) {
  if (!sym) return;
  sym = sym.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (!sym.endsWith('USDT')) sym += 'USDT';
  document.getElementById('coinSearch').value = sym.replace('USDT','');
  const el = document.getElementById('analysis-result');
  el.innerHTML = `<div class="state">Analysing ${sym.replace('USDT','')}<span class="loading-dots"></span></div>`;
  try {
    const res = await fetch('/api/coin/' + sym);
    const d = await res.json();
    if (d.error) {
      el.innerHTML = `<div class="empty" style="padding:40px"><div class="icon">❌</div><div class="title">${d.error}</div></div>`;
      return;
    }
    coinData = d;
    renderAnalysis(d);
  } catch (e) {
    el.innerHTML = `<div class="state error">Connection failed. Retry.</div>`;
  }
}

function renderAnalysis(d) {
  const el = document.getElementById('analysis-result');
  const rsiCls = d.rsi < 30 ? 'positive' : d.rsi > 70 ? 'negative' : '';
  const momCls = d.mom >= 10 ? 'positive' : d.mom >= 6 ? '' : 'negative';
  const momColor = d.mom >= 10 ? 'var(--green)' : d.mom >= 6 ? 'var(--amber)' : 'var(--red)';
  const fcc = d.forecast_class || 'neutral';
  const fcColor = {bullish:'var(--green)',bearish:'var(--red)',neutral:'var(--text-3)',squeeze:'var(--purple)',dip:'var(--accent)',breakout:'var(--green)'};
  const fcIcon = {bullish:'▲',bearish:'▼',neutral:'●',squeeze:'⚡',dip:'◆',breakout:'▲'};

  el.innerHTML = `
  <div class="grid g-2">
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">${d.short} / USDT</span>
        <span style="display:flex;gap:8px;align-items:center">
          <span class="market-feature" style="font-family:var(--font);font-size:20px;font-weight:700;font-variant-numeric:tabular-nums">$${fmt(d.price, d.price>=100?2:4)}</span>
          <span class="pill ${d.chg>=0?'pill-ok':'pill-crit'}">${pct(d.chg)}</span>
        </span>
      </div>
      <div class="panel-body">
        <div class="metric-grid">
          <div class="metric-tile"><div class="v" style="color:${d.rsi<30?'var(--green)':d.rsi>70?'var(--red)':'var(--text)'}">${d.rsi?.toFixed(1)??'—'}</div><div class="l">RSI(14)</div></div>
          <div class="metric-tile"><div class="v" style="color:${momColor}">${d.mom??'—'}</div><div class="l">Momentum</div></div>
          <div class="metric-tile"><div class="v">${d.vol_ratio?.toFixed(2)??'—'}</div><div class="l">Vol ratio</div></div>
          <div class="metric-tile"><div class="v">${d.atr_pct??'—'}%</div><div class="l">ATR 1h</div></div>
          <div class="metric-tile"><div class="v" style="color:${d.squeeze?'var(--purple)':'var(--text-3)'}">${d.squeeze?'ON':'OFF'}</div><div class="l">Squeeze</div></div>
          <div class="metric-tile"><div class="v">${d.stoch_k?.toFixed(0)??'—'}</div><div class="l">Stoch K</div></div>
        </div>
        <div style="padding:0 14px 10px">
          <div class="metric"><span class="metric-label">Obъём 24ч</span><span class="metric-value">$${(d.vol/1e6).toFixed(1)}M</span></div>
          <div class="metric"><span class="metric-label">Dist EMA20</span><span class="metric-value">${pct(d.dist_e20)}</span></div>
          <div class="metric"><span class="metric-label">SMC Signal</span><span class="metric-value" style="color:${d.smc==='LONG'?'var(--green)':d.smc==='SHORT'?'var(--red)':'var(--text-3)'}">${d.smc || '—'}${d.smc_str?` (${d.smc_str})`:''}</span></div>
          <div class="metric"><span class="metric-label">H1 Notional</span><span class="metric-value">$${(d.h1_notional/1000).toFixed(0)}k</span></div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header"><span class="panel-title">Scanner Signal</span><span class="pill ${d.score>=80?'pill-ok':d.score>=70?'pill-info':'pill-gray'}" style="${d.score>=80?'':d.score>=70?'background:var(--accent-dim);color:var(--accent)':''}">score ${d.score||'—'}</span></div>
      <div class="panel-body">
        <div class="metric-grid">
          <div class="metric-tile"><div class="v">${d.side||'—'}</div><div class="l">Side</div></div>
          <div class="metric-tile"><div class="v">${d.rr?.toFixed(2)??'—'}</div><div class="l">R:R</div></div>
          <div class="metric-tile"><div class="v">${d.tp_reach?.toFixed(1)??'—'}%</div><div class="l">TP reach</div></div>
        </div>
        <div style="padding:0 14px 10px">
          <div class="metric"><span class="metric-label">Entry</span><span class="metric-value">$${fmt(d.price)}</span></div>
          <div class="metric"><span class="metric-label">Stop Loss</span><span class="metric-value negative">${d.sl?'$'+fmt(d.sl):'—'}</span></div>
          <div class="metric"><span class="metric-label">Take Profit</span><span class="metric-value positive">${d.tp?'$'+fmt(d.tp):'—'}</span></div>
          <div class="metric"><span class="metric-label">H1 Trend</span><span class="metric-value">${d.h1_trend ?? '—'}/25</span></div>
          <div class="metric"><span class="metric-label">M15 Structure</span><span class="metric-value">${d.m15_trend ?? '—'}/20</span></div>
          <div class="metric"><span class="metric-label">H4 / D1</span><span class="metric-value">${d.h4_trend || '—'} / ${d.d1_trend || '—'}</span></div>
          <div class="metric"><span class="metric-label">MTF agree</span><span class="metric-value">${d.mtf_agree===true?'✅':d.mtf_agree===false?'❌':'—'}</span></div>
          <div class="metric"><span class="metric-label">7d range</span><span class="metric-value">${d.range_7d}</span></div>
          <div class="metric"><span class="metric-label">To 7d high</span><span class="metric-value">${pct(d.dist_to_7h)}</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="forecast fc-${fcc}" style="margin-top:12px">
    <div class="fc-title" style="color:${fcColor[fcc]||'var(--text)'}">${fcIcon[fcc]||'●'} ${d.forecast}</div>
    <div class="fc-sub">
      Score ${d.score} · Momentum ${d.mom}/15 · RSI ${d.rsi?.toFixed(1)} · Vol ratio ${d.vol_ratio?.toFixed(2)} · ATR ${d.atr_pct}% · updated ${d.ts}
    </div>
  </div>

  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="btn btn-ghost" onclick="window.open('https://www.tradingview.com/chart/?symbol=BINANCE:${d.symbol.replace('USDT','')}USDT.P','_blank')">📊 TradingView</button>
    <button class="btn btn-ghost" onclick="showAnalysis('${d.symbol}')">⟳ Refresh</button>
  </div>
  `;
}

// ── Setup search ──
const searchEl = document.getElementById('coinSearch');
const dropEl = document.getElementById('coinDropdown');
searchEl.addEventListener('input', async (e) => {
  const q = e.target.value.toUpperCase().trim();
  if (!q) { dropEl.classList.remove('show'); return; }
  const res = await fetch('/api/candidates?q=' + encodeURIComponent(q));
  const list = await res.json();
  if (!list.length) { dropEl.classList.remove('show'); return; }
  dropEl.innerHTML = list.slice(0, 12).map(c =>
    `<div class="dropdown-item" onclick="selectCoin('${c}')"><span>${c.replace('USDT','')}</span></div>`
  ).join('');
  dropEl.classList.add('show');
});
searchEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const val = searchEl.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (val) { showAnalysis(val); dropEl.classList.remove('show'); }
  }
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-wrap')) dropEl.classList.remove('show');
});
function selectCoin(sym) {
  searchEl.value = sym.replace('USDT','');
  showAnalysis(sym);
  dropEl.classList.remove('show');
}

// ═══ SCANNER PAGE ═══
async function renderScanner(d) {
  const el = document.getElementById('scan-list');
  document.getElementById('scan-total').textContent = `${d.candidates?.length||0} scanned`;
  try {
    const res = await fetch('/api/setups');
    const setups = await res.json();
    document.getElementById('scan-pass').textContent = `${setups?.length||0} active`;
    if (!setups?.length) {
      el.innerHTML = `<div class="empty compact"><div class="title">0 passing signals</div><div class="sub">All candidates filtered out — market flat or no impulse</div></div>`;
      return;
    }
    el.innerHTML = setups.map(s => `
      <div class="setup-row" onclick="switchPage('analysis');showAnalysis('${s.symbol}')">
        <div class="setup-symbol">${s.short}</div>
        <div><span class="pill ${s.side==='LONG'?'pill-ok':'pill-crit'}">${s.side||'—'}</span> <span class="tag tag-${(s.setup_type||'').toLowerCase()}">${s.setup_type||'SETUP'}</span></div>
        <div class="setup-details">
          <span>Price <b>${fmt(s.price, s.price>=100?2:4)}</b></span>
          <span>SL ${fmt(s.sl, s.sl>=100?2:4)}</span>
          <span>TP ${fmt(s.tp, s.tp>=100?2:4)}</span>
          ${s.contour !== 'NO_TRADE' ? `<span class="pill pill-info">${s.contour}</span>` : ''}
          ${s.smc ? `<span style="color:var(--accent)">SMC ${s.smc}</span>` : ''}
        </div>
        <div>R:R <b class="positive">${(s.rr||0).toFixed(1)}</b></div>
        <div class="setup-score">
          <div class="metric-value" style="color:${s.score>=80?'var(--green)':s.score>=70?'var(--amber)':'var(--text-2)'}">${s.score}</div>
          <div class="sub" style="font-size:9px">mom ${s.momentum??'—'}</div>
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<div class="state error">Scanner load failed</div>`;
  }
}

// ── Actions ──
async function controlAction(action) {
  // Confirmation for critical actions
  if ([ 'pause', 'auto_mode', 'manual_mode' ].includes(action) && action !== 'resume') {
    if (!confirm(`Action: ${action}\nAre you sure?`)) return;
  }
  try {
    const r = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action})});
    const d = await r.json();
    alert(d.msg || d.error || 'OK');
    refresh();
  } catch (e) { alert('Error: ' + e.message); }
}

async function closePosition(sym, side) {
  if (!confirm(`Close ${sym} ${side}? This places MARKET order on live account.`)) return;
  try {
    const r = await fetch('/api/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'close', symbol:sym, side})});
    const d = await r.json();
    alert(d.msg || d.error || 'Done');
    refresh();
  } catch (e) { alert('Error: ' + e.message); }
}

// ── Navigation ──
function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector(`.nav-link[data-page="${name}"]`).classList.add('active');
  if (name === 'scanner') renderScanner(lastData || {});
}
let lastData = null;

document.querySelectorAll('.nav-link').forEach(tab => tab.addEventListener('click', () => switchPage(tab.dataset.page)));

document.getElementById('themeBtn').addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark'));

// Clock
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}, 1000);

// Init
applyTheme(theme);
let refreshOk = false;
async function boot() {
  await refresh();
  setInterval(refresh, 8000);
}
boot();
</script>
</body>
</html>"""


def main():
    threading.Thread(target=lambda: [(_refresh(), time.sleep(_CACHE_TTL)) for _ in iter(int, 1)], daemon=True).start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"TradingOS Control Center → http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
