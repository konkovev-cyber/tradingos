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
        write_st("manual",st); log_ev("Trading paused","from control center","warning"); return True,"Paused"
    elif action=="resume":
        st=read_st("manual") or {}; st.update({"paused":False,"reason":"resumed","updated_at":datetime.now(timezone.utc).isoformat(),"updated_by":"dashboard"})
        write_st("manual",st); log_ev("Trading resumed","from control center","ok"); return True,"Resumed"
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
    _cache={"money":{"equity":equity,"available":free,"pnl":equity-free if equity>0 else 0,"risk":open_risk,"margin":max(0,equity-free) if equity>0 else 0},
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
                    import bingx_signal as bx; r=bx.bx_action(sym,side,'close'); res['msg']=r.get('msg','Closed'); log_ev(f"Closed {sym}",f"side={side}","warning")
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
HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0e14">
<title>TradingOS — Control Center</title>
<style>
:root{
  --bg:#0a0e14;--bg2:#11161d;--bg3:#1a212b;--bg4:#232c38;
  --border:#2a3340;--border-light:#364252;
  --text:#e6edf3;--text2:#8b95a7;--text3:#5a6373;
  --accent:#5e9bff;--accent-dim:rgba(94,155,255,.15);
  --green:#10b981;--green-dim:rgba(16,185,129,.15);
  --red:#ef4444;--red-dim:rgba(239,68,68,.15);
  --yellow:#f59e0b;--yellow-dim:rgba(245,158,11,.15);
  --purple:#a78bfa;--purple-dim:rgba(167,139,250,.15);
  --radius:10px;--radius-sm:6px;
  --shadow:0 1px 3px rgba(0,0,0,.2),0 4px 12px rgba(0,0,0,.15);
  --shadow-lg:0 4px 16px rgba(0,0,0,.3),0 12px 40px rgba(0,0,0,.25);
  --t:all .15s cubic-bezier(.4,0,.2,1);
}
.light{
  --bg:#f7f9fc;--bg2:#ffffff;--bg3:#eef2f7;--bg4:#e1e7ef;
  --border:#d8dee8;--border-light:#b8c0cc;
  --text:#0a0e14;--text2:#4a5563;--text3:#8b95a7;
  --accent:#2563eb;--accent-dim:rgba(37,99,235,.1);
  --green:#059669;--green-dim:rgba(5,150,105,.1);
  --red:#dc2626;--red-dim:rgba(220,38,38,.1);
  --yellow:#d97706;--yellow-dim:rgba(217,119,6,.1);
  --purple:#7c3aed;--purple-dim:rgba(124,58,237,.1);
  --shadow:0 1px 3px rgba(0,0,0,.05),0 4px 12px rgba(0,0,0,.04);
  --shadow-lg:0 4px 16px rgba(0,0,0,.08),0 12px 40px rgba(0,0,0,.06);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden}
a{color:var(--accent);text-decoration:none}
code{font-family:'SF Mono',ui-monospace,'JetBrains Mono',monospace;font-variant-numeric:tabular-nums}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:var(--border-light)}

.app{display:grid;grid-template-rows:56px 1fr;min-height:100vh}
.topbar{display:flex;align-items:center;gap:16px;padding:0 20px;height:56px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.brand{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;letter-spacing:-.01em}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot-ok{background:var(--green);box-shadow:0 0 8px var(--green)}
.dot-warn{background:var(--yellow);box-shadow:0 0 8px var(--yellow)}
.dot-crit{background:var(--red);box-shadow:0 0 8px var(--red);animation:blink 1.5s infinite}
.dot-off{background:var(--text3)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.4}}

.nav{display:flex;gap:4px;margin-left:24px}
.nav-link{padding:7px 14px;border-radius:8px;color:var(--text2);font-size:13px;font-weight:500;cursor:pointer;transition:var(--t);display:flex;align-items:center;gap:6px}
.nav-link:hover{background:var(--bg3);color:var(--text)}
.nav-link.active{background:var(--accent-dim);color:var(--accent)}
.nav-link .badge-count{background:var(--accent);color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;font-weight:600}

.top-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.mode-pill{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.02em;border:1px solid transparent}
.mode-auto{background:var(--green-dim);color:var(--green);border-color:var(--green)}
.mode-manual{background:var(--accent-dim);color:var(--accent);border-color:var(--accent)}
.mode-paused{background:var(--yellow-dim);color:var(--yellow);border-color:var(--yellow)}
.mode-kill{background:var(--red-dim);color:var(--red);border-color:var(--red);animation:blink 1.5s infinite}
.mode-off{background:var(--bg3);color:var(--text3);border-color:var(--border)}
.clock{color:var(--text2);font-size:12px;font-family:'SF Mono',monospace;padding:0 8px}
.icon-btn{width:32px;height:32px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;background:transparent;border:1px solid transparent;color:var(--text2);cursor:pointer;transition:var(--t);font-size:14px}
.icon-btn:hover{background:var(--bg3);color:var(--text);border-color:var(--border)}

.main{padding:20px;max-width:1400px;margin:0 auto;width:100%}
.page{display:none;animation:fadeIn .2s ease}
.page.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

