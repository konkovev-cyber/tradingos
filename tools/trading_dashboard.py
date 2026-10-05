#!/usr/bin/env python3
"""
TradingOS Dashboard v2 — современный интерфейс для управления торговой системой.

Доступ: http://localhost:8765  или  http://<IP>:8765
Темы: тёмная (по умолчанию) / светлая
Фичи:
  - Дашборд: баланс, позиции, ордера, сетапы
  - Поиск монеты: ввод символа → детальный анализ + сканер
  - Прогноз: RSI, ATR, SMC, стохастик, мультитаймфрейм
  - Авто-обновление каждые 8 сек
  - Адаптивный мобильный дизайн
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")
sys.path.insert(0, "/root/tradingos/diagnostics")

import price_monitor as pm

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
logger = logging.getLogger("trading_dashboard")

PORT = int(os.getenv("DASHBOARD_PORT", "8765"))

# ── Кэш данных ─────────────────────────────────────────────────────────

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

    # Сетапы из scanner
    setups: list[dict] = []
    try:
        from tradingos.signals.manual_scanner import score_symbol
        for sym in pm.CANDIDATES:
            try:
                sig = score_symbol(client, sym, min_score=55)
                if not sig:
                    continue
                if sig.get("score", 0) < 70:
                    continue
                if sig.get("parts", {}).get("momentum", 0) < 6:
                    continue
                if sig.get("vol_ratio", 0) < 0.8:
                    continue
                card = pm._scanner_to_card(sig, equity, cache)
                if card:
                    card["_sig"] = sig  # оригинальные данные сканера
                    setups.append(card)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"scanner: {e}")

    btc = cache.get("BTCUSDT", {})
    eth = cache.get("ETHUSDT", {})

    _cache = {
        "equity": equity,
        "free": free,
        "unrealised": equity - free,
        "risk": equity * 0.01,
        "positions": positions,
        "orders": orders,
        "setups": setups,
        "btc": {"price": btc.get("price", 0), "chg": btc.get("change", 0)},
        "eth": {"price": eth.get("price", 0), "chg": eth.get("change", 0)},
        "candidates": pm.CANDIDATES,
        "ts": datetime.now().strftime("%H:%M:%S"),
    }
    _cache_ts = time.time()
    return _cache


def _get_coin_analysis(sym: str) -> dict | None:
    """Полный анализ монеты: тикер + сканер + ATR + SMC."""
    sym = sym.upper().replace("-", "").replace("/", "")
    if not sym.endswith("USDT"):
        sym += "USDT"

    with pm.httpx.Client(timeout=10) as client:
        cache = pm._bulk_tickers(client)
        t = cache.get(sym, {})
        price = t.get("price", 0)
        chg = t.get("change", 0)
        vol = t.get("volume", 0)
        if not price:
            return None

        atr = pm._get_atr(sym, client)
        atr_pct = atr / price * 100 if price else 0

        # Scanner signal
        from tradingos.signals.manual_scanner import score_symbol
        sig = score_symbol(client, sym, min_score=40)

        # Bounding range (7d/30d)
        r7h = sig.get("range_7d_high", 0) if sig else 0
        r7l = sig.get("range_7d_low", 0) if sig else 0
        r30h = sig.get("range_30d_high", 0) if sig else 0
        r30l = sig.get("range_30d_low", 0) if sig else 0

        # Distance to bounds
        dist_to_7h = (r7h - price) / price * 100 if r7h else 0
        dist_to_7l = (price - r7l) / price * 100 if r7l else 0

        # Forecast logic
        forecast = "нейтрально"
        forecast_class = "neutral"
        if sig:
            rsi = sig.get("rsi", 50)
            mom = sig.get("parts", {}).get("momentum", 0)
            squeeze = sig.get("squeeze_on", False)
            smc = sig.get("smc_sweep", "")
            if rsi < 35 and mom >= 5:
                forecast = "вероятен рост (перепроданность)"
                forecast_class = "bullish"
            elif rsi > 65 and mom >= 5:
                forecast = "вероятна коррекция (перекупленность)"
                forecast_class = "bearish"
            elif squeeze and mom >= 6:
                forecast = "сжатие → ожидаем импульс"
                forecast_class = "squeeze"
            elif smc == "LONG":
                forecast = "SMC: свип ликвидности LONG"
                forecast_class = "bullish"
            elif smc == "SHORT":
                forecast = "SMC: свип ликвидности SHORT"
                forecast_class = "bearish"
            elif price < r7l * 1.02:
                forecast = "у нижнего края 7d — потенциальный DIP"
                forecast_class = "dip"
            elif price > r7h * 0.98:
                forecast = "у верхнего края 7d — потенциальный рост"
                forecast_class = "breakout"

        return {
            "symbol": sym,
            "short": sym.replace("USDT", ""),
            "price": price,
            "chg": chg,
            "vol": vol,
            "atr": atr,
            "atr_pct": round(atr_pct, 2),
            "sig": sig,
            "score": sig.get("score", 0) if sig else 0,
            "side": sig.get("side", "") if sig else "",
            "rr": sig.get("rr", 0) if sig else 0,
            "sl": sig.get("sl", 0) if sig else 0,
            "tp": sig.get("final_tp", 0) if sig else 0,
            "rsi": sig.get("rsi", 0) if sig else 0,
            "mom": sig.get("parts", {}).get("momentum", 0) if sig else 0,
            "vol_ratio": sig.get("vol_ratio", 0) if sig else 0,
            "dist_e20": sig.get("dist_e20_pct", 0) if sig else 0,
            "squeeze": sig.get("squeeze_on", False) if sig else False,
            "smc": sig.get("smc_sweep", "") if sig else "",
            "smc_str": sig.get("smc_sweep_strength", "") if sig else "",
            "stoch_k": sig.get("stoch_k", 0) if sig else 0,
            "stoch_d": sig.get("stoch_d", 0) if sig else 0,
            "h1_trend": sig.get("parts", {}).get("h1_trend", 0) if sig else 0,
            "m15_trend": sig.get("parts", {}).get("m15_structure", 0) if sig else 0,
            "mtf_agree": sig.get("mtf_agree", None) if sig else None,
            "h4_trend": sig.get("h4_trend", "") if sig else "",
            "d1_trend": sig.get("d1_trend", "") if sig else "",
            "dist_to_7h": round(dist_to_7h, 2),
            "dist_to_7l": round(dist_to_7l, 2),
            "range_7d": f"{r7l:.4f}–{r7h:.4f}" if r7h and r7l else "N/A",
            "range_30d": f"{r30l:.4f}–{r30h:.4f}" if r30h and r30l else "N/A",
            "tp_reachability": sig.get("tp_reachability_pct", 0) if sig else 0,
            "forecast": forecast,
            "forecast_class": forecast_class,
            "ts": datetime.now().strftime("%H:%M:%S"),
        }


# ── HTML Templates ─────────────────────────────────────────────────────

DARK_CSS = """
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#e6edf3;
--text2:#8b949e;--accent:#58a6ff;--green:#3fb85e;--red:#f85149;--yellow:#d29922;
--blue:#38bdf8;--purple:#a371f7;--card-radius:12px;--gap:16px}
.light{--bg:#f0f2f5;--bg2:#fff;--bg3:#f6f8fa;--border:#d0d7de;--text:#1f2328;
--text2:#656d76;--accent:#0969da;--green:#1a7f37;--red:#cf222e;--yellow:#9a6700;
--blue:#0550ae;--purple:#8250df}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5;min-height:100vh}
a{color:var(--accent);text-decoration:none}
.nav{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;display:flex;align-items:center;gap:20px;height:56px;position:sticky;top:0;z-index:100}
.nav-brand{font-size:16px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:8px}
.nav-tabs{display:flex;gap:4px;margin-left:20px}
.nav-tab{padding:8px 16px;border-radius:8px;color:var(--text2);cursor:pointer;font-size:13px;font-weight:500;transition:all .15s}
.nav-tab:hover{background:var(--bg3);color:var(--text)}
.nav-tab.active{background:var(--accent);color:#fff}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:12px}
.theme-btn{width:36px;height:36px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center}
.container{max-width:1200px;margin:0 auto;padding:20px}
.grid{display:grid;gap:var(--gap)}
.grid-2{grid-template-columns:repeat(2,1fr)}
.grid-3{grid-template-columns:repeat(3,1fr)}
.grid-4{grid-template-columns:repeat(4,1fr)}
@media(max-width:768px){.grid-2,.grid-3,.grid-4{grid-template-columns:1fr}.nav-tabs{display:none}}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--card-radius);padding:20px;transition:border-color .15s}
.card:hover{border-color:var(--accent)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.card-title{font-size:13px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em}
.stat{display:flex;flex-direction:column;gap:4px}
.stat-value{font-size:24px;font-weight:700;color:var(--text)}
.stat-label{font-size:12px;color:var(--text2)}
.stat-change{font-size:13px;font-weight:600}
.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:var(--yellow)}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,184,94,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-blue{background:rgba(56,189,248,.15);color:var(--blue)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-purple{background:rgba(163,113,247,.15);color:var(--purple)}
.badge-gray{background:rgba(139,148,158,.15);color:var(--text2)}
.input-group{position:relative}
.search-input{width:100%;padding:12px 16px;border-radius:10px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:14px;outline:none;transition:border-color .15s}
.search-input:focus{border-color:var(--accent)}
.search-input::placeholder{color:var(--text2)}
.dropdown{position:absolute;top:100%;left:0;right:0;background:var(--bg2);border:1px solid var(--border);border-radius:10px;margin-top:4px;max-height:300px;overflow-y:auto;z-index:200;display:none;box-shadow:0 8px 24px rgba(0,0,0,.3)}
.dropdown.show{display:block}
.dropdown-item{padding:10px 16px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:background .1s}
.dropdown-item:hover{background:var(--bg3)}
.dropdown-item .sym{font-weight:600;color:var(--text)}
.dropdown-item .price{color:var(--text2);font-size:12px}
.divider{height:1px;background:var(--border);margin:20px 0}
.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:all .15s;display:inline-flex;align-items:center;gap:6px}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff}
.btn-success{background:var(--green);color:#fff}
.btn-ghost{background:var(--bg3);color:var(--text);border:1px solid var(--border)}
.btn-ghost:hover{border-color:var(--accent)}
.btn-sm{padding:6px 12px;font-size:12px}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:var(--bg3);color:var(--text2)}
.progress-bar{height:6px;border-radius:3px;background:var(--bg3);overflow:hidden}
.progress-fill{height:100%;border-radius:3px;transition:width .3s}
.signal-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--card-radius);padding:16px;margin-bottom:12px;cursor:pointer;transition:all .15s}
.signal-card:hover{border-color:var(--accent);transform:translateY(-1px)}
.signal-card.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.signal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.signal-symbol{font-size:18px;font-weight:700}
.signal-score{font-size:24px;font-weight:700}
.signal-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--text2)}
.analysis-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}
.analysis-item{background:var(--bg3);border-radius:8px;padding:12px;text-align:center}
.analysis-value{font-size:20px;font-weight:700}
.analysis-label{font-size:11px;color:var(--text2);margin-top:4px}
.forecast-box{border-radius:var(--card-radius);padding:16px;margin:16px 0;border-left:4px solid}
.forecast-bullish{background:rgba(63,184,94,.08);border-color:var(--green)}
.forecast-bearish{background:rgba(248,81,73,.08);border-color:var(--red)}
.forecast-neutral{background:rgba(139,148,158,.08);border-color:var(--text2)}
.forecast-squeeze{background:rgba(163,113,247,.08);border-color:var(--purple)}
.forecast-dip{background:rgba(56,189,248,.08);border-color:var(--blue)}
.forecast-text{font-size:15px;font-weight:600;margin-bottom:4px}
.forecast-sub{font-size:12px;color:var(--text2)}
.empty-state{text-align:center;padding:60px 20px;color:var(--text2)}
.empty-state .icon{font-size:48px;margin-bottom:16px}
.empty-state .title{font-size:18px;font-weight:600;color:var(--text);margin-bottom:8px}
.page{display:none}.page.active{display:block}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--text2)}
"""

HTML_INDEX = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingOS</title>
<style>{dark_css}</style>
</head>
<body>
<nav class="nav">
  <div class="nav-brand">📊 TradingOS</div>
  <div class="nav-tabs">
    <div class="nav-tab active" data-page="dashboard">Дашборд</div>
    <div class="nav-tab" data-page="analysis">Анализ</div>
    <div class="nav-tab" data-page="scanner">Сканер</div>
  </div>
  <div class="nav-right">
    <span id="clock" style="color:var(--text2);font-size:13px"></span>
    <button class="theme-btn" id="themeBtn" title="Сменить тему">🌙</button>
  </div>
</nav>

<div class="container">
  <!-- ═══ DASHBOARD ═══ -->
  <div class="page active" id="page-dashboard">
    <div class="grid grid-4" style="margin-bottom:20px">
      <div class="card">
        <div class="stat">
          <span class="stat-label">Equity</span>
          <span class="stat-value" id="equity">—</span>
        </div>
      </div>
      <div class="card">
        <div class="stat">
          <span class="stat-label">Свободно</span>
          <span class="stat-value" id="free">—</span>
        </div>
      </div>
      <div class="card">
        <div class="stat">
          <span class="stat-label">PnL нереал.</span>
          <span class="stat-value" id="pnl">—</span>
        </div>
      </div>
      <div class="card">
        <div class="stat">
          <span class="stat-label">Риск 1%</span>
          <span class="stat-value" id="risk">—</span>
        </div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-bottom:20px">
      <div class="card">
        <div class="card-header"><span class="card-title">🌐 Рынок</span></div>
        <div style="display:flex;gap:24px">
          <div class="stat">
            <span class="stat-label">BTC/USDT</span>
            <span class="stat-value" id="btc-price">—</span>
            <span class="stat-change" id="btc-chg">—</span>
          </div>
          <div class="stat">
            <span class="stat-label">ETH/USDT</span>
            <span class="stat-value" id="eth-price">—</span>
            <span class="stat-change" id="eth-chg">—</span>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">🎯 Сетапы</span><span class="badge badge-blue" id="setup-count">0</span></div>
        <div id="setup-list"><div class="empty-state"><div class="icon">🔍</div><div class="title">Нет сетапов</div><div>Рынок в консолидации</div></div></div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-header"><span class="card-title">📂 Позиции</span><span class="badge badge-gray" id="pos-count">0</span></div>
        <div id="pos-list"><div class="empty-state"><div class="icon">✅</div><div class="title">Нет позиций</div></div></div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">📋 Ордера</span><span class="badge badge-gray" id="ord-count">0</span></div>
        <div id="ord-list"><div class="empty-state"><div class="icon">📭</div><div class="title">Нет ордеров</div></div></div>
      </div>
    </div>
  </div>

  <!-- ═══ ANALYSIS ═══ -->
  <div class="page" id="page-analysis">
    <div class="card" style="margin-bottom:20px">
      <div class="card-header"><span class="card-title">🔬 Анализ монеты</span></div>
      <div class="input-group">
        <input class="search-input" id="coinSearch" placeholder="Введите символ (например BTC, ETH, APT)..." autocomplete="off">
        <div class="dropdown" id="coinDropdown"></div>
      </div>
    </div>
    <div id="analysis-result">
      <div class="empty-state"><div class="icon">🔬</div><div class="title">Выберите монету для анализа</div><div style="margin-top:8px;font-size:13px">Введите символ или выберите из списка CANDIDATES</div></div>
    </div>
  </div>

  <!-- ═══ SCANNER ═══ -->
  <div class="page" id="page-scanner">
    <div class="card" style="margin-bottom:20px">
      <div class="card-header"><span class="card-title">📡 Сканирование рынка</span><span class="badge badge-blue" id="scan-count">0</span></div>
      <div style="font-size:13px;color:var(--text2);margin-bottom:12px">
        Кандидаты: <span id="scan-cands">0</span> &nbsp;|&nbsp;
        Прошло фильтров: <span id="scan-pass" style="color:var(--green)">0</span> &nbsp;|&nbsp;
       Score ≥ 70 &nbsp;|&nbsp; Momentum ≥ 6
      </div>
      <div id="scan-list"></div>
    </div>
  </div>
</div>

<script>
const CSS = `{dark_css}`;
let currentTheme = localStorage.getItem('theme') || 'dark';
let coinData = null;
let allCandidates = [];

function applyTheme(theme) {
  document.documentElement.className = theme === 'light' ? 'light' : '';
  document.getElementById('themeBtn').textContent = theme === 'light' ? '☀️' : '🌙';
  localStorage.setItem('theme', theme);
  currentTheme = theme;
}

function fmt(n, d=2) {
  if (n == null) return '—';
  n = parseFloat(n);
  if (isNaN(n)) return '—';
  if (Math.abs(n) >= 1000) return n.toLocaleString('en-US', {maximumFractionDigits: d});
  if (Math.abs(n) >= 1) return n.toFixed(d);
  if (Math.abs(n) >= 0.01) return n.toFixed(4);
  return n.toFixed(6);
}

function chgBadge(v) {
  const cls = v >= 0 ? 'badge-green' : 'badge-red';
  const icon = v >= 0 ? '▲' : '▼';
  return `<span class="badge ${cls}">${icon} ${v>=0?'+':''}${v.toFixed(2)}%</span>`;
}

function renderDashboard(d) {
  document.getElementById('equity').textContent = '$' + fmt(d.equity, 2);
  document.getElementById('free').textContent = '$' + fmt(d.free, 2);
  const pnlEl = document.getElementById('pnl');
  pnlEl.textContent = (d.unrealised >= 0 ? '+' : '') + '$' + fmt(d.unrealised, 2);
  pnlEl.className = 'stat-value ' + (d.unrealised >= 0 ? 'positive' : 'negative');
  document.getElementById('risk').textContent = '$' + fmt(d.risk, 2);

  document.getElementById('btc-price').textContent = '$' + fmt(d.btc.price, 2);
  document.getElementById('btc-chg').innerHTML = chgBadge(d.btc.chg);
  document.getElementById('eth-price').textContent = '$' + fmt(d.eth.price, 2);
  document.getElementById('eth-chg').innerHTML = chgBadge(d.eth.chg);

  // Positions
  const pe = document.getElementById('pos-list');
  document.getElementById('pos-count').textContent = d.positions.length;
  if (!d.positions.length) {
    pe.innerHTML = '<div class="empty-state"><div class="icon">✅</div><div class="title">Нет позиций</div></div>';
  } else {
    pe.innerHTML = d.positions.map(p => {
      const pnl = p.upnl;
      const pnlPct = p.entry ? ((p.mark - p.entry)/p.entry*100) : 0;
      const liqDist = p.liq && p.mark ? Math.abs((p.liq - p.mark)/p.mark*100) : 0;
      const liqColor = liqDist > 50 ? 'green' : liqDist > 20 ? 'yellow' : 'red';
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border)">
        <div><b>${p.symbol}</b> <span class="badge badge-${p.side==='LONG'?'green':'red'}">${p.side}</span> x${p.leverage}</div>
        <div style="text-align:right">
          <div class="${pnl>=0?'positive':'negative'}" style="font-weight:700">${pnl>=0?'+':''}$${fmt(pnl)}</div>
          <div style="font-size:11px;color:var(--text2)">${fmt(p.entry)}→${fmt(p.mark)} · ликв ${liqDist.toFixed(0)}%</div>
        </div>
      </div>`;
    }).join('');
  }

  // Orders
  const oe = document.getElementById('ord-list');
  document.getElementById('ord-count').textContent = d.orders.length;
  if (!d.orders.length) {
    oe.innerHTML = '<div class="empty-state"><div class="icon">📭</div><div class="title">Нет ордеров</div></div>';
  } else {
    oe.innerHTML = d.orders.map(o => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border)">
        <div><b>${o.symbol}</b> <span class="badge badge-blue">${o.side}</span> <code>${fmt(o.price)}</code></div>
        <div style="font-size:12px;color:var(--text2)">qty ${fmt(o.qty)}</div>
      </div>
    `).join('');
  }

  // Setups
  const se = document.getElementById('setup-list');
  document.getElementById('setup-count').textContent = d.setups.length;
  if (!d.setups.length) {
    se.innerHTML = '<div class="empty-state"><div class="icon">🔍</div><div class="title">Нет сетапов</div><div style="font-size:13px;margin-top:4px">Рынок в консолидации — хороший сигнал требует импульса (momentum ≥ 8)</div></div>';
  } else {
    se.innerHTML = d.setups.map(s => `
      <div class="signal-card" onclick="showAnalysis('${s.symbol}')">
        <div class="signal-header">
          <span class="signal-symbol">${s.side==='LONG'?'🟢':'🔴'} ${s.short}</span>
          <span class="signal-score" style="color:var(--green)">${s.rrr}:1</span>
        </div>
        <div class="signal-meta">
          <span>Вход: <code>${fmt(s.entry_low)}–${fmt(s.entry_high)}</code></span>
          <span>TP: ${fmt(s.tp)} (+${s.tp_pct}%)</span>
          <span>SL: ${fmt(s.sl)} (-${s.sl_pct}%)</span>
        </div>
        <div class="signal-meta" style="margin-top:6px">
          <span>${s.desc}</span>
          <span>24ч ${s.chg>=0?'+':''}${s.chg}%</span>
          <span>💡 ${s.smc_hint || ''}</span>
        </div>
      </div>
    `).join('');
  }
}

function showAnalysis(sym) {
  switchPage('analysis');
  document.getElementById('coinSearch').value = sym.replace('USDT','');
  doSearch(sym);
}

function renderAnalysis(d) {
  const el = document.getElementById('analysis-result');
  const rsiColor = d.rsi < 30 ? 'var(--green)' : d.rsi > 70 ? 'var(--red)' : 'var(--yellow)';
  const momColor = d.mom >= 10 ? 'var(--green)' : d.mom >= 6 ? 'var(--yellow)' : 'var(--red)';
  const forecastClass = d.forecast_class || 'neutral';
  const fcColors = {bullish:'var(--green)',bearish:'var(--red)',neutral:'var(--text2)',squeeze:'var(--purple)',dip:'var(--blue)',breakout:'var(--green)'};

  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-header">
          <span class="card-title">${d.short}/USDT</span>
          ${chgBadge(d.chg)}
        </div>
        <div class="stat-value" style="font-size:32px;margin:8px 0">$${fmt(d.price)}</div>
        <div style="font-size:13px;color:var(--text2)">
          Объём 24ч: $${(d.vol/1e6).toFixed(1)}M &nbsp;|&nbsp;
          ATR: ${fmt(d.atr)} (${d.atr_pct}%)
        </div>
        <div class="divider"></div>
        <div class="analysis-grid">
          <div class="analysis-item">
            <div class="analysis-value" style="color:${rsiColor}">${d.rsi.toFixed(1)}</div>
            <div class="analysis-label">RSI(14)</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value" style="color:${momColor}">${d.mom}</div>
            <div class="analysis-label">Momentum</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value">${d.vol_ratio.toFixed(2)}</div>
            <div class="analysis-label">Vol Ratio</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value">${d.dist_e20.toFixed(2)}%</div>
            <div class="analysis-label">Dist EMA20</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value" style="color:${d.squeeze?'var(--purple)':'var(--text2)'}">${d.squeeze?'🔥':'—'}</div>
            <div class="analysis-label">Squeeze</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value">${d.stoch_k.toFixed(0)}</div>
            <div class="analysis-label">Stoch K</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">📊 Сигнал сканера</span></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="analysis-item">
            <div class="analysis-value" style="color:var(--accent)">${d.score}</div>
            <div class="analysis-label">Score</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value">${d.rr.toFixed(2)}:1</div>
            <div class="analysis-label">R:R</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value" style="font-size:14px">${d.side || '—'}</div>
            <div class="analysis-label">Direction</div>
          </div>
          <div class="analysis-item">
            <div class="analysis-value" style="font-size:14px">${d.mtf_agree===true?'✅':d.mtf_agree===false?'❌':'—'}</div>
            <div class="analysis-label">MTF Agree</div>
          </div>
        </div>
        <div class="divider"></div>
        <div style="font-size:13px;color:var(--text2);line-height:1.8">
          <div>H1 тренд: <b>${d.h1_trend}/25</b> &nbsp;|&nbsp; M15: <b>${d.m15_trend}/20</b></div>
          <div>H4: <b>${d.h4_trend || '—'}</b> &nbsp;|&nbsp; D1: <b>${d.d1_trend || '—'}</b></div>
          <div>Стох: K=<b>${d.stoch_k.toFixed(0)}</b> D=<b>${d.stoch_d.toFixed(0)}</b></div>
          ${d.smc ? `<div>SMC: <b>${d.smc}${d.smc_str ? ' ('+d.smc_str+')' : ''}</b></div>` : ''}
          <div>Доступность TP: <b>${d.tp_reachability.toFixed(1)}%</b></div>
        </div>
        <div class="divider"></div>
        <div style="font-size:13px;color:var(--text2)">
          <div>Диапазон 7d: <code>${d.range_7d}</code></div>
          <div>Диапазон 30d: <code>${d.range_30d}</code></div>
          <div>До верха 7d: <b style="color:${d.dist_to_7h<5?'var(--green)':'var(--text2)'}">${d.dist_to_7h>=0?'+':''}${d.dist_to_7h.toFixed(1)}%</b></div>
          <div>До низа 7d: <b style="color:${d.dist_to_7l<5?'var(--green)':'var(--text2)'}">${d.dist_to_7l>=0?'+':''}${d.dist_to_7l.toFixed(1)}%</b></div>
        </div>
      </div>
    </div>

    <div class="forecast-box forecast-${forecastClass}" style="margin-top:16px">
      <div class="forecast-text" style="color:${fcColors[forecastClass]||'var(--text)'}">
        ${d.forecast_class==='bullish'?'🟢':d.forecast_class==='bearish'?'🔴':d.forecast_class==='squeeze'?'⚡':d.forecast_class==='dip'?'💎':'⚪'} ${d.forecast}
      </div>
      <div class="forecast-sub">Обновлено: ${d.ts} &nbsp;|&nbsp; Score: ${d.score} &nbsp;|&nbsp; Momentum: ${d.mom}/15 &nbsp;|&nbsp; RSI: ${d.rsi.toFixed(1)}</div>
    </div>

    <div style="margin-top:16px;display:flex;gap:8px">
      <button class="btn btn-ghost btn-sm" onclick="switchPage('dashboard')">← Назад</button>
      <button class="btn btn-primary btn-sm" onclick="doSearch('${d.symbol}')">🔄 Обновить</button>
    </div>
  `;
}

async function doSearch(sym) {
  if (!sym) return;
  sym = sym.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (!sym.endsWith('USDT')) sym += 'USDT';
  const res = await fetch('/api/coin/' + sym);
  const d = await res.json();
  if (d.error) {
    document.getElementById('analysis-result').innerHTML = `<div class="empty-state"><div class="icon">❌</div><div class="title">${d.error}</div></div>`;
  } else {
    coinData = d;
    renderAnalysis(d);
  }
}

function renderScanner(data) {
  const el = document.getElementById('scan-list');
  document.getElementById('scan-cands').textContent = data.candidates?.length || 0;

  // Run quick scan
  fetch('/api/scan').then(r => r.json()).then(sigs => {
    document.getElementById('scan-pass').textContent = sigs.length;
    if (!sigs.length) {
      el.innerHTML = '<div class="empty-state"><div class="icon">📡</div><div class="title">Нет сигналов</div><div style="font-size:13px;margin-top:4px">Все монеты прошли фильтрацию — рынок flat</div></div>';
      return;
    }
    el.innerHTML = sigs.map(s => `
      <div class="signal-card" onclick="showAnalysis('${s.symbol}')">
        <div class="signal-header">
          <div>
            <span class="signal-symbol">${s.side==='LONG'?'🟢':'🔴'} ${s.short}</span>
            <span class="badge badge-${s.score>=80?'green':s.score>=70?'blue':'yellow'}" style="margin-left:8px">score ${s.score}</span>
          </div>
          <div style="text-align:right">
            <div class="signal-score" style="color:var(--green)">${s.rr}:1</div>
            <div style="font-size:11px;color:var(--text2)">RSI ${s.rsi?.toFixed(1) ?? '—'} &nbsp; mom ${s.momentum ?? '—'}</div>
          </div>
        </div>
        <div class="signal-meta">
          <span>Цена: <code>${fmt(s.price)}</code></span>
          <span>ATR: ${s.atr_pct ?? 0}%</span>
          <span>Vol: ${s.vol_ratio ?? 0}</span>
          <span>${s.smc ? 'SMC: '+s.smc : ''}</span>
        </div>
      </div>
    `).join('');
  });
}

// Search dropdown
function setupSearch() {
  const input = document.getElementById('coinSearch');
  const dropdown = document.getElementById('coinDropdown');

  fetch('/api').then(r => r.json()).then(d => {
    allCandidates = d.candidates || [];
  });

  input.addEventListener('input', async (e) => {
    const q = e.target.value.toUpperCase().trim();
    if (!q) { dropdown.classList.remove('show'); return; }
    const res = await fetch('/api/candidates?q=' + encodeURIComponent(q));
    const list = await res.json();
    if (!list.length) { dropdown.classList.remove('show'); return; }
    dropdown.innerHTML = list.slice(0, 15).map(c => `
      <div class="dropdown-item" onclick="selectCoin('${c}')">
        <span class="sym">${c}</span>
        <span class="price">нажмите для анализа</span>
      </div>
    `).join('');
    dropdown.classList.add('show');
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const val = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
      if (val) {
        if (!val.endsWith('USDT')) val += 'USDT';
        doSearch(val);
        dropdown.classList.remove('show');
      }
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.input-group')) dropdown.classList.remove('show');
  });
}

function selectCoin(sym) {
  document.getElementById('coinSearch').value = sym.replace('USDT', '');
  doSearch(sym);
  document.getElementById('coinDropdown').classList.remove('show');
}

// Navigation
function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector(`.nav-tab[data-page="${name}"]`).classList.add('active');
}

document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => switchPage(tab.dataset.page));
});

document.getElementById('themeBtn').addEventListener('click', () => {
  applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
});

// Clock
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit'});
}
setInterval(updateClock, 1000);
updateClock();

// Init
applyTheme(currentTheme);
setupSearch();

let refreshTimer;
async function refresh() {
  try {
    const r = await fetch('/api');
    const d = await r.json();
    renderDashboard(d);
    if (document.getElementById('page-scanner').classList.contains('active')) {
      renderScanner(d);
    }
    if (coinData) {
      const cr = await fetch('/api/coin/' + coinData.symbol);
      const cd = await cr.json();
      if (!cd.error) { coinData = cd; renderAnalysis(cd); }
    }
  } catch(e) { console.error(e); }
}

refresh();
refreshTimer = setInterval(refresh, 8000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == '/' or path == '/index.html':
            html = HTML_INDEX.replace("{dark_css}", DARK_CSS)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())

        elif path == '/api':
            data = _refresh()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        elif path.startswith('/api/coin/'):
            sym = path.split('/')[-1].upper()
            data = _get_coin_analysis(sym)
            if data:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            else:
                self.send_json(404, {"error": f"Coin {sym} not found"})

        elif path == '/api/scan':
            try:
                from tradingos.signals.manual_scanner import score_symbol
                import httpx
                with httpx.Client(timeout=15) as c:
                    results = []
                    for sym in pm.CANDIDATES:
                        try:
                            sig = score_symbol(c, sym, min_score=55)
                            if not sig:
                                continue
                            if sig.get("score", 0) < 70:
                                continue
                            if sig.get("parts", {}).get("momentum", 0) < 6:
                                continue
                            results.append({
                                "symbol": sig["symbol"],
                                "short": sig["symbol"].replace("USDT", ""),
                                "side": sig.get("side", ""),
                                "score": sig.get("score", 0),
                                "rr": sig.get("rr", 0),
                                "price": sig.get("price", 0),
                                "rsi": sig.get("rsi", 0),
                                "momentum": sig.get("parts", {}).get("momentum", 0),
                                "vol_ratio": sig.get("vol_ratio", 0),
                                "atr_pct": round(sig.get("atr", 0) / sig.get("price", 1) * 100, 2) if sig.get("price") else 0,
                                "smc": sig.get("smc_sweep", ""),
                                "squeeze": sig.get("squeeze_on", False),
                            })
                        except Exception:
                            continue
                results.sort(key=lambda x: -x["score"])
                self.send_json(200, results)
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif path == '/api/candidates':
            q = qs.get("q", [""])[0].upper()
            candidates = [s for s in pm.CANDIDATES if q in s]
            self.send_json(200, candidates)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/action':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            action = body.get('action', '')
            symbol = body.get('symbol', '')
            result = {'msg': '', 'error': ''}
            if action == 'close' and symbol:
                try:
                    import bingx_signal as bx
                    res = bx.bx_action(symbol, 'LONG', 'close')
                    result['msg'] = res.get('msg', 'OK')
                except Exception as e:
                    result['error'] = str(e)
            elif action == 'cancel' and symbol:
                result['msg'] = f'Отмена {symbol} — вручную через BingX'
            else:
                result['error'] = 'unknown action'
            self.send_json(200, result)
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


def _start_refresh_loop():
    while True:
        _refresh()
        time.sleep(_CACHE_TTL)


def main():
    threading.Thread(target=_start_refresh_loop, daemon=True).start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    print(f"\n{'='*50}")
    print(f"  📊 TradingOS Dashboard v2")
    print(f"  Local:  http://localhost:{PORT}")
    print(f"  Local:  http://{ip}:{PORT}")
    print(f"{'='*50}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
