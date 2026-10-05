#!/usr/bin/env python3
"""
Trading Dashboard — простой веб-интерфейс для TradingOS.
Открывает доступ к системе без AI, прямо в браузере.

Авто: http://localhost:8765
Публично: http://<IP>:8765

Фичи:
  - Баланс, позиции, ордера в реальном времени
  - Сетапы из TradingOS scanner (только MARKET/LIMIT)
  - Кнопки: закрыть позицию, отменить ордер
  - Авто-обновление каждые 10 сек
  - Тёмная тема, адаптирован под телефон
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("trading_dashboard")

PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
STATE_FILE = Path("/root/tradingos/operations/dashboard_state.json")

# ── Данные ─────────────────────────────────────────────────────────────

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 8.0  # сек


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
        scanner_sigs = pm._scan_for_monitor()
        for sig in scanner_sigs:
            card = pm._scanner_to_card(sig, equity, cache)
            if card:
                setups.append(card)
    except Exception as e:
        logger.warning(f"scanner error: {e}")

    btc = cache.get("BTCUSDT", {})
    eth = cache.get("ETHUSDT", {})

    _cache = {
        "equity": equity,
        "free": free,
        "unrealised": equity - free,
        "positions": positions,
        "orders": orders,
        "setups": setups,
        "btc_price": btc.get("price", 0),
        "btc_chg": btc.get("change", 0),
        "eth_price": eth.get("price", 0),
        "eth_chg": eth.get("change", 0),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }
    _cache_ts = time.time()
    return _cache


def _start_refresh_loop():
    while True:
        _refresh()
        time.sleep(_CACHE_TTL)


# ── HTTP Handler ───────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingOS</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px}
a{color:#58a6ff;text-decoration:none}
h1{font-size:18px;padding:16px 16px 0;color:#f0f6fc}
.subtitle{padding:4px 16px 12px;color:#8b949e;font-size:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;margin:0 12px 12px;padding:12px 16px}
.card h2{font-size:14px;color:#8b949e;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}
.row{display:flex;justify-content:space-between;align-items:center;padding:4px 0}
.row b{color:#f0f6fc}
.pos{border-left:3px solid #3fb85e;margin:6px 0;padding:8px 12px;background:#0d1117;border-radius:4px}
.pos.long{border-left-color:#3fb85e}
.pos.short{border-left-color:#f85149}
.pos .sym{font-weight:600;font-size:15px}
.pos .pnl{font-size:16px;font-weight:700}
.pnl.green{color:#3fb85e}.pnl.red{color:#f85149}
.btn{display:inline-block;padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-weight:600;margin:2px}
.btn-green{background:#238636;color:#fff}.btn-red{background:#da3633;color:#fff}
.btn-blue{background:#1f6feb;color:#fff}.btn-gray{background:#30363d;color:#c9d1d9}
.btn:hover{opacity:.85}
.setup{border-left:3px solid #38bdf8;margin:8px 0;padding:10px 12px;background:#0d1117;border-radius:4px}
.setup .sym{font-weight:700;font-size:15px}
.setup .price{font-family:monospace;color:#8b949e;font-size:13px}
.setup .rr{color:#3fb85e;font-weight:600}
.tick{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600}
.tick-green{background:rgba(63,184,94,.15);color:#3fb85e}
.tick-red{background:rgba(248,81,73,.15);color:#f85149}
.tick-yellow{background:rgba(187,128,9,.15);color:#d29922}
.upd{text-align:center;padding:8px;color:#484f58;font-size:11px}
.loading{text-align:center;padding:40px;color:#8b949e}
.order-item{padding:6px 0;border-bottom:1px solid #21262d;font-size:13px}
.order-item:last-child{border:none}
.badge{display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}
.badge-market{background:rgba(63,184,94,.2);color:#3fb85e}
.badge-limit{background:rgba(56,189,248,.2);color:#38bdf8}
.badge-no-trade{background:rgba(139,148,158,.2);color:#8b949e}
</style>
</head>
<body>
<h1>📊 TradingOS</h1>
<div class="subtitle" id="ts">загрузка...</div>

<div class="card">
  <h2>💰 Баланс</h2>
  <div class="row"><span>Equity</span><b id="equity">—</b></div>
  <div class="row"><span>Свободно</span><b id="free">—</b></div>
  <div class="row"><span>Нереал. PnL</span><b id="unrealised">—</b></div>
  <div class="row"><span>Риск 1%</span><b id="risk">—</b></div>
</div>

<div class="card">
  <h2>🌐 Рынок</h2>
  <div class="row"><span>BTC</span><b id="btc-price">—</b> <span id="btc-chg"></span></div>
  <div class="row"><span>ETH</span><b id="eth-price">—</b> <span id="eth-chg"></span></div>
</div>

<div class="card">
  <h2>📂 Позиции <span id="pos-count"></span></h2>
  <div id="positions"><div class="loading">нет позиций</div></div>
</div>

<div class="card">
  <h2>📋 Ордера <span id="ord-count"></span></h2>
  <div id="orders"><div class="loading">нет ордеров</div></div>
</div>

<div class="card">
  <h2>🎯 Сетапы <span id="setup-count"></span></h2>
  <div id="setups"><div class="loading">нет сетапов</div></div>
</div>

<div class="upd" id="footer">обновляется автоматически</div>

<script>
async function fetchData(){
  try{
    const r=await fetch('/api');
    const d=await r.json();
    document.getElementById('ts').textContent='🕐 '+d.ts;
    document.getElementById('equity').textContent='$'+d.equity.toFixed(2);
    document.getElementById('free').textContent='$'+d.free.toFixed(2);
    const up=document.getElementById('unrealised');
    up.textContent=(d.unrealised>=0?'+':'')+d.unrealised.toFixed(2)+'$';
    up.style.color=d.unrealised>=0?'#3fb85e':'#f85149';
    document.getElementById('risk').textContent='$'+(d.equity*0.01).toFixed(2);

    // BTC
    document.getElementById('btc-price').textContent='$'+d.btc_price.toLocaleString();
    const btcChg=document.getElementById('btc-chg');
    btcChg.textContent=(d.btc_chg>=0?'+':'')+d.btc_chg.toFixed(2)+'%';
    btcChg.className=d.btc_chg>=0?'tick tick-green':'tick tick-red';

    // ETH
    document.getElementById('eth-price').textContent='$'+d.eth_price.toLocaleString();
    const ethChg=document.getElementById('eth-chg');
    ethChg.textContent=(d.eth_chg>=0?'+':'')+d.eth_chg.toFixed(2)+'%';
    ethChg.className=d.eth_chg>=0?'tick tick-green':'tick tick-red';

    // Positions
    const posEl=document.getElementById('positions');
    document.getElementById('pos-count').textContent=d.positions.length?`(${d.positions.length})`:'';
    if(!d.positions.length){posEl.innerHTML='<div class="loading">нет открытых позиций</div>';}
    else{posEl.innerHTML=d.positions.map(p=>`
      <div class="pos ${p.side.toLowerCase()}">
        <div class="row"><span class="sym">${p.symbol}</span><span class="pnl ${p.upnl>=0?'green':'red'}">${p.upnl>=0?'+':''}${p.upnl.toFixed(2)}$ (${((p.mark-p.entry)/p.entry*100).toFixed(1)}%)</span></div>
        <div class="price">вх ${p.entry} → марк ${p.mark} · x${p.leverage} · ликв ${p.liq}%</div>
        ${p.liq>0?`<button class="btn btn-red" onclick="closePos('${p.symbol}','${p.side}')">❌ Закрыть</button>`:''}
      </div>`).join('');}

    // Orders
    const ordEl=document.getElementById('orders');
    document.getElementById('ord-count').textContent=d.orders.length?`(${d.orders.length})`:'';
    if(!d.orders.length){ordEl.innerHTML='<div class="loading">нет активных ордеров</div>';}
    else{ordEl.innerHTML=d.orders.map(o=>`
      <div class="order-item">
        <b>${o.symbol}</b> ${o.side} <code>${o.price}</code> qty=${o.qty}
        <button class="btn btn-gray" onclick="cancelOrder('${o.symbol}','${o.price}')">❌ Отмена</button>
      </div>`).join('');}

    // Setups
    const setEl=document.getElementById('setups');
    document.getElementById('setup-count').textContent=d.setups.length?`(${d.setups.length})`:'';
    if(!d.setups.length){setEl.innerHTML='<div class="loading">нет хороших сетапов — рынок flat<br><small> scanner: score≥75, momentum≥8, vol_ratio≥1.2</small></div>';}
    else{setEl.innerHTML=d.setups.map(s=>`
      <div class="setup">
        <div class="row"><span class="sym">${s.side==='LONG'?'🟢':'🔴'} ${s.short} ${s.side}</span><span class="rr">R:R ${s.rrr}</span></div>
        <div class="price">Вход: <code>${s.entry_low}</code>–<code>${s.entry_high}</code> · Тейк: <code>${s.tp}</code> · Стоп: <code>${s.sl}</code></div>
        <div class="price">${s.desc} · 24ч ${s.chg>=0?'+':''}${s.chg}% · волатильность ${s.atr_pct}%</div>
        <div class="price">до входа: ${s.dist_to_entry>=0?'+':''}${s.dist_to_entry}% · ${s.hold}</div>
        ${s.smc_hint?`<div class="price">💡 ${s.smc_hint}</div>`:''}
      </div>`).join('');}
  }catch(e){console.error(e);}
}
async function closePos(sym,side){
  if(!confirm(`Закрыть ${sym} ${side}?`))return;
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'close',symbol:sym})});
  const d=await r.json();alert(d.msg||d.error);fetchData();
}
async function cancelOrder(sym,price){
  if(!confirm(`Отменить ордер ${sym} @ ${price}?`))return;
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'cancel',symbol:sym,price:parseFloat(price)})});
  const d=await r.json();alert(d.msg||d.error);fetchData();
}
fetchData();
setInterval(fetchData,10000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif path == '/api':
            data = _refresh()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
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
                # Закрыть позицию через bingx_signal
                try:
                    import bingx_signal as bx
                    res = bx.bx_action(symbol, 'LONG', 'close')
                    result['msg'] = res.get('msg', 'OK')
                except Exception as e:
                    result['error'] = str(e)
            elif action == 'cancel' and symbol:
                result['msg'] = f'Отмена {symbol} — вручную через BingX'
                result['error'] = 'API отмены не реализован'
            else:
                result['error'] = 'unknown action'

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main():
    threading.Thread(target=_start_refresh_loop, daemon=True).start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  📊 TradingOS Dashboard")
    print(f"  http://localhost:{PORT}")
    print(f"  http://$(hostname -I | awk '{{print $1}}'):{PORT}")
    print(f"{'='*50}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
