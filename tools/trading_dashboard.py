#!/usr/bin/env python3
"""TradingOS Control Center v4 — professional trading terminal."""
from __future__ import annotations
import json, logging, os, sys, threading, time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")
sys.path.insert(0, "/root/tradingos/diagnostics")
import price_monitor as pm

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("trading_cc")
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))

STATE = {k: Path(f"/root/tradingos/operations/{n}.json") for k,n in
         [("manual","manual_state"),("trading","bingx_trading_mode"),
          ("guard","deposit_guard_state"),("session","manual_session")]}
ACT_LOG = Path("/root/tradingos/logs/activity_feed.jsonl")
_TTL, _cache, _ts = 6.0, {}, 0.0

def read_st(k):
    try: return json.loads(STATE[k].read_text()) if STATE[k].exists() else {}
    except: return {}
def write_st(k,d):
    try: STATE[k].write_text(json.dumps(d))
    except: pass
def log_ev(ev, detail="", level="info"):
    try:
        ACT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ACT_LOG,"a") as f:
            f.write(json.dumps({"ts":datetime.now(timezone.utc).isoformat(),"event":ev,"detail":detail,"level":level})+"\n")
    except: pass

def get_ctl():
    st,tm,dg,sess = read_st("manual"),read_st("trading"),read_st("guard"),read_st("session")
    paused=bool(st.get("paused",False)); mode=tm.get("mode","MANUAL")
    kill=bool(dg.get("kill_switch",False)); orders_off=bool(sess.get("manual_orders_disabled",False))
    if kill: overall,cls="KILL SWITCH","critical"
    elif paused: overall,cls="PAUSED","warning"
    elif mode=="AUTO": overall,cls="ONLINE","ok"
    else: overall,cls="MANUAL","info"
    hb=None
    u=st.get("updated_at","")
    if u:
        try: hb=int((datetime.now(timezone.utc)-datetime.fromisoformat(u.replace("Z","+00:00"))).total_seconds())
        except: pass
    return {"overall":overall,"cls":cls,"paused":paused,"mode":mode,"kill_switch":kill,
            "orders_disabled":orders_off,"heartbeat_age":hb,"sell_disabled":bool(tm.get("sell_disabled",False)),
            "risk_per_trade":tm.get("risk_per_trade",0),"max_positions":tm.get("max_positions",0)}

def apply_ctl(action):
    if action=="pause":
        st=read_st("manual") or {}; st.update({"paused":True,"reason":"cc","updated_at":datetime.now(timezone.utc).isoformat(),"updated_by":"dashboard"})
        write_st("manual",st); log_ev("Торговля приостановлена","from control center","warning"); return True,"Paused"
    elif action=="resume":
        st=read_st("manual") or {}; st.update({"paused":False,"reason":"resumed","updated_at":datetime.now(timezone.utc).isoformat(),"updated_by":"dashboard"})
        write_st("manual",st); log_ev("Торговля возобновлена","from control center","ok"); return True,"Resumed"
    elif action=="set_manual":
        tm=read_st("trading") or {}; tm["mode"]="MANUAL"; tm["updated_at"]=datetime.now(timezone.utc).isoformat()
        write_st("trading",tm); log_ev("Mode set MANUAL","from control center","warning"); return True,"MANUAL"
    elif action=="set_auto":
        tm=read_st("trading") or {}; tm["mode"]="AUTO"; tm["updated_at"]=datetime.now(timezone.utc).isoformat()
        write_st("trading",tm); log_ev("Mode set AUTO","from control center","ok"); return True,"AUTO"
    return False,"unknown"

def refresh():
    global _cache,_ts
    now=time.time()
    if now-_ts<_TTL and _cache: return _cache
    ak,as_=pm._load_keys()
    with pm.httpx.Client(timeout=10) as c:
        tc=pm._bulk_tickers(c)
    equity=free=0.0; positions=[]; orders=[]
    if ak and as_:
        try: b=pm.bingx_balance(ak,as_); equity=free=0.0; equity=b.get("equity",0); free=b.get("free",0)
        except: pass
        try: positions=pm.bingx_positions(ak,as_)
        except: pass
        try: orders=pm.bingx_open_orders(ak,as_)
        except: pass
    open_risk=sum((p["entry"]*p["qty"]*abs((p["mark"]-p["entry"])/p["entry"])/max(1,p["leverage"])) for p in positions if p.get("entry") and p.get("mark")) if positions else 0.0
    ctl=get_ctl(); act=_get_act(20)
    _cache={"money":{"equity":equity,"available":free,"used_margin":equity-free if equity>0 else 0,"risk":open_risk},
            "positions":positions,"orders":orders,
            "market":[{"symbol":s,"price":tc.get(s,{}).get("price",0),"chg":tc.get(s,{}).get("change",0)} for s in ["BTCUSDT","ETHUSDT","SOLUSDT"]],
            "control":ctl,"activity":act,"candidates":pm.CANDIDATES,"ts":datetime.now().strftime("%H:%M:%S")}
    _ts=time.time()
    return _cache

def _get_act(n=20):
    try:
        if not ACT_LOG.exists(): return []
        return [json.loads(l) for l in reversed(ACT_LOG.read_text().strip().split("\n")[-n:]) if l.strip()]
    except: return []

def _refresh_loop():
    while True: refresh(); time.sleep(_TTL)

def collect_setups():
    try:
        from tradingos.signals.manual_scanner import score_symbol
        import httpx as hx
        from concurrent.futures import ThreadPoolExecutor, as_completed
        res=[]
        # Scan top 10 in parallel (3-4s total vs 25s sequential)
        limited = pm.CANDIDATES[:10]
        with hx.Client(timeout=25) as c:
            def _score(sym):
                try:
                    sig=score_symbol(c,sym,min_score=50)
                    if not sig: return None
                    if sig.get("score",0)<70: return None
                    if sig.get("contour","NO_TRADE") not in ("MARKET","LIMIT"): return None
                    chg=pm.get_24h(sym).get("change",0)
                    setup="DIP" if chg<=-5 else "CORR" if chg<=-2 else "IMP" if chg>6 else "ACC"
                    return {"symbol":sig["symbol"],"short":sig["symbol"].replace("USDT",""),
                                "side":sig.get("side",""),"score":sig.get("score",0),"rr":sig.get("rr",0),
                                "price":sig.get("price",0),"sl":sig.get("sl",0),"tp":sig.get("final_tp",0),
                                "rsi":sig.get("rsi",0),"mom":sig.get("parts",{}).get("momentum",0),
                                "vol_ratio":sig.get("vol_ratio",0),"contour":sig.get("contour","NO_TRADE"),
                                "setup":setup,"squeeze":sig.get("squeeze_on",False),"smc":sig.get("smc_sweep","")}
                except: return None
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures={ex.submit(_score,s):s for s in limited}
                for f in as_completed(futures, timeout=15):
                    r=f.result()
                    if r: res.append(r)
                    if len(res)>=5: break
        res.sort(key=lambda x:(-x["score"],-x["rr"]))
        return res[:5]
    except Exception as e: logger.warning(f"setups:{e}"); return []