.panel{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow)}
.panel-hd{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border)}
.panel-title{font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;display:flex;align-items:center;gap:6px}
.panel-title-icon{font-size:14px}
.panel-body{padding:16px}
.panel-body.no-pad{padding:0}
.panel-count{font-size:11px;background:var(--bg3);color:var(--text2);padding:2px 8px;border-radius:10px;font-weight:600}

.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;padding:16px}
.stat-card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;transition:var(--t);position:relative;overflow:hidden}
.stat-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:transparent;transition:var(--t)}
.stat-card:hover{border-color:var(--border-light)}
.stat-card.accent-green::before{background:var(--green)}
.stat-card.accent-red::before{background:var(--red)}
.stat-card.accent-blue::before{background:var(--accent)}
.stat-card.accent-yellow::before{background:var(--yellow)}
.stat-label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;font-weight:600}
.stat-value{font-size:24px;font-weight:700;font-family:'SF Mono',ui-monospace,monospace;letter-spacing:-.02em;line-height:1.2}
.stat-sub{font-size:11px;color:var(--text2);margin-top:4px;display:flex;align-items:center;gap:4px}
.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:var(--text2)}

.ctrl{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;background:var(--bg3);border-top:1px solid var(--border);align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--bg2);color:var(--text);cursor:pointer;font-size:12px;font-weight:500;transition:var(--t);font-family:inherit;white-space:nowrap}
.btn:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{background:#4b87ee;border-color:#4b87ee;color:#fff}
.btn-ghost{background:transparent;border-color:var(--border)}
.btn-success{background:var(--green-dim);color:var(--green);border-color:var(--green)}
.btn-success:hover{background:var(--green);color:#fff}
.btn-warn{background:var(--yellow-dim);color:var(--yellow);border-color:var(--yellow)}
.btn-warn:hover{background:var(--yellow);color:#fff}
.btn-danger{background:var(--red-dim);color:var(--red);border-color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}
.btn-sm{padding:5px 10px;font-size:11px}
.btn-block{flex:1;justify-content:center}
.seg{display:flex;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:2px;gap:2px}
.seg-btn{padding:6px 12px;border-radius:6px;background:transparent;border:none;color:var(--text2);font-size:12px;font-weight:500;cursor:pointer;transition:var(--t);font-family:inherit}
.seg-btn:hover{color:var(--text)}
.seg-btn.active{background:var(--accent);color:#fff}

table{width:100%;border-collapse:collapse;font-size:13px}
thead th{text-align:left;padding:10px 16px;color:var(--text2);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--border);background:var(--bg3);position:sticky;top:0;z-index:1}
tbody td{padding:12px 16px;border-bottom:1px solid var(--border);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr{transition:var(--t)}
tbody tr:hover{background:var(--bg3)}
.sym{font-weight:600;font-family:'SF Mono',monospace}
.price{font-family:'SF Mono',monospace;font-variant-numeric:tabular-nums}

.badge{display:inline-flex;align-items:center;gap:3px;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.badge-long{background:var(--green-dim);color:var(--green)}
.badge-short{background:var(--red-dim);color:var(--red)}
.badge-warn{background:var(--yellow-dim);color:var(--yellow)}
.badge-danger{background:var(--red-dim);color:var(--red)}
.badge-info{background:var(--accent-dim);color:var(--accent)}
.badge-neutral{background:var(--bg3);color:var(--text2)}

.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:48px 20px;text-align:center;color:var(--text2)}
.empty-icon{font-size:48px;margin-bottom:12px;opacity:.4;line-height:1}
.empty-title{font-size:15px;font-weight:600;color:var(--text);margin-bottom:6px}
.empty-sub{font-size:12px;color:var(--text2);max-width:320px;line-height:1.5;margin-bottom:16px}
.empty-cta{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;background:var(--accent-dim);color:var(--accent);font-size:13px;font-weight:500;cursor:pointer;border:1px solid var(--accent);transition:var(--t);font-family:inherit}
.empty-cta:hover{background:var(--accent);color:#fff}

.act-list{max-height:300px;overflow-y:auto}
.act-item{display:flex;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;align-items:flex-start;transition:var(--t)}
.act-item:last-child{border-bottom:none}
.act-item:hover{background:var(--bg3)}
.act-time{color:var(--text2);font-family:'SF Mono',monospace;font-size:10px;white-space:nowrap;min-width:48px;padding-top:2px}
.act-dot{width:6px;height:6px;border-radius:50%;margin-top:6px;flex-shrink:0}
.act-dot-ok{background:var(--green)}.act-dot-warn{background:var(--yellow)}.act-dot-crit{background:var(--red)}
.act-text{flex:1;color:var(--text);line-height:1.5}
.act-detail{color:var(--text2);font-size:11px;margin-top:1px}

.setup-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;padding:14px}
.setup-card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px;cursor:pointer;transition:var(--t);position:relative;overflow:hidden}
.setup-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--border);transition:var(--t)}
.setup-card.long::before{background:var(--green)}
.setup-card.short::before{background:var(--red)}
.setup-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.setup-card-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.setup-sym{font-size:15px;font-weight:700;font-family:'SF Mono',monospace;display:flex;align-items:center;gap:5px}
.setup-side{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em}
.setup-side.long{color:var(--green)}
.setup-side.short{color:var(--red)}
.setup-prices{display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;margin:8px 0;padding:8px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.setup-pr-cell{text-align:center}
.setup-pr-label{font-size:9px;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px;font-weight:600}
.setup-pr-val{font-size:12px;font-weight:600;font-family:'SF Mono',monospace}
.setup-pr-val.entry{color:var(--text)}
.setup-pr-val.tp{color:var(--green)}
.setup-pr-val.sl{color:var(--red)}
.setup-foot{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--text2)}
.setup-rr{color:var(--text);font-weight:600}
.setup-dist{display:flex;align-items:center;gap:3px}

.market-row{display:flex;justify-content:space-between;align-items:center;padding:8px 16px;border-bottom:1px solid var(--border);font-size:13px;transition:var(--t)}
.market-row:last-child{border-bottom:none}
.market-row:hover{background:var(--bg3)}
.market-sym{font-weight:600;font-family:'SF Mono',monospace}
.market-price{font-family:'SF Mono',monospace;font-variant-numeric:tabular-nums}
.market-chg{font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;font-family:'SF Mono',monospace}

.dash-grid{display:grid;gap:16px;grid-template-columns:1fr;margin-top:16px}
@media(min-width:1024px){.dash-grid{grid-template-columns:2fr 1fr}}
.sidebar{display:flex;flex-direction:column;gap:16px}

.search-top{position:relative;width:280px}
.search-input{width:100%;padding:7px 12px 7px 32px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:13px;outline:none;transition:var(--t);font-family:inherit}
.search-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-dim)}
.search-input::placeholder{color:var(--text3)}
.search-icon{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--text3);font-size:13px;pointer-events:none}
.search-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:var(--bg2);border:1px solid var(--border);border-radius:8px;max-height:280px;overflow-y:auto;z-index:200;display:none;box-shadow:var(--shadow-lg)}
.search-dd.show{display:block}
.search-item{padding:8px 12px;cursor:pointer;font-size:13px;font-family:'SF Mono',monospace;display:flex;justify-content:space-between;align-items:center;transition:var(--t)}
.search-item:hover{background:var(--bg3)}
.search-item .sym-name{font-weight:600}
.search-item .sym-type{font-size:10px;color:var(--text3)}