def coin_anal(sym):
    sym=sym.upper().replace("-","").replace("/","")
    if not sym.endswith("USDT"): sym+="USDT"
    with pm.httpx.Client(timeout=10) as c:
        tc=pm._bulk_tickers(c); t=tc.get(sym,{})
        price=t.get("price",0)
        if not price: return None
        chg,vol=t.get("change",0),t.get("volume",0)
        atr=pm._get_atr(sym,c); ap=atr/price*100 if price else 0
        from tradingos.signals.manual_scanner import score_symbol
        sig=score_symbol(c,sym,min_score=40)
        r7h=sig.get("range_7d_high",0) if sig else 0; r7l=sig.get("range_7d_low",0) if sig else 0
        rsi=sig.get("rsi",50) if sig else 50; mom=sig.get("parts",{}).get("momentum",0) if sig else 0
        sq=sig.get("squeeze_on",False) if sig else False; smc=sig.get("smc_sweep","") if sig else ""
        if rsi<35 and mom>=5: fc,fccl="Oversold — bounce likely","bullish"
        elif rsi>65 and mom>=5: fc,fccl="Overbought — correction likely","bearish"
        elif sq and mom>=6: fc,fccl="Squeeze — breakout imminent","squeeze"
        elif smc=="LONG": fc,fccl="SMC: liquidity sweep LONG","bullish"
        elif smc=="SHORT": fc,fccl="SMC: liquidity sweep SHORT","bearish"
        elif r7l and price<r7l*1.02: fc,fccl="Near 7d low — DIP zone","dip"
        elif r7h and price>r7h*0.98: fc,fccl="Near 7d high — breakout zone","breakout"
        else: fc,fccl="Neutral — waiting for impulse","neutral"
        return {"symbol":sym,"short":sym.replace("USDT",""),"price":price,"chg":chg,"vol":vol,
                "atr":atr,"atr_pct":round(ap,1),"score":sig.get("score",0) if sig else 0,
                "side":sig.get("side","") if sig else "","rr":sig.get("rr",0) if sig else 0,
                "sl":sig.get("sl",0) if sig else 0,"tp":sig.get("final_tp",0) if sig else 0,
                "rsi":rsi,"mom":mom,"vol_ratio":sig.get("vol_ratio",0) if sig else 0,
                "dist_e20":sig.get("dist_e20_pct",0) if sig else 0,"squeeze":sq,"smc":smc,
                "stoch_k":sig.get("stoch_k",0) if sig else 0,
                "h1":sig.get("parts",{}).get("h1_trend",0) if sig else 0,
                "h4":sig.get("h4_trend","") if sig else "","d1":sig.get("d1_trend","") if sig else "",
                "mtf":sig.get("mtf_agree") if sig else None,
                "dist7h":round((r7h-price)/price*100,1) if r7h else 0,
                "dist7l":round((price-r7l)/price*100,1) if r7l else 0,
                "range7d":f"{r7l:.4f}–{r7h:.4f}" if r7h and r7l else "—",
                "forecast":fc,"fc_class":fccl,"ts":datetime.now().strftime("%H:%M:%S")}

# Aliases for new Handler class
_get_coin_analysis = coin_anal
_get_activity = _get_act

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        p=urlparse(self.path).path; qs=parse_qs(urlparse(self.path).query)
        if p in('/','/index.html'): self._h(HTML)
        elif p=='/api': self._j(refresh())
        elif p=='/api/setups': self._j(collect_setups())
        elif p.startswith('/api/coin/'): d=coin_anal(p.split('/')[-1]); self._j(d or {"error":"not found"},404 if not d else 200)
        elif p=='/api/candidates': self._j([s for s in pm.CANDIDATES if qs.get('q',[''])[0].upper() in s])
        elif p=='/api/activity': self._j(_get_act(50))
        else: self._j({"error":"not found"},404)
    def do_POST(self):
        p=urlparse(self.path).path; n=int(self.headers.get('Content-Length',0)); b=json.loads(self.rfile.read(n)) if n else {}
        if p=='/api/control':
            ok,msg=apply_ctl(b.get('action','')); globals(); _ts=0; self._j({"ok":ok,"msg":msg})
        elif p=='/api/action':
            a,sym,side=b.get('action',''),b.get('symbol',''),b.get('side','LONG'); res={"msg":"","error":""}
            if a=='close' and sym:
                try:
                    import bingx_signal as bx; r=bx.bx_action(sym,side,'close'); res['msg']=r.get('msg','Закрыто'); log_ev(f"Closed {sym}",f"side={side}","warning")
                except Exception as e: res['error']=str(e)
            globals(); _ts=0; self._j(res)
        else: self._j({"error":"not found"},404)
    def _h(self,html):
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(html.encode())
    def _j(self,data,code=200):
        self.send_response(code); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps(data,ensure_ascii=False).encode())



# ════════════════════════════════════════════════════════════════════════════
# Frontend — Professional Trading Control Center
# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# Frontend — Modern TradingOS Control Center
# ════════════════════════════════════════════════════════════════════════════
HTML = r""""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08090A">
<title>TradingOS</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  /* Surface — Linear Dark but BRIGHTER for readability */
  --bg: #111318;
  --surface: #181B21;
  --surface-1: #1E222A;
  --surface-2: #252A33;
  --surface-3: #2C323E;

  /* Ink — warm white, not pure */
  --ink: #F0F2F8;
  --ink-muted: #A8AEB8;
  --ink-subtle: #7A8190;

  /* Lines — subtle but visible */
  --hairline: #3D4452;
  --hairline-strong: #505766;

  /* Accent — Linear purple, slightly brighter */
  --accent: #7C8AFF;
  --accent-strong: #8B9AFF;
  --accent-soft: rgba(107, 124, 247, 0.12);

  /* Semantic */
  --good: #3DDC95;
  --good-soft: rgba(52, 211, 153, 0.1);
  --warn: #FBBF24;
  --warn-soft: rgba(251, 191, 36, 0.1);
  --bad: #FA7070;
  --bad-soft: rgba(248, 113, 113, 0.1);

  /* Geometry */
  --radius: 6px;
  --radius-sm: 4px;
}

.light {
  --bg: #FAFAFA;
  --surface: #FFFFFF;
  --surface-1: #F6F9FC;
  --surface-2: #F0F4F8;
  --surface-3: #E8EDF2;
  --ink: #0A2540;
  --ink-muted: #425466;
  --ink-subtle: #8898AA;
  --hairline: #E8EDF2;
  --hairline-strong: #D4DBE3;
  --accent: #635BFF;
  --accent-strong: #5247DB;
  --accent-soft: #EBF0FF;
  --good: #00875A;
  --good-soft: rgba(0, 135, 90, 0.08);
  --warn: #FFB300;
  --warn-soft: rgba(255, 179, 0, 0.08);
  --bad: #E25950;
  --bad-soft: rgba(226, 89, 80, 0.08);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--bg);  height: 100%; }
body { background: var(--bg); 
  background: var(--bg);
  color: var(--ink);
  font-family: 'Inter', 'PT Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
}
code, .mono {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
}
a { color: var(--accent); text-decoration: none; }

/* ── App shell ── */
.app { display: grid; grid-template-rows: 48px 1fr; min-height: 100vh; }

/* ── Topbar ── */
.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 0 16px; height: 48px;
  background: var(--surface);
  border-bottom: 1px solid var(--hairline);
  position: sticky; top: 0; z-index: 50;
}
.brand {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; letter-spacing: -0.01em; font-weight: 600; letter-spacing: -0.01em; color: var(--ink);
}
.brand-mark {
  width: 20px; height: 20px;
  background: var(--accent);
  border-radius: var(--radius-sm);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; color: #fff;
}
.dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot-ok { background: var(--good); }
.dot-warn { background: var(--warn); }
.dot-crit { background: var(--bad); animation: blink 1.2s infinite; }
.dot-off { background: var(--ink-subtle); }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.4} }

.nav { display: flex; gap: 0; margin-left: 16px; height: 100%; }
.nav-link {
  height: 100%; padding: 0 12px;
  display: flex; align-items: center; gap: 6px;
  color: var(--ink-muted); font-size: 13px; font-weight: 500;
  cursor: pointer; border-right: 1px solid var(--hairline);
  transition: color .15s, background .15s;
}
.nav-link:hover { background: var(--surface-1); color: var(--ink); }
.nav-link.active { background: var(--surface-1); color: var(--ink); }
.nav-link .badge {
  background: var(--accent-soft); color: var(--accent);
  font-size: 12px; padding: 1px 5px; border-radius: 10px; font-weight: 600;
}

.top-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }

.search-wrap { position: relative; }
.search-input {
  width: 220px; height: 28px;
  padding: 0 8px 0 28px;
  border: 1px solid var(--hairline-strong);
  background: var(--surface-1);
  color: var(--ink); font-size: 13px; outline: none;
  border-radius: var(--radius-sm); transition: all .15s;
  font-family: inherit;
}
.search-input:focus { border-color: var(--accent); background: var(--surface-2); }
.search-input::placeholder { color: var(--ink-subtle); }
.search-icon {
  position: absolute; left: 8px; top: 50%; transform: translateY(-50%);
  color: var(--ink-subtle); pointer-events: none;
}
.search-dd {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0;
  background: var(--surface-1); border: 1px solid var(--hairline-strong);
  border-radius: var(--radius); max-height: 240px; overflow-y: auto;
  z-index: 200; display: none; box-shadow: 0 4px 16px rgba(0,0,0,.3);
}
.search-dd.show { display: block; }
.search-item {
  padding: 7px 10px; cursor: pointer; font-size: 13px;
  display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid var(--hairline); transition: background .1s;
}
.search-item:last-child { border-bottom: none; }
.search-item:hover { background: var(--surface-2); }
.search-item .sym-name { font-weight: 500; font-family: 'JetBrains Mono', monospace; }
.search-item .sym-type { font-size: 12px; color: var(--ink-subtle); }

.mode-tag {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 8px; font-size: 12px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase;
  border: 1px solid; font-family: 'JetBrains Mono', monospace; border-radius: var(--radius-sm);
}
.mode-auto { color: var(--good); border-color: var(--good); background: var(--good-soft); }
.mode-manual { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.mode-paused { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }
.mode-kill { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); animation: blink 1.2s infinite; }
.mode-off { color: var(--ink-subtle); border-color: var(--hairline-strong); background: transparent; }
.clock { color: var(--ink-muted); font-size: 12px; font-family: 'JetBrains Mono', monospace; letter-spacing: .04em; }
.icon-btn {
  width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid var(--hairline); color: var(--ink-muted);
  cursor: pointer; transition: all .15s; border-radius: var(--radius-sm);
}
.icon-btn:hover { color: var(--ink); border-color: var(--hairline-strong); background: var(--surface-1); }

/* ── Main ── */
.main { padding: 20px; width: 100%; max-width: 1400px; margin: 0 auto; }
.page { display: none; }
.page.active { display: block; animation: fadeIn .15s ease; }
@keyframes fadeIn { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }

/* ── Panels ── */
.panel {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
  overflow: hidden;
}
.panel-hd {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--hairline);
  background: var(--surface-1);
}
.panel-title {
  font-size: 12px; font-weight: 600; color: var(--ink-muted);
  text-transform: uppercase; letter-spacing: .08em;
  display: flex; align-items: center; gap: 6px;
}
.panel-title svg { width: 12px; height: 12px; stroke: var(--ink-subtle); fill: none; stroke-width: 1.8; }
.panel-body { background: var(--bg);  padding: 0; }
.panel-count { font-size: 12px; color: var(--ink-subtle); font-family: 'JetBrains Mono', monospace; }

/* ── Hero stat strip ── */
.hero { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px; background: var(--hairline); border: 1px solid var(--hairline); border-radius: var(--radius); margin-bottom: 12px; overflow: hidden; }
.hero-card { min-height: 80px; }
.hero-card {
  background: var(--surface); padding: 20px 22px; position: relative;
}
.hero-label { font-size: 10px; color: var(--ink-subtle); text-transform: uppercase; letter-spacing: .12em; font-weight: 700; margin-bottom: 8px; }
.hero-value { font-size: 32px; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1; letter-spacing: -.02em; font-family: 'JetBrains Mono', monospace; }
.hero-sub { font-size: 12px; color: var(--ink-muted); margin-top: 4px; font-family: 'JetBrains Mono', monospace; }

/* ── Control bar ── */
.ctrl {
  display: flex; gap: 0; background: var(--surface);
  border: 1px solid var(--hairline); border-radius: var(--radius);
  overflow: hidden; margin-bottom: 12px; height: 34px; align-items: stretch;
}
.seg { display: flex; height: 100%; }
.seg-btn {
  height: 100%; padding: 0 12px; background: transparent; border: none;
  border-right: 1px solid var(--hairline); color: var(--ink-muted);
  font-size: 12px; font-weight: 500; cursor: pointer;
  transition: all .15s; font-family: inherit;
  display: flex; align-items: center; gap: 5px;
}
.seg-btn:hover { color: var(--ink); background: var(--surface-1); }
.seg-btn.active { background: var(--accent-soft); color: var(--accent); }
.seg-btn.danger.active { background: var(--bad-soft); color: var(--bad); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  height: 100%; padding: 0 12px; border: none; border-right: 1px solid var(--hairline);
  background: transparent; color: var(--ink); cursor: pointer;
  font-size: 12px; font-weight: 500; transition: all .15s;
  font-family: inherit; white-space: nowrap;
}
.btn:hover { background: var(--surface-1); color: var(--ink); }
.btn-accent { color: var(--accent); }
.btn-accent:hover { background: var(--accent-soft); color: var(--accent); }
.btn-bad { color: var(--bad); }
.btn-bad:hover { background: var(--bad-soft); color: var(--bad); }
.btn-spacer { flex: 1; border-right: 1px solid var(--hairline); }

/* ── Grid layout ── */
.grid { display: grid; gap: 1px; background: var(--hairline); border: 1px solid var(--hairline); border-radius: var(--radius); overflow: hidden; }
@media(min-width:1024px){ .grid { grid-template-columns: 1fr 300px; align-items: start; } }
.col { display: flex; flex-direction: column; gap: 1px; background: var(--hairline); }
.panel { flex: 1; display: flex; flex-direction: column; }
.panel-body { flex: 1; }
.sidebar { display: flex; flex-direction: column; background: var(--hairline); }
.col { display: flex; flex-direction: column; background: var(--hairline); }

/* ── Tables ── */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 14px;
  color: var(--ink-subtle); font-weight: 500; font-size: 10px;
  text-transform: uppercase; letter-spacing: .08em;
  border-bottom: 1px solid var(--hairline); background: var(--surface-1);
  position: sticky; top: 0; z-index: 1; white-space: nowrap;
}
thead th.r { text-align: right; }
thead th.c { text-align: center; }
tbody td { padding: 10px 14px; border-bottom: 1px solid var(--hairline); vertical-align: middle; height: 38px; }
tbody td.r { text-align: right; }
tbody td.c { text-align: center; }
tbody tr:last-child td { border-bottom: none; }
tbody tr { transition: background .1s; }
tbody tr:hover { background: var(--surface-1); }
tbody tr:hover td:first-child { color: var(--accent); }
.sym { font-weight: 500; font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: -.01em; }
.num { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }

/* ── Tags ── */
.tag {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 1px 6px; font-size: 10px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase;
  font-family: 'JetBrains Mono', monospace;
  border: 1px solid; border-radius: 3px;
}
.tag-long { color: var(--good); border-color: var(--good); background: var(--good-soft); }
.tag-short { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.tag-buy { color: var(--good); border-color: var(--good); background: var(--good-soft); }
.tag-sell { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.tag-warn { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }
.tag-info { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tag-muted { color: var(--ink-muted); border-color: var(--hairline-strong); background: transparent; }
.tag-dot { width: 4px; height: 4px; border-radius: 50%; background: currentColor; flex-shrink: 0; }

/* ── Empty states ── */
.empty { padding: 28px 16px; text-align: center; color: var(--ink-subtle); font-size: 13px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.empty svg { width: 28px; height: 28px; stroke: var(--ink-subtle); fill: none; stroke-width: 1.5; opacity: .5; }
.empty-cta {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 10px 14px; border-radius: var(--radius-sm);
  background: var(--accent-soft); color: var(--accent);
  font-size: 12px; font-weight: 500; cursor: pointer;
  border: 1px solid var(--accent); transition: all .15s;
  font-family: inherit; margin-top: 4px;
}
.empty-cta:hover { background: var(--accent); color: #fff; }

/* ── Activity ── */
.act-list { max-height: 300px; overflow-y: auto; }
.act-item { display: grid; grid-template-columns: 50px 1fr auto; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--hairline); font-size: 12px; align-items: center; transition: background .1s; }
.act-item:last-child { border-bottom: none; }
.act-item:hover { background: var(--surface-1); }
.act-time { color: var(--ink-subtle); font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.act-text { color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: 6px; }
.act-dot { width: 4px; height: 4px; border-radius: 50%; flex-shrink: 0; }
.act-dot-ok { background: var(--good); }
.act-dot-warn { background: var(--warn); }
.act-dot-crit { background: var(--bad); }
.act-detail { color: var(--ink-subtle); font-size: 12px; font-family: 'JetBrains Mono', monospace; }

/* ── Setup cards ── */
.setup-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 1px; background: var(--hairline); }
.setup-card { background: var(--surface); padding: 14px 16px; cursor: pointer; transition: all .15s; position: relative; border-left: 2px solid var(--hairline-strong); }
.setup-card:hover { background: var(--surface-1); }
.setup-card.long { border-left-color: var(--good); }
.setup-card.short { border-left-color: var(--bad); }
.setup-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.setup-sym { font-size: 13px; font-weight: 600; font-family: 'JetBrains Mono', monospace; letter-spacing: -.01em; }
.setup-side { font-size: 10px; font-weight: 600; color: var(--ink-subtle); text-transform: uppercase; letter-spacing: .08em; }
.setup-side.long { color: var(--good); }
.setup-side.short { color: var(--bad); }
.setup-prices { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; padding: 6px 0; border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); background: var(--surface-1); }
.setup-pr-cell { text-align: center; padding: 0 4px; border-right: 1px solid var(--hairline); }
.setup-pr-cell:last-child { border-right: none; }
.setup-pr-label { font-size: 9px; color: var(--ink-subtle); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 2px; font-weight: 600; }
.setup-pr-val { font-size: 12px; font-weight: 500; font-family: 'JetBrains Mono', monospace; }
.setup-pr-val.entry { color: var(--ink); }
.setup-pr-val.tp { color: var(--good); }
.setup-pr-val.sl { color: var(--bad); }
.setup-foot { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--ink-subtle); font-family: 'JetBrains Mono', monospace; margin-top: 6px; }
.setup-rr { color: var(--ink); font-weight: 600; }

/* ── Market rows ── */
.market-row { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--hairline); font-size: 12px; align-items: center; transition: background .1s; }
.market-row:last-child { border-bottom: none; }
.market-row:hover { background: var(--surface-1); }
.market-sym { font-weight: 500; font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: -.01em; }
.market-price { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-size: 12px; }
.market-chg { font-size: 12px; font-weight: 600; padding: 1px 5px; font-family: 'JetBrains Mono', monospace; min-width: 52px; text-align: right; border-radius: 3px; }

/* ── Modal ── */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 200; display: flex; align-items: center; justify-content: center; padding: 20px; }
.modal-overlay.hidden { display: none; }
.modal { background: var(--surface-1); border: 1px solid var(--hairline-strong); width: 100%; max-width: 500px; max-height: 90vh; overflow: hidden; display: flex; flex-direction: column; }
.modal-hd { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--hairline); background: var(--surface-2); }
.modal-title { font-size: 14px; letter-spacing: -0.01em; font-weight: 600; font-family: 'JetBrains Mono', monospace; letter-spacing: -.01em; display: flex; align-items: center; gap: 8px; }
.modal-body { background: var(--bg);  padding: 18px; overflow-y: auto; flex: 1; }
.modal-section { margin-bottom: 14px; }
.modal-section-title { font-size: 10px; font-weight: 600; color: var(--ink-subtle); text-transform: uppercase; letter-spacing: .1em; margin-bottom: 8px; }
.modal-foot { display: flex; gap: 0; padding: 0; border-top: 1px solid var(--hairline); background: var(--surface-2); height: 38px; }
.modal-foot .btn { border-right: 1px solid var(--hairline); }

/* ── Toast ── */
.toast { position: fixed; bottom: 16px; right: 16px; padding: 10px 14px; font-size: 13px; z-index: 300; font-weight: 500; display: flex; align-items: center; gap: 8px; max-width: 360px; font-family: 'JetBrains Mono', monospace; border: 1px solid; }
.toast-ok { background: var(--good-soft); color: var(--good); border-color: var(--good); }
.toast-err { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.toast-info { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }
.toast-warn { background: var(--warn-soft); color: var(--warn); border-color: var(--warn); }

/* ── Misc ── */
.loading-state { padding: 18px; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--ink-subtle); font-size: 12px; }
.spinner { width: 12px; height: 12px; border: 1.5px solid var(--hairline-strong); border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-banner { background: var(--bad-soft); border: 1px solid var(--bad); color: var(--bad); padding: 10px 14px; font-size: 12px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; font-family: 'JetBrains Mono', monospace; border-radius: var(--radius-sm); }

/* ── Mobile ── */
@media(max-width:768px){
  .topbar { padding: 0 10px; gap: 8px; height: 44px; }
  .nav-link { padding: 0 8px; font-size: 12px; }
  .nav-link span:not(.badge) { display: none; }
  .top-right { gap: 4px; }
  .clock { display: none; }
  .search-input { width: 120px; }
  .hero { grid-template-columns: 1fr 1fr; }
  .hero-card { padding: 14px 16px; }
  .hero-value { font-size: 18px; }
  .grid { grid-template-columns: 1fr; }
  thead th, tbody td { padding: 5px 8px; font-size: 12px; height: 28px; }
  .ctrl { height: 38px; }
  .seg-btn, .btn { padding: 0 8px; font-size: 12px; }
}

/* Fixed viewport terminal layout */
html,body{height:100%;overflow:hidden}
body{min-height:100vh}
.app{height:100vh;min-height:0;overflow:hidden;display:flex;flex-direction:column}
.topbar{flex:0 0 48px}
.main{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column;padding:16px}
.page{flex:1;min-height:0;overflow:hidden;display:none}
.page.active{display:flex;flex-direction:column}
#page-dashboard{gap:12px}
.hero{flex:0 0 auto;margin-bottom:0}
.ctrl{flex:0 0 34px;margin-bottom:0}
.grid{flex:1;min-height:0;overflow:hidden;grid-template-columns:minmax(0,1fr) 300px}
.col,.sidebar{min-height:0;overflow:hidden}
.col>.panel{min-height:0;flex:1}
.sidebar>.panel{min-height:0;flex:1}
.panel-body{min-height:0;overflow:hidden}
#posWrap,#ordWrap{min-height:0;overflow:auto}
#marketBody,#setBody,#actBody{min-height:0;overflow:auto}
#page-scanner,#page-activity{min-height:0;overflow:hidden}
#page-scanner>.panel,#page-activity>.panel{min-height:0;flex:1;display:flex;flex-direction:column}
#scanWrap,#actBodyFull{min-height:0;flex:1;overflow:auto}
#actBodyFull{max-height:none!important}
@media(max-width:1023px){html,body{overflow:auto}.app{height:auto;min-height:100vh;overflow:visible}.main{overflow:visible}.page.active{overflow:visible}.grid{display:block;overflow:visible}.col,.sidebar{overflow:visible}.col>.panel,.sidebar>.panel{min-height:auto}.topbar{position:relative}}

/* Natural-height tables and compact widgets. */
@media(min-width:1024px){
  .main{height:auto;min-height:calc(100vh - 48px);overflow:visible}
  #page-dashboard{height:auto;min-height:0;overflow:visible}
  #page-dashboard>.grid{height:auto;min-height:0}
  .col,.sidebar{min-height:0;overflow:visible}
  .col>.panel,.sidebar>.panel{flex:0 0 auto;min-height:0}
  #actBody{max-height:300px;overflow-y:auto}
}
#posBody,#ordBody{display:block;min-height:0}
#posBody>table,#ordBody>table{min-height:0}
#posBody>.empty,#ordBody>.empty{min-height:0;flex:none;background:transparent;border:0;margin:0}
#setBody>.empty{min-height:0;padding:16px 12px}
#setBody>.empty .empty-cta{width:auto;display:inline-flex;margin:12px auto 0;padding:8px 16px;background:var(--accent);color:#fff;border:0;border-radius:var(--radius-sm);box-shadow:0 2px 8px var(--accent-soft)}
.act-text,.act-detail{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:1023px){#posBody>.empty,#ordBody>.empty{min-height:100px}}

/* FINAL OVERRIDE: natural data panels; no filler, no stretched empty area. */
@media(min-width:1024px){
  .main{height:auto!important;min-height:calc(100vh - 48px)!important;overflow:visible!important}
  #page-dashboard{height:auto!important;min-height:0!important;overflow:visible!important}
  #page-dashboard>.grid{height:auto!important;min-height:0!important;overflow:visible!important;display:grid!important;align-items:start!important}
  .col,.sidebar{height:auto!important;min-height:0!important;overflow:visible!important;display:flex!important;flex-direction:column!important}
  .col>.panel,.sidebar>.panel{height:auto!important;min-height:0!important;flex:0 0 auto!important}
  .panel-body{height:auto!important;min-height:0!important;overflow:visible!important}
  #posWrap,#ordWrap,#marketBody,#setBody,#actBody{height:auto!important;min-height:0!important;overflow:visible!important}
  #posBody,#ordBody{height:auto!important;min-height:0!important;display:block!important}
  #posBody>table,#ordBody>table{height:auto!important;min-height:0!important;flex:none!important}
  #posBody>.empty,#ordBody>.empty{height:auto!important;min-height:0!important;flex:none!important;background:transparent!important;border:0!important;margin:0!important}
  #setBody>.empty{height:auto!important;min-height:0!important;padding:18px 12px!important}
  #actBody{max-height:240px!important;overflow-y:auto!important}
}
</style>
</head>
<body>
<div class="app">

<header class="topbar">
  <div class="brand">
    <span class="dot dot-off" id="statusDot"></span>
    <div class="brand-mark">T</div>
    <span>TradingOS</span>
  </div>
  <nav class="nav">
    <div class="nav-link active" data-page="dashboard" onclick="switchPage('dashboard')">Дашборд</div>
    <div class="nav-link" data-page="scanner" onclick="switchPage('scanner')">
      Сканер — все сетапы<span class="badge" id="navSetupsCount" style="display:none">0</span>
    </div>
    <div class="nav-link" data-page="activity" onclick="switchPage('activity')">История</div>
  </nav>
  <div class="top-right">
    <div class="search-wrap">
      <svg class="search-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input class="search-input mono" id="globalSearch" placeholder="Search…" oninput="onGlobalSearch(this.value)" onkeydown="if(event.key==='Enter')doGlobalSearch()" onfocus="onGlobalSearch(this.value)">
      <div class="search-dd" id="searchDd"></div>
    </div>
    <span class="mode-tag mode-off" id="modePill">—</span>
    <span class="clock mono" id="clock">--:--:--</span>
    <button class="icon-btn" onclick="toggleTheme()" title="Theme">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
    </button>
  </div>
</header>

<main class="main">

<div class="page active" id="page-dashboard">

  <!-- Hero Stats -->
  <div class="hero" id="statStrip">
    <div class="hero-card">
      <div class="hero-label">Статус</div>
      <div class="hero-value" id="heroStatus" style="color:var(--ink-subtle)">—</div>
      <div class="hero-sub" id="heroMode">—</div>
    </div>
    <div class="hero-card">
      <div class="hero-label">Баланс</div>
      <div class="hero-value mono" id="heroEquity">—</div>
      <div class="hero-sub">Свободно: <span id="heroAvailable">—</span></div>
    </div>
    <div class="hero-card">
      <div class="hero-label">Заморожено</div>
      <div class="hero-value mono" id="heroPnl">—</div>
      <div class="hero-sub">В ордерах и позициях</div>
    </div>
    <div class="hero-card">
      <div class="hero-label">BTC</div>
      <div class="hero-value mono" id="heroBtc">—</div>
      <div class="hero-sub mono" id="heroBtcChg">—</div>
    </div>
    <div class="hero-card">
      <div class="hero-label">Сетапы</div>
      <div class="hero-value mono" id="heroSetups">—</div>
      <div class="hero-sub">Активных сигналов</div>
    </div>
    <div class="hero-card">
      <div class="hero-label">Обновлено</div>
      <div class="hero-value mono" id="lastUpdate" style="font-size:14px;margin-top:4px">—</div>
      <div class="hero-sub">[R] перезагрузить</div>
    </div>
  </div>

  <!-- Control bar -->
  <div class="ctrl">
    <div class="seg">
      <button class="seg-btn" data-mode="AUTO" onclick="ctrlAction('set_auto')">
        <span class="tag-dot" style="background:var(--good)"></span>AUTO
      </button>
      <button class="seg-btn" data-mode="MANUAL" onclick="ctrlAction('set_manual')">
        <span class="tag-dot" style="background:var(--accent)"></span>MANUAL
      </button>
      <button class="seg-btn danger" data-mode="PAUSE" onclick="ctrlAction('pause')">
        <span class="tag-dot" style="background:var(--warn)"></span>PAUSE
      </button>
    </div>
    <button class="btn btn-accent" onclick="refreshNow()">↻ Обновить</button>
    <div class="btn-spacer"></div>
    <button class="btn btn-bad" onclick="ctrlAction('kill')">⚠ KILL</button>
  </div>

  <div class="grid">
    <div class="col">
      <!-- Positions -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
            Positions
          </div>
          <span class="panel-count" id="posCount">0</span>
        </div>
        <div class="panel-body no-pad" id="posWrap"><div id="posBody"></div></div>
      </div>
      <!-- Orders -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
            Orders
          </div>
          <span class="panel-count" id="ordCount">0</span>
        </div>
        <div class="panel-body no-pad" id="ordWrap"><div id="ordBody"></div></div>
      </div>
    </div>

    <div class="sidebar">
      <!-- Market -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
            Market
          </div>
        </div>
        <div class="panel-body no-pad" id="marketBody"></div>
      </div>
      <!-- Setups -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
            Setups
          </div>
          <span class="panel-count" id="setCount">0</span>
        </div>
        <div class="panel-body no-pad"><div id="setBody"></div></div>
      </div>
      <!-- Activity -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            Activity
          </div>
        </div>
        <div class="panel-body no-pad">
          <div class="act-list" id="actBody"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Scanner page -->
<div class="page" id="page-scanner">
  <div class="panel">
    <div class="panel-hd">
      <div class="panel-title">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        Scanner
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="panel-count" id="scanStats">—</span>
        <button class="btn" style="height:26px;padding:0 8px;font-size:10px" onclick="loadScanner()">↻</button>
      </div>
    </div>
    <div class="panel-body no-pad" id="scanWrap"><div id="scanBody"></div></div>
  </div>
</div>

<!-- Activity page -->
<div class="page" id="page-activity">
  <div class="panel">
    <div class="panel-hd">
      <div class="panel-title">
        <svg viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        Журнал событий
      </div>
      <span class="panel-count" id="actCount">0</span>
    </div>
    <div class="panel-body no-pad">
      <div class="act-list" id="actBodyFull" style="max-height:none"></div>
    </div>
  </div>
</div>

</main>
</div>

<!-- Modal -->
<div class="modal-overlay hidden" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-hd">
      <span class="modal-title" id="modalTitle">Детали</span>
      <button class="icon-btn" onclick="closeModal()" style="width:24px;height:24px">
        <svg viewBox="0 0 24 24" width="12" height="12"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-foot" id="modalFoot"></div>
  </div>
</div>

<div id="toastContainer"></div>

<script>
'use strict';
const API='/api';
let CANDIDATES=[];
let currentState=null;
let refreshTimer=null;
let loading=false;
let lastError=null;
let filteredSetups=[];

function toggleTheme(){
  document.documentElement.classList.toggle('light');
  try{localStorage.setItem('theme',document.documentElement.classList.contains('light')?'light':'dark')}catch(e){}
}
try{const t=localStorage.getItem('theme');if(t==='light')document.documentElement.classList.add('light')}catch(e){}

function tickClock(){
  const el=document.getElementById('clock');
  if(el)el.textContent=new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
setInterval(tickClock,10000);
tickClock();

function toast(msg,type='info'){
  const c=document.getElementById('toastContainer');
  const t=document.createElement('div');
  t.className='toast toast-'+type;
  t.textContent=msg;
  c.appendChild(t);
  setTimeout(()=>{t.style.opacity='0';setTimeout(()=>t.remove(),200)},3000);
}

async function api(path,opts={}){
  try{
    const r=await fetch(API+path,opts);
    if(!r.ok){if(r.status===404)return{error:'not found'};throw new Error('HTTP '+r.status)}
    return await r.json();
  }catch(e){console.warn('API error',path,e);return{error:e.message}}
}

function escHtml(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function fmtPx(v){if(v==null||v==='')return'—';v=parseFloat(v);if(isNaN(v))return'—';if(Math.abs(v)>=1000)return v.toLocaleString('en-US',{maximumFractionDigits:2});if(Math.abs(v)>=1)return v.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');if(Math.abs(v)>=0.01)return v.toFixed(5).replace(/0+$/,'').replace(/\.$/,'');return v.toFixed(7).replace(/0+$/,'').replace(/\.$/,'')}
function fmtN(v,d=2){return v==null?'—':parseFloat(v).toFixed(d)}
function pnlCls(v){return v>0?'positive':v<0?'negative':'neutral'}
function pnlSign(v){return v>0?'+':''}
function chgCls(v){return v>=0?'positive':'negative'}

function switchPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l=>l.classList.remove('active'));
  const pg=document.getElementById('page-'+name);
  const lnk=document.querySelector(`.nav-link[data-page="${name}"]`);
  if(pg)pg.classList.add('active');
  if(lnk)lnk.classList.add('active');
  if(name==='scanner')loadScanner();
  if(name==='activity')loadActivity();
}

async function loadDashboard(){
  if(loading)return;
  loading=true;
  const d=await api('');
  loading=false;
  if(d&&d.error){showError('API недоступен: '+d.error);return}
  if(!d)return;
  currentState=d;CANDIDATES=d.candidates||CANDIDATES;lastError=null;hideError();
  renderDashboard(d);
  renderSetupsPreview(d.setups||[]);
}

function showError(msg){
  if(lastError===msg)return;lastError=msg;
  const e=document.createElement('div');e.className='error-banner';e.id='errorBanner';
  e.innerHTML='<span>⚠</span><span>'+escHtml(msg)+'</span><button class="btn" style="margin-left:auto;padding:4px 10px;font-size:10px;height:26px" onclick="hideError()">✕</button>';
  const main=document.querySelector('.main');
  if(main&&!document.getElementById('errorBanner'))main.prepend(e);
}
function hideError(){const e=document.getElementById('errorBanner');if(e)e.remove();lastError=null}

function renderDashboard(d){
  const ctl=d.control||{};
  const dot=document.getElementById('statusDot');
  const dotCls=ctl.cls==='ok'?'dot-ok':ctl.cls==='critical'?'dot-crit':ctl.cls==='warning'?'dot-warn':'dot-off';
  dot.className='dot '+dotCls;
  const heroStatus=document.getElementById('heroStatus');
  heroStatus.textContent=ctl.overall||'OFFLINE';
  heroStatus.style.color=ctl.cls==='ok'?'var(--good)':ctl.cls==='critical'?'var(--bad)':'var(--ink-subtle)';
  document.getElementById('heroMode').textContent=(ctl.mode||'—')+' · '+(ctl.orders_disabled?'Ордера ВЫКЛ':'Ордера ВКЛ');
  const m=d.money||{};
  document.getElementById('heroEquity').textContent='$'+fmtPx(m.equity);
  document.getElementById('heroAvailable').textContent='$'+fmtPx(m.available);
  const pnlEl=document.getElementById('heroPnl');
  const used=m.used_margin||0;
  pnlEl.textContent='$'+fmtPx(used);
  pnlEl.style.color=used>0?'var(--warn)':'var(--ink)';
  const btc=(d.market||[])[0]||{};
  document.getElementById('heroBtc').textContent='$'+fmtPx(btc.price);
  const btcChgEl=document.getElementById('heroBtcChg');
  btcChgEl.textContent=pnlSign(btc.chg||0)+((btc.chg||0).toFixed(2))+'%';
  btcChgEl.style.color=btc.chg>=0?'var(--good)':'var(--bad)';
  const setups=d.setups||[];
  document.getElementById('heroSetups').textContent=setups.length;
  document.getElementById('setCount').textContent=setups.length;
  const navBadge=document.getElementById('navSetupsCount');
  if(setups.length>0){navBadge.style.display='';navBadge.textContent=setups.length}
  else navBadge.style.display='none';
  document.querySelectorAll('.seg-btn').forEach(b=>{
    b.classList.toggle('active',
      (b.dataset.mode==='AUTO'&&ctl.mode==='AUTO'&&!ctl.paused&&!ctl.kill_switch)||
      (b.dataset.mode==='MANUAL'&&ctl.mode==='MANUAL'&&!ctl.paused&&!ctl.kill_switch)||
      (b.dataset.mode==='PAUSE'&&(ctl.paused||ctl.kill_switch)));
  });
  const mp=document.getElementById('modePill');
  mp.className='mode-tag '+
    (ctl.kill_switch?'mode-kill':ctl.paused?'mode-paused':ctl.mode==='AUTO'?'mode-auto':ctl.mode==='MANUAL'?'mode-manual':'mode-off');
  mp.textContent=ctl.kill_switch?'KILL':ctl.paused?'PAUSED':ctl.mode==='AUTO'?'AUTO':ctl.mode==='MANUAL'?'MANUAL':'OFFLINE';
  renderPositions(d.positions||[]);
  renderOrders(d.orders||[]);
  renderMarket(d.market||[]);
  renderActivityPreview(d.activity||[]);
  document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function renderPositions(pos){
  const body=document.getElementById('posBody');
  document.getElementById('posCount').textContent=pos.length;
  if(!pos.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg><div>Нет открытых позиций</div><button class="empty-cta" onclick="switchPage(\'scanner\')">Открыть сканер</button></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>МОНЕТА</th><th>СТОРОНА</th><th class="r">SIZE</th><th class="r">ENTRY</th><th class="r">MARK</th><th class="r">PNL</th><th class="r">LIQ%</th><th class="c"></th></tr></thead><tbody>'+pos.map(p=>{
    const pnl=p.upnl||0;
    const liqDist=p.liq&&p.mark?Math.abs((p.liq-p.mark)/p.mark*100):0;
    const liqCls=liqDist>50?'positive':liqDist>20?'':'negative';
    const side=p.side==='LONG'?'long':'short';
    return '<tr><td><span class="sym">'+escHtml(p.symbol)+'</span></td><td><span class="tag tag-'+side+'">'+side.toUpperCase()+'</span></td><td class="num r">'+fmtN(p.qty,4)+'</td><td class="num r">'+fmtPx(p.entry)+'</td><td class="num r">'+fmtPx(p.mark)+'</td><td class="num r '+pnlCls(pnl)+'" style="font-weight:600">'+pnlSign(pnl)+fmtN(pnl,2)+'</td><td class="num r '+liqCls+'">'+liqDist.toFixed(0)+'%</td><td class="c"><button class="icon-btn" style="width:22px;height:22px;color:var(--ink-subtle)" onclick="closePos(\''+escHtml(p.symbol)+'\',\''+escHtml(p.side)+'\')" title="Close"><svg viewBox="0 0 24 24" width="10" height="10"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>';
  }).join('')+'</tbody></table>';
}

function renderOrders(orders){
  const ord=orders.filter(o=>o.type&&['LIMIT','STOP_LIMIT','TAKE_PROFIT_LIMIT'].includes(String(o.type).toUpperCase()));
  const body=document.getElementById('ordBody');
  document.getElementById('ordCount').textContent=ord.length;
  if(!ord.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg><div>Нет активных ордеров</div></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>МОНЕТА</th><th>СТОРОНА</th><th>ТИП</th><th class="r">PRICE</th><th class="r">QTY</th><th class="c"></th></tr></thead><tbody>'+ord.map(o=>{
    const side=o.side==='BUY'?'buy':'sell';
    return '<tr><td><span class="sym">'+escHtml(o.symbol)+'</span></td><td><span class="tag tag-'+side+'">'+side.toUpperCase()+'</span></td><td><span class="tag tag-muted">'+escHtml(o.type)+'</span></td><td class="num r">'+fmtPx(o.price)+'</td><td class="num r">'+fmtN(o.qty,4)+'</td><td class="c"><button class="icon-btn" style="width:22px;height:22px;color:var(--ink-subtle)" onclick="cancelOrd(\''+escHtml(o.symbol)+'\','+(parseFloat(o.price)||0)+')" title="Cancel"><svg viewBox="0 0 24 24" width="10" height="10"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>';
  }).join('')+'</tbody></table>';
}

function renderMarket(market){
  const body=document.getElementById('marketBody');
  if(!market.length){body.innerHTML='<div class="empty"><span>No data</span></div>';return}
  body.innerHTML=market.slice(0,6).map(m=>{
    const chg=m.chg||0;
    const c=chg>=0?'var(--good)':'var(--bad)';
    const bg=chg>=0?'var(--good-soft)':'var(--bad-soft)';
    return '<div class="market-row"><span class="market-sym">'+escHtml(m.symbol.replace('USDT',''))+'</span><span class="market-price num">'+fmtPx(m.price)+'</span><span class="market-chg num" style="color:'+c+';background:'+bg+'">'+pnlSign(chg)+chg.toFixed(2)+'%</span></div>';
  }).join('');
}

function renderSetupsPreview(setups){
  const body=document.getElementById('setBody');
  if(!setups.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><div>Нет сетапов — рынок во флэте</div><button class="empty-cta" onclick="switchPage(\'scanner\')">Открыть сканер</button></div>';
    return;
  }
  body.innerHTML='<div class="setup-grid">'+setups.slice(0,6).map(renderSetupCard).join('')+'</div>';
}

function renderActivityPreview(acts){
  const body=document.getElementById('actBody');
  if(!acts.length){body.innerHTML='<div class="empty"><span>Empty</span></div>';return}
  body.innerHTML=acts.slice(0,6).map(renderActItem).join('');
}

function renderActItem(a){
  const dotCls=a.level==='critical'?'act-dot-crit':a.level==='warning'?'act-dot-warn':'act-dot-ok';
  const time=a.ts?new Date(a.ts).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  let evt=escHtml(a.event||'');
  const map={'Trading resumed':'Торговля возобновлена','Trading paused':'Торговля приостановлена','from dashboard':'из панели','Closed':'Закрыто','Cancelled':'Отменено'};
  for(const[k,v]of Object.entries(map))evt=evt.replace(new RegExp(k,'g'),v);
  let det=escHtml(a.detail||'');
  for(const[k,v]of Object.entries(map))det=det.replace(new RegExp(k,'g'),v);
  return '<div class="act-item"><span class="act-time">'+time+'</span><span class="act-text"><span class="act-dot '+dotCls+'"></span>'+evt+'</span><span class="act-detail">'+det+'</span></div>';
}

function renderSetupCard(s){
  const side=(s.side||'LONG').toLowerCase();
  const dist=s.dist_to_entry!=null?s.dist_to_entry:0;
  return '<div class="setup-card '+side+'" onclick="showCoin(\''+escHtml(s.symbol)+'\')"><div class="setup-head"><span class="setup-sym">'+escHtml(s.short||s.symbol)+'</span><span class="setup-side '+side+'">'+side.toUpperCase()+'</span></div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><span class="tag tag-info">SC '+s.score+'</span><span class="tag tag-muted">'+s.contour+'</span></div><div class="setup-prices"><div class="setup-pr-cell"><div class="setup-pr-label">ENTRY</div><div class="setup-pr-val entry num">'+fmtPx(s.entry_low||s.price)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">TP</div><div class="setup-pr-val tp num">'+fmtPx(s.tp)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">SL</div><div class="setup-pr-val sl num">'+fmtPx(s.sl)+'</div></div></div><div class="setup-foot"><span>R:R <span class="setup-rr num">'+fmtN(s.rr,1)+'</span></span><span class="num">'+pnlSign(dist)+dist.toFixed(1)+'%</span></div></div>';
}

async function showCoin(sym){
  const overlay=document.getElementById('modalOverlay');
  const body=document.getElementById('modalBody');
  const foot=document.getElementById('modalFoot');
  document.getElementById('modalTitle').textContent=sym.replace('USDT','');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Загрузка…</div>';
  foot.innerHTML='';
  overlay.classList.remove('hidden');
  const d=await api('/coin/'+encodeURIComponent(sym));
  if(d.error||!d){body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg><div>'+escHtml(d.error||'Не найдено')+'</div></div>';return}
  renderCoinModal(d);
}

function renderCoinModal(d){
  const body=document.getElementById('modalBody');
  const foot=document.getElementById('modalFoot');
  const rsiColor=d.rsi<30?'var(--good)':d.rsi>70?'var(--bad)':'var(--ink)';
  const momColor=d.mom>=10?'var(--good)':d.mom>=6?'var(--warn)':'var(--bad)';
  const side=d.side?d.side.toLowerCase():'';
  body.innerHTML='<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px"><div style="display:flex;align-items:center;gap:8px"><span class="sym" style="font-size:16px">'+escHtml(d.short||d.symbol)+'</span>'+(side?'<span class="tag tag-'+side+'">'+side.toUpperCase()+'</span>':'')+'</div>'+(d.score?'<span class="tag tag-info">SC '+d.score+'</span>':'')+'</div><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px"><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">PRICE</div><div class="num" style="font-size:14px;font-weight:600">'+fmtPx(d.price)+'</div></div><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">24H</div><div class="num" style="font-size:14px;font-weight:600;'+(d.chg>=0?'color:var(--good)':'color:var(--bad)')+'">'+pnlSign(d.chg||0)+(d.chg||0).toFixed(2)+'%</div></div><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">RSI(14)</div><div class="num" style="font-size:14px;font-weight:600;color:'+rsiColor+'">'+fmtN(d.rsi,1)+'</div></div><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">ATR(1H)</div><div class="num" style="font-size:14px;font-weight:600">'+(d.atr_pct||'—')+'%</div></div><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">VOL</div><div class="num" style="font-size:14px;font-weight:600">'+fmtN(d.vol_ratio,2)+'x</div></div><div style="background:var(--surface-2);padding:8px 10px;border-radius:var(--radius-sm)"><div class="hero-label">MOM</div><div class="num" style="font-size:14px;font-weight:600;color:'+momColor+'">'+(d.mom||0)+'/15</div></div></div>'+(d.sl&&d.tp?'<div class="modal-section"><div class="modal-section-title">LEVELS</div><table style="width:100%"><thead><tr><th>ТИП</th><th class="r">PRICE</th><th class="r">DELTA</th></tr></thead><tbody><tr><td>ENTRY</td><td class="num r">'+fmtPx(d.entry_low||d.price)+'</td><td class="num r" style="color:var(--ink-subtle)">—</td></tr><tr><td style="color:var(--good);font-weight:600">TP</td><td class="num r" style="color:var(--good)">'+fmtPx(d.tp)+'</td><td class="num r" style="color:var(--good)">+'+fmtN(d.tp_pct,1)+'%</td></tr><tr><td style="color:var(--bad);font-weight:600">SL</td><td class="num r" style="color:var(--bad)">'+fmtPx(d.sl)+'</td><td class="num r" style="color:var(--bad)">-'+fmtN(d.sl_pct,1)+'%</td></tr></tbody></table>'+(d.rr?'<div style="margin-top:8px;text-align:right;font-size:11px;color:var(--ink-muted)">R:R <b class="num" style="color:var(--ink)">'+fmtN(d.rr,2)+'</b></div>':'')+'</div>':'')+(d.forecast?'<div class="modal-section"><div class="modal-section-title">SIGNAL</div><div class="num" style="padding:8px 10px;background:var(--accent-soft);border-left:2px solid var(--accent);border-radius:0 var(--radius-sm) var(--radius-sm) 0;font-size:11px">'+escHtml(d.forecast)+'</div></div>':'');
  foot.innerHTML='<button class="btn btn-accent" style="flex:1" onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BINANCE:'+encodeURIComponent((d.symbol||'').replace('USDT',''))+'USDT.P\',\'_blank\')">TRADINGVIEW</button><button class="btn" onclick="showCoin(\''+escHtml(d.symbol)+'\')">ОБНОВИТЬ</button>';
}

function closeModal(){document.getElementById('modalOverlay').classList.add('hidden')}

async function loadScanner(){
  const body=document.getElementById('scanBody');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Сканирование…</div>';
  const setups=await api('/setups');
  filteredSetups=setups||[];
  document.getElementById('scanStats').textContent=filteredSetups.length+' setups';
  if(!filteredSetups.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><div>Нет квалифицированных сетапов</div><div style="font-size:11px;color:var(--ink-subtle);margin-top:4px">Все кандидаты не прошли фильтры. Рынок в боковике.</div><button class="empty-cta" onclick="loadScanner()">Повторить</button></div>';
    return;
  }
  body.innerHTML='<div class="setup-grid">'+filteredSetups.map(renderSetupCard).join('')+'</div>';
}

async function loadActivity(){
  const body=document.getElementById('actBodyFull');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Загрузка…</div>';
  const acts=await api('/activity');
  const list=Array.isArray(acts)?acts:[];
  document.getElementById('actCount').textContent=list.length;
  if(!list.length){body.innerHTML='<div class="empty"><span>Empty</span></div>';return}
  body.innerHTML=list.map(renderActItem).join('');
}

async function ctrlAction(action){
  if(action==='kill'){if(!confirm('KILL SWITCH?\n\nDisable all trading?'))return;if(!confirm('Точно? Бот перестанет торговать.'))return}
  else if(action==='pause'){if(!confirm('Поставить на паузу?'))return}
  else if(action==='set_auto'){if(!confirm('Переключить в AUTO?'))return}
  else if(action==='set_manual'){if(!confirm('Переключить в MANUAL?'))return}
  try{
    const r=await fetch(API+'/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    const d=await r.json();
    if(d.error)toast('⚠ '+d.error,'err');else{toast(d.msg||'OK','ok');loadDashboard()}
  }catch(e){toast('Ошибка сети','err')}
}

async function closePos(sym,side){
  if(!confirm('Close '+sym+' '+side+'?'))return;
  try{const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'close',symbol:sym,side})});const d=await r.json();toast(d.error?'⚠ '+d.error:'Закрыто',d.error?'err':'ok');loadDashboard()}catch(e){toast('Ошибка сети','err')}
}
async function cancelOrd(sym,price){
  if(!confirm('Cancel '+sym+' @ '+price+'?'))return;
  try{const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'cancel',symbol:sym,price})});const d=await r.json();toast(d.error?'⚠ '+d.error:'Отменено',d.error?'err':'ok');loadDashboard()}catch(e){toast('Ошибка сети','err')}
}

function refreshNow(){loadDashboard();if(document.getElementById('page-scanner').classList.contains('active'))loadScanner();if(document.getElementById('page-activity').classList.contains('active'))loadActivity()}

function onGlobalSearch(q){
  const dd=document.getElementById('searchDd');
  if(!q||q.length<1){dd.classList.remove('show');dd.innerHTML='';return}
  const upper=q.toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(!upper){dd.classList.remove('show');dd.innerHTML='';return}
  const matches=CANDIDATES.filter(s=>s.includes(upper)).slice(0,8);
  if(!matches.length){dd.innerHTML='<div class="search-item" style="color:var(--ink-subtle);cursor:default"><span>Не найдено</span></div>'}
  else{dd.innerHTML=matches.map(s=>'<div class="search-item" onclick="selectCoin(\''+s+'\')"><span class="sym-name">'+s.replace('USDT','')+'</span><span class="sym-type">USDT PERP</span></div>').join('')}
  dd.classList.add('show');
}
function doGlobalSearch(){
  const input=document.getElementById('globalSearch');
  let v=(input.value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(!v)return;
  if(!v.endsWith('USDT'))v+='USDT';
  selectCoin(v);
}
function selectCoin(sym){
  document.getElementById('globalSearch').value=sym.replace('USDT','');
  document.getElementById('searchDd').classList.remove('show');
  showCoin(sym);
}
document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap')){const dd=document.getElementById('searchDd');if(dd)dd.classList.remove('show')}});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape')closeModal();
  if(e.key==='/'&&!e.target.matches('input,textarea')){e.preventDefault();document.getElementById('globalSearch').focus()}
  if(e.key==='r'&&!e.target.matches('input,textarea')&&!e.ctrlKey&&!e.metaKey)refreshNow();
});

async function init(){
  const meta=await api('');
  if(meta&&!meta.error){CANDIDATES=meta.candidates||[];renderDashboard(meta);renderSetupsPreview(meta.setups||[]);renderActivityPreview(meta.activity||[])}
  else{showError('API недоступен')}
  if(refreshTimer)clearInterval(refreshTimer);
  refreshTimer=setInterval(loadDashboard,10000);
}
init();
</script>
</body>
</html>"""