.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;display:flex;align-items:center;justify-content:center;padding:16px;backdrop-filter:blur(4px);animation:fadeIn .15s}
.modal-overlay.hidden{display:none}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:14px;width:100%;max-width:560px;max-height:90vh;overflow:hidden;box-shadow:var(--shadow-lg);display:flex;flex-direction:column}
.modal-hd{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border)}
.modal-title{font-size:16px;font-weight:700;font-family:'SF Mono',monospace}
.modal-body{padding:20px;overflow-y:auto;flex:1}
.modal-section{margin-bottom:18px}
.modal-section-title{font-size:10px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.modal-foot{display:flex;gap:8px;padding:14px 20px;border-top:1px solid var(--border);background:var(--bg3)}

.toast{position:fixed;bottom:20px;right:20px;padding:10px 16px;border-radius:10px;font-size:13px;z-index:300;animation:slideIn .2s ease;box-shadow:var(--shadow-lg);font-weight:500;display:flex;align-items:center;gap:8px;max-width:380px}
.toast-ok{background:var(--green);color:#fff}
.toast-err{background:var(--red);color:#fff}
.toast-info{background:var(--accent);color:#fff}
.toast-warn{background:var(--yellow);color:#000}
@keyframes slideIn{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}

.loading-state{padding:24px;display:flex;align-items:center;justify-content:center;gap:10px;color:var(--text2);font-size:13px}
.spinner{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.error-banner{background:var(--red-dim);border:1px solid var(--red);color:var(--red);padding:12px 16px;border-radius:8px;font-size:13px;margin-bottom:16px;display:flex;align-items:center;gap:10px}

@media(max-width:768px){
  .topbar{padding:0 12px;gap:8px}
  .nav{margin-left:8px;gap:0}
  .nav-link{padding:6px 10px;font-size:12px}
  .nav-link span:not(.badge-count){display:none}
  .top-right{gap:4px}
  .clock{display:none}
  .search-top{width:160px}
  .main{padding:12px}
  .stat-grid{grid-template-columns:1fr 1fr;padding:12px;gap:8px}
  .stat-value{font-size:18px}
  .ctrl{padding:8px 10px}
  .btn{padding:7px 10px;font-size:11px}
  .dash-grid{grid-template-columns:1fr;gap:12px}
  thead th,tbody td{padding:8px 10px;font-size:12px}
}
</style>
</head>
<body>
<div class="app">

<header class="topbar">
  <div class="brand"><span class="dot dot-off" id="statusDot"></span><span>TradingOS</span></div>
  <nav class="nav">
    <div class="nav-link active" data-page="dashboard" onclick="switchPage('dashboard')">
      <span>📊</span><span>Dashboard</span>
    </div>
    <div class="nav-link" data-page="scanner" onclick="switchPage('scanner')">
      <span>🔍</span><span>Scanner</span>
      <span class="badge-count" id="navSetupsCount" style="display:none">0</span>
    </div>
    <div class="nav-link" data-page="activity" onclick="switchPage('activity')">
      <span>📋</span><span>Activity</span>
    </div>
  </nav>
  <div class="top-right">
    <div class="search-top">
      <span class="search-icon">🔎</span>
      <input class="search-input" id="globalSearch" placeholder="Поиск монеты…" oninput="onGlobalSearch(this.value)" onkeydown="if(event.key==='Enter')doGlobalSearch()" onfocus="onGlobalSearch(this.value)">
      <div class="search-dd" id="searchDd"></div>
    </div>
    <span class="mode-pill mode-off" id="modePill">—</span>
    <span class="clock" id="clock">--:--</span>
    <button class="icon-btn" id="themeBtn" onclick="toggleTheme()" title="Тема">◐</button>
  </div>
</header>

<main class="main">

<div class="page active" id="page-dashboard">

  <div class="panel">
    <div class="panel-hd">
      <div class="panel-title"><span class="panel-title-icon">📡</span> System Status</div>
      <span class="panel-count" id="lastUpdate">—</span>
    </div>
    <div class="stat-grid" id="statGrid">
      <div class="stat-card accent-blue">
        <div class="stat-label">Status</div>
        <div class="stat-value" id="heroStatus">—</div>
        <div class="stat-sub" id="heroMode">—</div>
      </div>
      <div class="stat-card accent-green" id="equityCard">
        <div class="stat-label">Equity</div>
        <div class="stat-value" id="heroEquity">—</div>
        <div class="stat-sub" id="heroAvailable">—</div>
      </div>
      <div class="stat-card" id="pnlCard">
        <div class="stat-label">PnL (Day)</div>
        <div class="stat-value" id="heroPnl">—</div>
        <div class="stat-sub" id="heroRisk">—</div>
      </div>
      <div class="stat-card accent-yellow">
        <div class="stat-label">BTC</div>
        <div class="stat-value" id="heroBtc">—</div>
        <div class="stat-sub" id="heroBtcChg">—</div>
      </div>
      <div class="stat-card accent-blue">
        <div class="stat-label">Setups</div>
        <div class="stat-value" id="heroSetups">—</div>
        <div class="stat-sub">Активные сигналы</div>
      </div>
    </div>
    <div class="ctrl" id="ctrlBar">
      <div class="seg" id="modeSeg">
        <button class="seg-btn" data-mode="AUTO" onclick="ctrlAction('set_auto')">⚡ Auto</button>
        <button class="seg-btn" data-mode="MANUAL" onclick="ctrlAction('set_manual')">✋ Manual</button>
        <button class="seg-btn" data-mode="PAUSE" onclick="ctrlAction('pause')">⏸ Pause</button>
      </div>
      <button class="btn btn-sm btn-ghost" onclick="refreshNow()" title="Обновить (R)">🔄</button>
      <button class="btn btn-sm btn-danger" onclick="ctrlAction('kill')" style="margin-left:auto">⚠ KILL</button>
    </div>
  </div>

  <div class="dash-grid">
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title"><span class="panel-title-icon">📂</span> Open Positions</div>
          <span class="panel-count" id="posCount">0</span>
        </div>
        <div class="panel-body no-pad" id="posWrap"><div id="posBody"></div></div>
      </div>
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title"><span class="panel-title-icon">📋</span> Open Orders</div>
          <span class="panel-count" id="ordCount">0</span>
        </div>
        <div class="panel-body no-pad" id="ordWrap"><div id="ordBody"></div></div>
      </div>
    </div>

    <div class="sidebar">
      <div class="panel">
        <div class="panel-hd"><div class="panel-title"><span class="panel-title-icon">📈</span> Market</div></div>
        <div class="panel-body no-pad" id="marketBody"></div>
      </div>
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title"><span class="panel-title-icon">🎯</span> Top Setups</div>
          <span class="panel-count" id="setCount">0</span>
        </div>
        <div class="panel-body no-pad"><div id="setBody"></div></div>
      </div>
      <div class="panel">
        <div class="panel-hd"><div class="panel-title"><span class="panel-title-icon">📋</span> Recent Activity</div></div>
        <div class="panel-body no-pad">
          <div class="act-list" id="actBody"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="page" id="page-scanner">
  <div class="panel">
    <div class="panel-hd">
      <div class="panel-title"><span class="panel-title-icon">🔍</span> Scanner — все сетапы</div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="panel-count" id="scanStats">—</span>
        <button class="btn btn-sm" onclick="loadScanner()">🔄</button>
      </div>
    </div>
    <div class="panel-body no-pad" id="scanWrap"><div id="scanBody"></div></div>
  </div>
</div>

<div class="page" id="page-activity">
  <div class="panel">
    <div class="panel-hd">
      <div class="panel-title"><span class="panel-title-icon">📋</span> Activity Log</div>
      <span class="panel-count" id="actCount">0</span>
    </div>
    <div class="panel-body no-pad">
      <div class="act-list" id="actBodyFull"></div>
    </div>
  </div>
</div>

</main>
</div>

<div class="modal-overlay hidden" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-hd">
      <span class="modal-title" id="modalTitle">Details</span>
      <button class="icon-btn" onclick="closeModal()">✕</button>
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
  if(el)el.textContent=new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
}
setInterval(tickClock,30000);
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
    if(!r.ok){
      if(r.status===404)return{error:'not found'};
      throw new Error('HTTP '+r.status);
    }
    return await r.json();
  }catch(e){
    console.warn('API error',path,e);
    return{error:e.message};
  }
}

function escHtml(s){
  if(s==null)return'';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtPx(v){
  if(v==null||v==='')return'—';
  v=parseFloat(v);
  if(isNaN(v))return'—';
  if(Math.abs(v)>=1000)return v.toLocaleString('en-US',{maximumFractionDigits:2});
  if(Math.abs(v)>=1)return v.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  if(Math.abs(v)>=0.01)return v.toFixed(5).replace(/0+$/,'').replace(/\.$/,'');
  return v.toFixed(7).replace(/0+$/,'').replace(/\.$/,'');
}
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
  if(d&&d.error){
    showError('API недоступен: '+d.error);
    return;
  }
  if(!d)return;
  currentState=d;
  CANDIDATES=d.candidates||CANDIDATES;
  lastError=null;
  hideError();
  renderDashboard(d);
  renderSetupsPreview(d.setups||[]);
}

function showError(msg){
  if(lastError===msg)return;
  lastError=msg;
  const e=document.createElement('div');
  e.className='error-banner';
  e.id='errorBanner';
  e.innerHTML='<span>⚠</span><span>'+escHtml(msg)+'</span><button class="btn btn-sm" style="margin-left:auto" onclick="hideError()">✕</button>';
  const main=document.querySelector('.main');
  if(main&&!document.getElementById('errorBanner'))main.prepend(e);
}
function hideError(){
  const e=document.getElementById('errorBanner');
  if(e)e.remove();
  lastError=null;
}

function renderDashboard(d){
  const ctl=d.control||{};
  const dot=document.getElementById('statusDot');
  const dotCls=ctl.cls==='ok'?'dot-ok':ctl.cls==='critical'?'dot-crit':ctl.cls==='warning'?'dot-warn':'dot-off';
  dot.className='dot '+dotCls;
  const heroStatus=document.getElementById('heroStatus');
  heroStatus.textContent=ctl.overall||'OFFLINE';
  heroStatus.className='stat-value '+(ctl.cls==='ok'?'positive':ctl.cls==='critical'?'negative':'neutral');
  document.getElementById('heroMode').textContent=(ctl.mode||'—')+' · Orders '+(ctl.orders_disabled?'OFF':'ON');
  const m=d.money||{};
  document.getElementById('heroEquity').textContent='$'+fmtPx(m.equity);
  document.getElementById('heroAvailable').textContent='Available: $'+fmtPx(m.available);
  const pnlEl=document.getElementById('heroPnl');
  const pnl=m.pnl||0;
  pnlEl.textContent=pnlSign(pnl)+'$'+fmtPx(pnl);
  pnlEl.className='stat-value '+pnlCls(pnl);
  document.getElementById('heroRisk').textContent='Open risk: $'+fmtPx(m.risk);
  const btc=(d.market||[])[0]||{};
  document.getElementById('heroBtc').textContent='$'+fmtPx(btc.price);
  const btcChgEl=document.getElementById('heroBtcChg');
  btcChgEl.textContent=pnlSign(btc.chg||0)+((btc.chg||0).toFixed(2))+'%';
  btcChgEl.className='stat-sub '+chgCls(btc.chg||0);
  const setups=d.setups||[];
  document.getElementById('heroSetups').textContent=setups.length;
  document.getElementById('setCount').textContent=setups.length;
  const navBadge=document.getElementById('navSetupsCount');
  if(setups.length>0){navBadge.style.display='';navBadge.textContent=setups.length}
  else navBadge.style.display='none';
  document.querySelectorAll('#modeSeg .seg-btn').forEach(b=>{
    b.classList.toggle('active',
      (b.dataset.mode==='AUTO'&&ctl.mode==='AUTO'&&!ctl.paused&&!ctl.kill_switch)||
      (b.dataset.mode==='MANUAL'&&ctl.mode==='MANUAL'&&!ctl.paused&&!ctl.kill_switch)||
      (b.dataset.mode==='PAUSE'&&(ctl.paused||ctl.kill_switch)));
  });
  const mp=document.getElementById('modePill');
  mp.className='mode-pill '+
    (ctl.kill_switch?'mode-kill':ctl.paused?'mode-paused':ctl.mode==='AUTO'?'mode-auto':ctl.mode==='MANUAL'?'mode-manual':'mode-off');
  mp.textContent=
    ctl.kill_switch?'⚠ KILL':ctl.paused?'⏸ PAUSED':ctl.mode==='AUTO'?'⚡ AUTO':ctl.mode==='MANUAL'?'✋ MANUAL':'—';
  renderPositions(d.positions||[]);
  renderOrders(d.orders||[]);
  renderMarket(d.market||[]);
  renderActivityPreview(d.activity||[]);
  document.getElementById('lastUpdate').textContent='Updated '+new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
}

function renderPositions(pos){
  const body=document.getElementById('posBody');
  document.getElementById('posCount').textContent=pos.length;
  if(!pos.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">📭</div><div class="empty-title">Нет открытых позиций</div><div class="empty-sub">Откройте первую сделку или дождитесь сигнала от сканера</div><button class="empty-cta" onclick="switchPage(\'scanner\')">🔍 Перейти в Scanner</button></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Liq%</th><th></th></tr></thead><tbody>'+pos.map(p=>{
    const pnl=p.upnl||0;
    const liqDist=p.liq&&p.mark?Math.abs((p.liq-p.mark)/p.mark*100):0;
    const liqCls=liqDist>50?'positive':liqDist>20?'neutral':'negative';
    return '<tr><td><span class="sym">'+escHtml(p.symbol)+'</span></td><td>'+badge(p.side==='LONG'?'long':'short',p.side)+'</td><td class="price">'+fmtN(p.qty,4)+'</td><td class="price">$'+fmtPx(p.entry)+'</td><td class="price">$'+fmtPx(p.mark)+'</td><td class="'+pnlCls(pnl)+'" style="font-weight:600">'+pnlSign(pnl)+'$'+fmtN(pnl,2)+'</td><td class="'+liqCls+'">'+liqDist.toFixed(0)+'%</td><td><button class="btn btn-sm btn-danger" onclick="closePos(\''+escHtml(p.symbol)+'\',\''+escHtml(p.side)+'\')">Close</button></td></tr>';
  }).join('')+'</tbody></table>';
}

function renderOrders(orders){
  const ord=orders.filter(o=>o.type&&['LIMIT','STOP_LIMIT','TAKE_PROFIT_LIMIT'].includes(String(o.type).toUpperCase()));
  const body=document.getElementById('ordBody');
  document.getElementById('ordCount').textContent=ord.length;
  if(!ord.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">📋</div><div class="empty-title">Нет активных ордеров</div><div class="empty-sub">Лимитные ордера появятся здесь после создания</div></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th>Price</th><th>Qty</th><th></th></tr></thead><tbody>'+ord.map(o=>'<tr><td><span class="sym">'+escHtml(o.symbol)+'</span></td><td>'+badge(o.side==='BUY'?'long':'short',o.side)+'</td><td><span class="badge badge-neutral">'+escHtml(o.type)+'</span></td><td class="price">$'+fmtPx(o.price)+'</td><td class="price">'+fmtN(o.qty,4)+'</td><td><button class="btn btn-sm btn-danger" onclick="cancelOrd(\''+escHtml(o.symbol)+'\','+(parseFloat(o.price)||0)+')">Cancel</button></td></tr>').join('')+'</tbody></table>';
}

function renderMarket(market){
  const body=document.getElementById('marketBody');
  if(!market.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">📈</div><div class="empty-title">Нет данных</div></div>';
    return;
  }
  body.innerHTML=market.slice(0,6).map(m=>{
    const chg=m.chg||0;
    return '<div class="market-row"><span class="market-sym">'+escHtml(m.symbol.replace('USDT',''))+'</span><div style="display:flex;align-items:center;gap:10px"><span class="market-price">$'+fmtPx(m.price)+'</span><span class="market-chg '+chgCls(chg)+'" style="background:'+(chg>=0?'var(--green-dim)':'var(--red-dim)')+'">'+pnlSign(chg)+chg.toFixed(2)+'%</span></div></div>';
  }).join('');
}

function renderSetupsPreview(setups){
  const body=document.getElementById('setBody');
  if(!setups.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">🔍</div><div class="empty-title">Сканер не нашёл сетапов</div><div class="empty-sub">Рынок в боковике — ждём пробоя диапазона</div><button class="empty-cta" onclick="switchPage(\'scanner\')">🔍 Открыть Scanner</button></div>';
    return;
  }
  body.innerHTML='<div class="setup-grid">'+setups.slice(0,6).map(renderSetupCard).join('')+'</div>';
}

function renderActivityPreview(acts){
  const body=document.getElementById('actBody');
  if(!acts.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">📋</div><div class="empty-title">Нет событий</div></div>';
    return;
  }
  body.innerHTML=acts.slice(0,6).map(renderActItem).join('');
}

function renderActItem(a){
  const dotCls=a.level==='critical'?'act-dot-crit':a.level==='warning'?'act-dot-warn':'act-dot-ok';
  const time=a.ts?new Date(a.ts).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):'—';
  return '<div class="act-item"><span class="act-time">'+time+'</span><span class="act-dot '+dotCls+'"></span><div style="flex:1"><div class="act-text">'+escHtml(a.event||'')+'</div>'+(a.detail?'<div class="act-detail">'+escHtml(a.detail)+'</div>':'')+'</div></div>';
}

function badge(cls,text){
  return '<span class="badge badge-'+cls+'">'+escHtml(text||'')+'</span>';
}

function renderSetupCard(s){
  const side=(s.side||'LONG').toLowerCase();
  const scoreCls=s.score>=80?'badge-long':s.score>=70?'badge-info':'badge-neutral';
  const dist=s.dist_to_entry!=null?s.dist_to_entry:0;
  const distEmoji=Math.abs(dist)<=0.2?'🟢':Math.abs(dist)<=1?'🟡':'⚪';
  return '<div class="setup-card '+side+'" onclick="showCoin(\''+escHtml(s.symbol)+'\')"><div class="setup-card-head"><div><div class="setup-sym">'+escHtml(s.short||s.symbol)+'</div><div class="setup-side '+side+'">'+side+'</div></div><span class="badge '+scoreCls+'">'+s.score+'</span></div><div class="setup-prices"><div class="setup-pr-cell"><div class="setup-pr-label">Entry</div><div class="setup-pr-val entry">$'+fmtPx(s.entry_low||s.price)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">TP</div><div class="setup-pr-val tp">$'+fmtPx(s.tp)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">SL</div><div class="setup-pr-val sl">$'+fmtPx(s.sl)+'</div></div></div><div class="setup-foot"><span>R:R <span class="setup-rr">'+fmtN(s.rr,1)+'</span></span><span class="setup-dist">'+distEmoji+' '+pnlSign(dist)+dist.toFixed(1)+'%</span></div></div>';
}

async function showCoin(sym){
  const overlay=document.getElementById('modalOverlay');
  const body=document.getElementById('modalBody');
  const foot=document.getElementById('modalFoot');
  const title=document.getElementById('modalTitle');
  title.textContent=sym.replace('USDT','');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Загрузка...</div>';
  foot.innerHTML='';
  overlay.classList.remove('hidden');
  const d=await api('/coin/'+encodeURIComponent(sym));
  if(d.error||!d){
    body.innerHTML='<div class="empty"><div class="empty-icon">❌</div><div class="empty-title">Ошибка</div><div class="empty-sub">'+escHtml(d.error||'Монета не найдена')+'</div></div>';
    return;
  }
  renderCoinModal(d);
}

function renderCoinModal(d){
  const body=document.getElementById('modalBody');
  const foot=document.getElementById('modalFoot');
  const rsiColor=d.rsi<30?'var(--green)':d.rsi>70?'var(--red)':'var(--text)';
  const momColor=d.mom>=10?'var(--green)':d.mom>=6?'var(--yellow)':'var(--red)';
  const fcColor=({bullish:'var(--green)',bearish:'var(--red)',neutral:'var(--text2)',squeeze:'var(--purple)',dip:'var(--accent)',breakout:'var(--green)'})[d.fc_class]||'var(--text2)';
  const fcIcon=({bullish:'🟢',bearish:'🔴',neutral:'⚪',squeeze:'⚡',dip:'💎',breakout:'🚀'})[d.fc_class]||'●';
  body.innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px"><h2 style="font-size:22px;font-weight:700;font-family:monospace">'+escHtml(d.short||d.symbol)+'</h2>'+(d.side?badge(d.side==='LONG'?'long':'short',d.side):'')+' '+(d.score?'<span class="badge badge-info">Score '+d.score+'</span>':'')+'</div><div class="stat-grid" style="padding:0;margin-bottom:16px"><div class="stat-card"><div class="stat-label">Price</div><div class="stat-value">$'+fmtPx(d.price)+'</div></div><div class="stat-card"><div class="stat-label">24h</div><div class="stat-value '+chgCls(d.chg||0)+'">'+pnlSign(d.chg||0)+(d.chg||0).toFixed(2)+'%</div></div><div class="stat-card"><div class="stat-label">RSI(14)</div><div class="stat-value" style="color:'+rsiColor+'">'+fmtN(d.rsi,1)+'</div></div><div class="stat-card"><div class="stat-label">ATR(1h)</div><div class="stat-value">'+(d.atr_pct||'—')+'%</div></div><div class="stat-card"><div class="stat-label">Vol Ratio</div><div class="stat-value">'+fmtN(d.vol_ratio,2)+'</div></div><div class="stat-card"><div class="stat-label">Mom</div><div class="stat-value" style="color:'+momColor+'">'+(d.mom||0)+'/15</div></div></div>'+(d.sl&&d.tp?'<div class="modal-section"><div class="modal-section-title">📐 Trade Levels</div><div class="stat-grid" style="padding:0"><div class="stat-card"><div class="stat-label">Entry Zone</div><div class="stat-value" style="font-size:16px">$'+fmtPx(d.entry_low||d.price)+'</div><div class="stat-sub">→ $'+fmtPx(d.entry_high||d.price)+'</div></div><div class="stat-card" style="border-left:3px solid var(--green)"><div class="stat-label" style="color:var(--green)">Take Profit</div><div class="stat-value" style="color:var(--green);font-size:16px">$'+fmtPx(d.tp)+'</div><div class="stat-sub">+'+fmtN(d.tp_pct,1)+'%</div></div><div class="stat-card" style="border-left:3px solid var(--red)"><div class="stat-label" style="color:var(--red)">Stop Loss</div><div class="stat-value" style="color:var(--red);font-size:16px">$'+fmtPx(d.sl)+'</div><div class="stat-sub">-'+fmtN(d.sl_pct,1)+'%</div></div></div>'+(d.rr?'<div style="margin-top:8px;text-align:center;font-size:13px;color:var(--text2)">R:R <b style="color:var(--text)">'+fmtN(d.rr,2)+'</b></div>':'')+'</div>':'')+(d.forecast?'<div class="modal-section"><div class="modal-section-title">🔮 Forecast</div><div style="padding:12px 14px;border-radius:8px;background:'+fcColor+'22;border-left:3px solid '+fcColor+';font-size:13px;display:flex;align-items:center;gap:8px"><span style="font-size:18px">'+fcIcon+'</span><span>'+escHtml(d.forecast)+'</span></div></div>':'');
  foot.innerHTML='<button class="btn btn-primary btn-block" onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BINANCE:'+encodeURIComponent((d.symbol||'').replace('USDT',''))+'USDT.P\',\'_blank\')">📊 TradingView</button><button class="btn" onclick="showCoin(\''+escHtml(d.symbol)+'\')">🔄</button>';
}

function closeModal(){
  document.getElementById('modalOverlay').classList.add('hidden');
}

async function loadScanner(){
  const body=document.getElementById('scanBody');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Сканирование...</div>';
  const setups=await api('/setups');
  filteredSetups=setups||[];
  document.getElementById('scanStats').textContent=filteredSetups.length+' сетапов';
  if(!filteredSetups.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">🔍</div><div class="empty-title">Нет квалифицированных сетапов</div><div class="empty-sub">Все кандидаты не прошли фильтры. Рынок в боковике.</div><button class="empty-cta" onclick="loadScanner()">🔄 Повторить</button></div>';
    return;
  }
  body.innerHTML='<div class="setup-grid">'+filteredSetups.map(renderSetupCard).join('')+'</div>';
}

async function loadActivity(){
  const body=document.getElementById('actBodyFull');
  body.innerHTML='<div class="loading-state"><div class="spinner"></div>Загрузка...</div>';
  const acts=await api('/activity');
  const list=Array.isArray(acts)?acts:[];
  document.getElementById('actCount').textContent=list.length;
  if(!list.length){
    body.innerHTML='<div class="empty"><div class="empty-icon">📋</div><div class="empty-title">Журнал пуст</div><div class="empty-sub">Здесь появятся все действия системы</div></div>';
    return;
  }
  body.innerHTML=list.map(renderActItem).join('');
}

async function ctrlAction(action){
  if(action==='kill'){
    if(!confirm('⚠️ EMERGENCY KILL\\n\\nОтключить всю торговлю?\\nЭто экстренная остановка.'))return;
    if(!confirm('Точно включить KILL? Бот перестанет торговать.'))return;
  }else if(action==='pause'){
    if(!confirm('⏸ Поставить на паузу? Новые ордера не будут выставляться.'))return;
  }else if(action==='set_auto'){
    if(!confirm('⚡ Переключить в AUTO? Автоторговля возобновится.'))return;
  }else if(action==='set_manual'){
    if(!confirm('✋ Переключить в MANUAL? Автоторговля остановится.'))return;
  }
  try{
    const r=await fetch(API+'/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    const d=await r.json();
    if(d.error){toast('⚠ '+d.error,'err')}
    else{toast(d.msg||'OK','ok');loadDashboard()}
  }catch(e){toast('Ошибка сети','err')}
}

async function closePos(sym,side){
  if(!confirm('Закрыть '+sym+' '+side+'?'))return;
  try{
    const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'close',symbol:sym,side})});
    const d=await r.json();
    toast(d.error?'⚠ '+d.error:'Закрыто',d.error?'err':'ok');
    loadDashboard();
  }catch(e){toast('Ошибка сети','err')}
}
async function cancelOrd(sym,price){
  if(!confirm('Отменить '+sym+' лимит @ '+price+'?'))return;
  try{
    const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'cancel',symbol:sym,price})});
    const d=await r.json();
    toast(d.error?'⚠ '+d.error:'Отменено',d.error?'err':'ok');
    loadDashboard();
  }catch(e){toast('Ошибка сети','err')}
}