class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if path in ('/', '/index.html'):
            html = HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())
        elif path == '/favicon.ico':
            # Inline SVG favicon (green dot)
            svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="#10b981"/></svg>'
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml')
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(svg)
        elif path == '/api':
            self._json(refresh())
        elif path == '/api/setups':
            self._json(collect_setups())
        elif path.startswith('/api/coin/'):
            sym = path.split('/')[-1].upper()
            d = _get_coin_analysis(sym)
            if d:
                self._json(d)
            else:
                self._json({"error": f"Coin {sym} not found"}, 404)
        elif path == '/api/candidates':
            q = qs.get('q', [''])[0].upper()
            self._json([s for s in pm.CANDIDATES if q in s])
        elif path == '/api/activity':
            self._json(_get_activity(50))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if path == '/api/control':
            ok, msg = apply_control(body.get('action', ''))
            self._json({"ok": ok, "msg": msg})
        elif path == '/api/action':
            action = body.get('action', '')
            sym = body.get('symbol', '')
            side = body.get('side', 'LONG')
            result = {"msg": "", "error": ""}
            if action == 'close' and sym:
                try:
                    import bingx_signal as bx
                    r = bx.bx_action(sym, side, 'close')
                    result['msg'] = r.get('msg', 'Закрыто')
                    log_event(f"Position closed {sym}", f"side={side}", "ok")
                except Exception as e:
                    result['error'] = str(e)
                    log_event(f"Close failed {sym}", str(e), "critical")
            elif action == 'cancel' and sym:
                result['msg'] = f"Cancel order {sym} — manually via BingX"
                log_event(f"Cancel order {sym}", "", "info")
            else:
                result['error'] = 'unknown action'
            self._json(result)
        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


def main():
    threading.Thread(target=_refresh_loop, daemon=True).start()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
    srv.socket.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)
    ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except: pass
    print(f"\n{'='*50}\n  TradingOS Control Center\n  http://localhost:{PORT}\n  http://{ip}:{PORT}\n{'='*50}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