function refreshNow(){
  loadDashboard();
  if(document.getElementById('page-scanner').classList.contains('active'))loadScanner();
  if(document.getElementById('page-activity').classList.contains('active'))loadActivity();
}

function onGlobalSearch(q){
  const dd=document.getElementById('searchDd');
  if(!q||q.length<1){dd.classList.remove('show');dd.innerHTML='';return;}
  const upper=q.toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(!upper){dd.classList.remove('show');dd.innerHTML='';return;}
  const matches=CANDIDATES.filter(s=>s.includes(upper)).slice(0,8);
  if(!matches.length){
    dd.innerHTML='<div class="search-item" style="color:var(--text3);cursor:default"><span>Ничего не найдено</span></div>';
  }else{
    dd.innerHTML=matches.map(s=>'<div class="search-item" onclick="selectCoin(\''+s+'\')"><span class="sym-name">'+s.replace('USDT','')+'</span><span class="sym-type">USDT PERP</span></div>').join('');
  }
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
document.addEventListener('click',e=>{
  if(!e.target.closest('.search-top')){
    const dd=document.getElementById('searchDd');
    if(dd)dd.classList.remove('show');
  }
});

document.addEventListener('keydown',e=>{
  if(e.key==='Escape')closeModal();
  if(e.key==='/'&&!e.target.matches('input,textarea')){
    e.preventDefault();
    document.getElementById('globalSearch').focus();
  }
  if(e.key==='r'&&!e.target.matches('input,textarea')&&!e.ctrlKey&&!e.metaKey){
    refreshNow();
  }
});

async function init(){
  const meta=await api('');
  if(meta&&!meta.error){
    CANDIDATES=meta.candidates||[];
    renderDashboard(meta);
    renderSetupsPreview(meta.setups||[]);
    renderActivityPreview(meta.activity||[]);
  }else{
    showError('Не удалось подключиться к API. Проверьте, что сервис запущен.');
  }
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
                    result['msg'] = r.get('msg', 'Closed')
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
