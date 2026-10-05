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
<title>TradingOS — Панель управления</title>
<style>
:root{
  --bg:#0b0e14;--bg2:#10141c;--bg3:#161b25;--bg4:#1c2230;
  --border:#1f2530;--border-light:#2a313e;--border-strong:#353c4a;
  --text:#d4dae3;--text2:#7a8494;--text3:#4f5867;
  --accent:#3b82f6;--accent-dim:rgba(59,130,246,.08);
  --green:#00c896;--green-dim:rgba(0,200,150,.06);
  --red:#ff3b5c;--red-dim:rgba(255,59,92,.06);
  --yellow:#ffb020;--yellow-dim:rgba(255,176,32,.06);
  --radius:3px;
}
.light{
  --bg:#f4f5f7;--bg2:#ffffff;--bg3:#f9fafb;--bg4:#eceef2;
  --border:#d9dde3;--border-light:#c4c9d1;--border-strong:#aab0ba;
  --text:#0e1217;--text2:#5b6473;--text3:#8b95a3;
  --accent:#0969d9;--accent-dim:rgba(9,105,217,.06);
  --green:#0a8c66;--green-dim:rgba(10,140,102,.04);
  --red:#cf1124;--red-dim:rgba(207,17,36,.04);
  --yellow:#a85d00;--yellow-dim:rgba(168,93,0,.04);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
body{background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;line-height:1.4;overflow-x:hidden;font-feature-settings:'cv11','ss01'}
a{color:var(--accent);text-decoration:none}
code,.mono{font-family:'JetBrains Mono','SF Mono',ui-monospace,'Roboto Mono',Menlo,monospace;font-variant-numeric:tabular-nums;font-feature-settings:'tnum','zero'}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border-light)}
::-webkit-scrollbar-thumb:hover{background:var(--border-strong)}

/* ── App shell ── */
.app{display:grid;grid-template-rows:44px 1fr;min-height:100vh}
.topbar{display:flex;align-items:center;gap:14px;padding:0 16px;height:44px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50}
.brand{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--text)}
.brand-tag{color:var(--text3);font-weight:400;font-size:11px;letter-spacing:0}
.dot{width:6px;height:6px;display:inline-block;flex-shrink:0}
.dot-ok{background:var(--green);box-shadow:0 0 6px rgba(0,200,150,.5)}
.dot-warn{background:var(--yellow)}
.dot-crit{background:var(--red);box-shadow:0 0 6px rgba(255,59,92,.5);animation:blink 1.2s infinite}
.dot-off{background:var(--text3)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

/* ── Nav (terminal-style tabs) ── */
.nav{display:flex;gap:0;height:100%}
.nav-link{height:100%;padding:0 16px;display:flex;align-items:center;gap:6px;color:var(--text2);font-size:12px;font-weight:500;cursor:pointer;border-right:1px solid var(--border);transition:background .1s}
.nav-link:hover{background:var(--bg3);color:var(--text)}
.nav-link.active{background:var(--bg3);color:var(--text);box-shadow:inset 0 -2px 0 var(--accent)}
.nav-link .badge-count{background:var(--accent);color:#fff;font-size:10px;padding:1px 5px;font-weight:600;min-width:16px;text-align:center}
.nav-icon{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.6}

.top-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.search-top{position:relative;width:240px}
.search-input{width:100%;height:28px;padding:0 8px 0 26px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:12px;outline:none;transition:border-color .1s;font-family:inherit}
.search-input:focus{border-color:var(--accent)}
.search-input::placeholder{color:var(--text3)}
.search-icon{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--text3);pointer-events:none;width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:1.8}
.search-dd{position:absolute;top:calc(100% + 1px);left:0;right:0;background:var(--bg2);border:1px solid var(--border-strong);max-height:280px;overflow-y:auto;z-index:200;display:none;box-shadow:0 8px 24px rgba(0,0,0,.3)}
.search-dd.show{display:block}
.search-item{padding:6px 10px;cursor:pointer;font-size:12px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);transition:background .1s}
.search-item:last-child{border-bottom:none}
.search-item:hover{background:var(--bg3)}
.search-item .sym-name{font-weight:600;font-family:'JetBrains Mono',monospace}
.search-item .sym-type{font-size:10px;color:var(--text3);font-family:'JetBrains Mono',monospace}

.mode-tag{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;border:1px solid;font-family:'JetBrains Mono',monospace}
.mode-auto{color:var(--green);border-color:var(--green);background:var(--green-dim)}
.mode-manual{color:var(--accent);border-color:var(--accent);background:var(--accent-dim)}
.mode-paused{color:var(--yellow);border-color:var(--yellow);background:var(--yellow-dim)}
.mode-kill{color:var(--red);border-color:var(--red);background:var(--red-dim);animation:blink 1.2s infinite}
.mode-off{color:var(--text3);border-color:var(--border)}
.clock{color:var(--text2);font-size:11px;font-family:'JetBrains Mono',monospace;letter-spacing:.05em}
.icon-btn{width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;background:transparent;border:1px solid var(--border);color:var(--text2);cursor:pointer;transition:all .1s}
.icon-btn:hover{color:var(--text);border-color:var(--border-light);background:var(--bg3)}
.icon-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.6}

/* ── Main ── */
.main{padding:0;width:100%;background:var(--bg)}
.page{display:none}
.page.active{display:block}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}

/* ── Panel (data block) ── */
.panel{background:var(--bg2);border:1px solid var(--border);overflow:hidden}
.panel-hd{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid var(--border);background:linear-gradient(180deg,rgba(255,255,255,.02) 0%,transparent 100%);min-height:34px}
.panel-title{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.1em;display:flex;align-items:center;gap:6px}
.panel-title-icon{width:12px;height:12px;stroke:var(--text2);fill:none;stroke-width:1.8}
.panel-body{padding:0}
.panel-body.no-pad{padding:0}
.panel-count{font-size:10px;color:var(--text3);font-family:'JetBrains Mono',monospace}
.panel-tag{font-size:9px;padding:1px 5px;background:var(--bg4);color:var(--text2);text-transform:uppercase;letter-spacing:.08em}

/* ── Stat cards (top strip) ── */
.stat-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));background:var(--bg2);border-bottom:1px solid var(--border)}
.stat{display:flex;flex-direction:column;padding:12px 16px;border-right:1px solid var(--border);position:relative;min-height:68px;transition:all .18s}
.stat:hover{background:var(--bg3)}
.stat:last-child{border-right:none}
.stat::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,var(--accent-dim),transparent);opacity:0;transition:opacity .2s;pointer-events:none}
.stat:hover::before{opacity:1}
.stat.green::before{background:linear-gradient(135deg,var(--green-dim),transparent)}
.stat.red::before{background:linear-gradient(135deg,var(--red-dim),transparent)}
.stat-label{font-size:9px;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-bottom:3px}
.stat-value{font-size:22px;font-weight:700;line-height:1.1;letter-spacing:-.02em}
.stat-sub{font-size:10px;color:var(--text2);margin-top:2px;display:flex;align-items:center;gap:6px;font-family:'JetBrains Mono',monospace}
.stat-spark{margin-top:4px;height:14px;opacity:.7}
.stat-spark svg{width:100%;height:100%}
.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:var(--text2)}

/* ── Control bar ── */
.ctrl{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0;align-items:stretch;height:34px}
.seg{display:flex;height:100%}
.seg-btn{height:100%;padding:0 12px;background:transparent;border:none;border-right:1px solid var(--border);color:var(--text2);font-size:11px;font-weight:500;cursor:pointer;transition:all .1s;font-family:inherit;display:flex;align-items:center;gap:5px;text-transform:uppercase;letter-spacing:.05em}
.seg-btn:hover{color:var(--text);background:var(--bg3)}
.seg-btn.active{background:var(--bg3);color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent)}
.seg-btn.danger.active{color:var(--red);box-shadow:inset 0 -2px 0 var(--red)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:100%;padding:0 12px;border:none;border-right:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;font-size:11px;font-weight:500;transition:all .1s;font-family:inherit;white-space:nowrap;text-transform:uppercase;letter-spacing:.05em}
.btn:hover{background:var(--bg3);color:var(--text);box-shadow:0 0 12px var(--accent-dim)}
.btn-primary{color:var(--accent)}
.btn-primary:hover{color:var(--text);background:var(--bg3)}
.btn-danger{color:var(--red)}
.btn-danger:hover{background:var(--bg3);color:var(--red)}
.btn-spacer{flex:1;border-right:1px solid var(--border)}
.btn-sm{padding:0 8px;font-size:10px}

/* ── Tables (terminal style) ── */
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{text-align:left;padding:6px 10px;color:var(--text3);font-weight:500;font-size:10px;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--border);background:var(--bg3);position:sticky;top:0;z-index:1;white-space:nowrap}
thead th.r{text-align:right}
thead th.c{text-align:center}
tbody td{padding:5px 10px;border-bottom:1px solid var(--border);vertical-align:middle;height:30px}
tbody td.r{text-align:right}
tbody td.c{text-align:center}
tbody tr:last-child td{border-bottom:none}
tbody tr{transition:background .08s}
tbody tr:hover{background:var(--bg3)}
.sym{font-weight:600;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:-.01em}
.num{font-family:'JetBrains Mono',monospace;font-variant-numeric:tabular-nums;font-feature-settings:'tnum','zero'}

/* ── Side tags (replace rounded pills) ── */
.tag{display:inline-flex;align-items:center;gap:3px;padding:1px 5px;font-size:9px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;font-family:'JetBrains Mono',monospace;border:1px solid}
.tag-long{color:var(--green);border-color:var(--green);background:var(--green-dim)}
.tag-short{color:var(--red);border-color:var(--red);background:var(--red-dim)}
.tag-buy{color:var(--green);border-color:var(--green);background:var(--green-dim)}
.tag-sell{color:var(--red);border-color:var(--red);background:var(--red-dim)}
.tag-warn{color:var(--yellow);border-color:var(--yellow);background:var(--yellow-dim)}
.tag-info{color:var(--accent);border-color:var(--accent);background:var(--accent-dim)}
.tag-muted{color:var(--text2);border-color:var(--border);background:transparent}
.tag-dot{width:6px;height:6px;display:inline-block;background:currentColor}

/* ── Empty state (compact) ── */
.empty{padding:16px 12px;text-align:center;color:var(--text3);font-size:11px;display:flex;align-items:center;justify-content:center;gap:6px}
.empty svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.5;opacity:.5}
.empty-cta{color:var(--accent);cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:500;padding:2px 6px;border:1px solid var(--accent);transition:all .1s;margin-left:6px;font-family:inherit;background:transparent}
.empty-cta:hover{background:var(--accent);color:#fff}

/* ── Activity feed ── */
.act-list{max-height:280px;overflow-y:auto}
.act-item{display:grid;grid-template-columns:46px 1fr auto;gap:8px;padding:5px 10px;border-bottom:1px solid var(--border);font-size:11px;align-items:center;transition:background .08s}
.act-item:last-child{border-bottom:none}
.act-item:hover{background:var(--bg3)}
.act-time{color:var(--text3);font-family:'JetBrains Mono',monospace;font-size:10px}
.act-dot{width:5px;height:5px;display:inline-block;margin-right:5px;background:var(--text3)}
.act-dot-ok{background:var(--green)}.act-dot-warn{background:var(--yellow)}.act-dot-crit{background:var(--red)}
.act-text{color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.act-detail{color:var(--text3);font-size:10px;font-family:'JetBrains Mono',monospace}

/* ── Setup cards (compact data blocks) ── */
.setup-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1px;background:var(--border);padding:0}
.setup-card{background:var(--bg2);padding:8px 10px;cursor:pointer;transition:background .1s;position:relative;display:flex;flex-direction:column;gap:5px}
.setup-card:hover{background:var(--bg3)}
.setup-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--border-light)}
.setup-card.long::before{background:var(--green)}
.setup-card.short::before{background:var(--red)}
.setup-head{display:flex;justify-content:space-between;align-items:center}
.setup-sym{font-size:13px;font-weight:600;font-family:'JetBrains Mono',monospace;letter-spacing:-.01em}
.setup-side{font-size:9px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;margin-top:1px}
.setup-side.long{color:var(--green)}
.setup-side.short{color:var(--red)}
.setup-prices{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;padding:5px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);background:var(--bg)}
.setup-pr-cell{text-align:center;padding:0 4px;border-right:1px solid var(--border)}
.setup-pr-cell:last-child{border-right:none}
.setup-pr-label{font-size:8px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:1px;font-weight:600}
.setup-pr-val{font-size:11px;font-weight:600;font-family:'JetBrains Mono',monospace}
.setup-pr-val.entry{color:var(--text)}
.setup-pr-val.tp{color:var(--green)}
.setup-pr-val.sl{color:var(--red)}
.setup-foot{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--text2);font-family:'JetBrains Mono',monospace}
.setup-rr{color:var(--text);font-weight:600}
.setup-dist{display:flex;align-items:center;gap:2px}

/* ── Market row (terminal tick) ── */
.market-row{display:grid;grid-template-columns:1fr auto auto;gap:8px;padding:4px 10px;border-bottom:1px solid var(--border);font-size:11px;align-items:center;transition:background .08s}
.market-row:last-child{border-bottom:none}
.market-row:hover{background:var(--bg3)}
.market-sym{font-weight:600;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:-.01em}
.market-price{font-family:'JetBrains Mono',monospace;font-variant-numeric:tabular-nums;font-size:12px}
.market-chg{font-size:10px;font-weight:600;padding:1px 4px;font-family:'JetBrains Mono',monospace;min-width:54px;text-align:right}

/* ── Layouts ── */
.dash-grid{display:grid;gap:0;grid-template-columns:1fr;margin-top:1px}
@media(min-width:1100px){.dash-grid{grid-template-columns:1fr 320px}}
.sidebar{display:flex;flex-direction:column;gap:1px;background:var(--border);border-top:1px solid var(--border)}
.col{display:flex;flex-direction:column;gap:1px;background:var(--border);border-top:1px solid var(--border)}

/* ── Modal ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(2px);animation:fadeIn .1s}
.modal-overlay.hidden{display:none}
.modal{background:var(--bg2);border:1px solid var(--border-strong);width:100%;max-width:520px;max-height:90vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:var(--shadow-lg),0 0 0 1px rgba(255,255,255,.03) inset}
.modal-hd{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg3)}
.modal-title{font-size:14px;font-weight:600;font-family:'JetBrains Mono',monospace;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}
.modal-body{padding:14px;overflow-y:auto;flex:1}
.modal-section{margin-bottom:14px}
.modal-section-title{font-size:9px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;display:flex;align-items:center;gap:6px}
.modal-foot{display:flex;gap:0;padding:0;border-top:1px solid var(--border);background:var(--bg3);height:36px}
.modal-foot .btn{border-right:1px solid var(--border)}

/* ── Toast ── */
.toast{position:fixed;bottom:16px;right:16px;padding:8px 12px;font-size:12px;z-index:300;animation:slideIn .15s ease;font-weight:500;display:flex;align-items:center;gap:8px;max-width:380px;box-shadow:0 8px 24px rgba(0,0,0,.4);font-family:'JetBrains Mono',monospace}
.toast-ok{background:var(--green);color:#000}
.toast-err{background:var(--red);color:#fff}
.toast-info{background:var(--accent);color:#fff}
.toast-warn{background:var(--yellow);color:#000}
@keyframes slideIn{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}

/* ── Misc ── */
.loading-state{padding:14px;display:flex;align-items:center;justify-content:center;gap:8px;color:var(--text3);font-size:11px}
.spinner{width:12px;height:12px;border:1.5px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.error-banner{background:var(--red-dim);border-left:3px solid var(--red);color:var(--red);padding:8px 12px;font-size:12px;margin:8px 12px 0;display:flex;align-items:center;gap:8px;font-family:'JetBrains Mono',monospace}

/* ── Icon helpers ── */
.ico{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:1.8;flex-shrink:0;display:inline-block;vertical-align:middle}

/* ── Mobile ── */
@media(max-width:768px){
  .topbar{padding:0 8px;gap:6px;height:40px}
  .nav-link{padding:0 10px;font-size:11px}
  .nav-link span:not(.badge-count){display:none}
  .top-right{gap:4px}
  .clock{display:none}
  .search-top{width:140px}
  .stat-strip{grid-template-columns:1fr 1fr}
  .stat{border-right:1px solid var(--border);border-bottom:1px solid var(--border)}
  .stat:nth-child(2n){border-right:none}
  .stat-value{font-size:16px}
  .dash-grid{grid-template-columns:1fr}
  thead th,tbody td{padding:4px 8px;font-size:11px;height:26px}
  .setup-grid{grid-template-columns:1fr}
  .brand-tag{display:none}
}
</style>
</head>
<body>
<div class="app">

<header class="topbar">
  <div class="brand">
    <span class="dot dot-off" id="statusDot"></span>
    <span>TradingOS</span>
    <span class="brand-tag">Terminal</span>
  </div>
  <nav class="nav">
    <div class="nav-link active" data-page="dashboard" onclick="switchPage('dashboard')">
      <svg class="nav-icon" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
      <span>Дашборд</span>
    </div>
    <div class="nav-link" data-page="scanner" onclick="switchPage('scanner')">
      <svg class="nav-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <span>Сканер</span>
      <span class="badge-count" id="navSetupsCount" style="display:none">0</span>
    </div>
    <div class="nav-link" data-page="activity" onclick="switchPage('activity')">
      <svg class="nav-icon" viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
      <span>События</span>
    </div>
  </nav>
  <div class="top-right">
    <div class="search-top">
      <svg class="search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input class="search-input mono" id="globalSearch" placeholder="ПОИСК МОНЕТЫ [/]..." oninput="onGlobalSearch(this.value)" onkeydown="if(event.key==='Enter')doGlobalSearch()" onfocus="onGlobalSearch(this.value)">
      <div class="search-dd" id="searchDd"></div>
    </div>
    <span class="mode-tag mode-off" id="modePill">—</span>
    <span class="clock mono" id="clock">--:--:--</span>
    <button class="icon-btn" id="themeBtn" onclick="toggleTheme()" title="Тема">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
    </button>
  </div>
</header>

<main class="main">

<div class="page active" id="page-dashboard">

  <!-- Stat Strip (Bloomberg-style top row) -->
  <div class="stat-strip" id="statStrip">
    <div class="stat">
      <div class="stat-label">СТАТУС</div>
      <div class="stat-value" id="heroStatus">—</div>
      <div class="stat-sub"><span id="heroMode">—</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">БАЛАНС</div>
      <div class="stat-value mono" id="heroEquity">—</div>
      <div class="stat-sub">ДОСТУПНО: <span class="mono" id="heroAvailable">—</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">ИСПОЛЬЗОВАНО</div>
      <div class="stat-value mono" id="heroPnl">—</div>
      <div class="stat-sub">ЗАМРОЖЕНО В ПОЗИЦИЯХ</div>
    </div>
    <div class="stat">
      <div class="stat-label">BTCUSDT</div>
      <div class="stat-value mono" id="heroBtc">—</div>
      <div class="stat-sub"><span class="mono" id="heroBtcChg">—</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">СЕТАПЫ</div>
      <div class="stat-value mono" id="heroSetups">—</div>
      <div class="stat-sub">СИГНАЛОВ СКАНЕРА</div>
    </div>
    <div class="stat" style="min-width:180px">
      <div class="stat-label">ОБНОВЛЕНИЕ</div>
      <div class="stat-value mono" id="lastUpdate" style="font-size:14px;margin-top:4px">—</div>
      <div class="stat-sub"><span style="color:var(--text3)">КЛАВИША [R]</span></div>
    </div>
  </div>

  <!-- Control bar -->
  <div class="ctrl" id="ctrlBar">
    <div class="seg" id="modeSeg">
      <button class="seg-btn" data-mode="AUTO" onclick="ctrlAction('set_auto')">
        <span class="tag-dot" style="background:var(--green)"></span>AUTO
      </button>
      <button class="seg-btn" data-mode="MANUAL" onclick="ctrlAction('set_manual')">
        <span class="tag-dot" style="background:var(--accent)"></span>MANUAL
      </button>
      <button class="seg-btn danger" data-mode="PAUSE" onclick="ctrlAction('pause')">
        <span class="tag-dot" style="background:var(--yellow)"></span>ПАУЗА
      </button>
    </div>
    <button class="btn btn-primary" onclick="refreshNow()" title="Обновить [R]">
      <svg class="ico" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
      ОБНОВИТЬ
    </button>
    <div class="btn-spacer"></div>
    <button class="btn btn-danger" onclick="ctrlAction('kill')">
      <svg class="ico" viewBox="0 0 24 24"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      KILL SWITCH
    </button>
  </div>

  <div class="dash-grid">
    <div class="col">
      <!-- Open Positions -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg class="panel-title-icon" viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
            ОТКРЫТЫЕ ПОЗИЦИИ
          </div>
          <span class="panel-count mono" id="posCount">0</span>
        </div>
        <div class="panel-body no-pad" id="posWrap"><div id="posBody"></div></div>
      </div>
      <!-- Open Orders -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg class="panel-title-icon" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
            ОТКРЫТЫЕ ОРДЕРА
          </div>
          <span class="panel-count mono" id="ordCount">0</span>
        </div>
        <div class="panel-body no-pad" id="ordWrap"><div id="ordBody"></div></div>
      </div>
    </div>

    <div class="sidebar">
      <!-- Market -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg class="panel-title-icon" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
            РЫНОК
          </div>
        </div>
        <div class="panel-body no-pad" id="marketBody"></div>
      </div>
      <!-- Top Setups -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg class="panel-title-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
            ТОП СЕТАПЫ
          </div>
          <span class="panel-count mono" id="setCount">0</span>
        </div>
        <div class="panel-body no-pad"><div id="setBody"></div></div>
      </div>
      <!-- Recent Activity -->
      <div class="panel">
        <div class="panel-hd">
          <div class="panel-title">
            <svg class="panel-title-icon" viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            СОБЫТИЯ
          </div>
        </div>
        <div class="panel-body no-pad">
          <div class="act-list" id="actBody"></div>
        </div>
      </div>
    </div>
  </div>
</div>
        </div>
        <div class="panel-body no-pad"><div id="setBody"></div></div>
      </div>
      <div class="panel">
        <div class="panel-hd"><div class="panel-title"><span class="panel-title-icon">📋</span> Последние события</div></div>
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
      <div class="panel-title"><span class="panel-title-icon">🔍</span> Сканер — все сетапы</div>
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
      <div class="panel-title"><span class="panel-title-icon">📋</span> Журнал событий</div>
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
      <span class="modal-title" id="modalTitle">Детали</span>
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
  document.getElementById('heroMode').textContent=(ctl.mode||'—')+' · '+(ctl.orders_disabled?'ОРДЕРА ВЫКЛ':'ОРДЕРА ВКЛ');
  const m=d.money||{};
  document.getElementById('heroEquity').textContent=fmtPx(m.equity);
  document.getElementById('heroAvailable').textContent=fmtPx(m.available);
  const pnlEl=document.getElementById('heroPnl');
  const used=m.used_margin||0;
  pnlEl.textContent='$'+fmtPx(used);
  pnlEl.className='stat-value mono neutral';
  const riskEl=document.getElementById('heroRisk');
  if(riskEl) riskEl.textContent='$'+fmtPx(m.risk);
  const btc=(d.market||[])[0]||{};
  document.getElementById('heroBtc').textContent=fmtPx(btc.price);
  const btcChgEl=document.getElementById('heroBtcChg');
  btcChgEl.textContent=pnlSign(btc.chg||0)+((btc.chg||0).toFixed(2))+'%';
  btcChgEl.className='mono '+chgCls(btc.chg||0);
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
  mp.className='mode-tag '+
    (ctl.kill_switch?'mode-kill':ctl.paused?'mode-paused':ctl.mode==='AUTO'?'mode-auto':ctl.mode==='MANUAL'?'mode-manual':'mode-off');
  mp.textContent=
    ctl.kill_switch?'KILL':ctl.paused?'PAUSED':ctl.mode==='AUTO'?'AUTO':ctl.mode==='MANUAL'?'MANUAL':'OFFLINE';
  renderPositions(d.positions||[]);
  renderOrders(d.orders||[]);
  renderMarket(d.market||[]);
  renderActivityPreview(d.activity||[]);
  document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString('ru-RU');
}

function renderPositions(pos){
  const body=document.getElementById('posBody');
  document.getElementById('posCount').textContent=pos.length;
  if(!pos.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg><span>НЕТ ОТКРЫТЫХ ПОЗИЦИЙ</span><button class="empty-cta" onclick="switchPage(\'scanner\')">СКАНИРОВАТЬ</button></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>МОНЕТА</th><th>СТОРОНА</th><th class="r">РАЗМЕР</th><th class="r">ВХОД</th><th class="r">ТЕКУЩАЯ</th><th class="r">PNL</th><th class="r">ЛИК%</th><th class="c"></th></tr></thead><tbody>'+pos.map(p=>{
    const pnl=p.upnl||0;
    const liqDist=p.liq&&p.mark?Math.abs((p.liq-p.mark)/p.mark*100):0;
    const liqCls=liqDist>50?'positive':liqDist>20?'neutral':'negative';
    const side=p.side==='LONG'?'long':'short';
    return '<tr><td><span class="sym">'+escHtml(p.symbol)+'</span></td><td><span class="tag tag-'+side+'">'+side.toUpperCase()+'</span></td><td class="num r">'+fmtN(p.qty,4)+'</td><td class="num r">'+fmtPx(p.entry)+'</td><td class="num r">'+fmtPx(p.mark)+'</td><td class="num r '+pnlCls(pnl)+'" style="font-weight:600">'+pnlSign(pnl)+fmtN(pnl,2)+'</td><td class="num r '+liqCls+'">'+liqDist.toFixed(0)+'%</td><td class="c"><button class="icon-btn" title="Закрыть позицию" onclick="closePos(\''+escHtml(p.symbol)+'\',\''+escHtml(p.side)+'\')" style="color:var(--red)"><svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>';
  }).join('')+'</tbody></table>';
}

function renderOrders(orders){
  const ord=orders.filter(o=>o.type&&['LIMIT','STOP_LIMIT','TAKE_PROFIT_LIMIT'].includes(String(o.type).toUpperCase()));
  const body=document.getElementById('ordBody');
  document.getElementById('ordCount').textContent=ord.length;
  if(!ord.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg><span>НЕТ АКТИВНЫХ ОРДЕРОВ</span></div>';
    return;
  }
  body.innerHTML='<table><thead><tr><th>МОНЕТА</th><th>СТОРОНА</th><th>ТИП</th><th class="r">ЦЕНА</th><th class="r">КОЛ-ВО</th><th class="c"></th></tr></thead><tbody>'+ord.map(o=>{
    const side=o.side==='BUY'?'buy':'sell';
    return '<tr><td><span class="sym">'+escHtml(o.symbol)+'</span></td><td><span class="tag tag-'+side+'">'+side.toUpperCase()+'</span></td><td><span class="tag tag-muted">'+escHtml(o.type)+'</span></td><td class="num r">'+fmtPx(o.price)+'</td><td class="num r">'+fmtN(o.qty,4)+'</td><td class="c"><button class="icon-btn" title="Отменить ордер" onclick="cancelOrd(\''+escHtml(o.symbol)+'\','+(parseFloat(o.price)||0)+')" style="color:var(--text3)"><svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>';
  }).join('')+'</tbody></table>';
}

function renderMarket(market){
  const body=document.getElementById('marketBody');
  if(!market.length){
    body.innerHTML='<div class="empty"><span>НЕТ ДАННЫХ</span></div>';
    return;
  }
  body.innerHTML=market.slice(0,6).map(m=>{
    const chg=m.chg||0;
    const chgStyle=chg>=0?'color:var(--green)':'color:var(--red)';
    return '<div class="market-row"><span class="market-sym">'+escHtml(m.symbol.replace('USDT',''))+'</span><span class="market-price num">'+fmtPx(m.price)+'</span><span class="market-chg num" style="'+chgStyle+'">'+pnlSign(chg)+chg.toFixed(2)+'%</span></div>';
  }).join('');
}

function renderSetupsPreview(setups){
  const body=document.getElementById('setBody');
  if(!setups.length){
    body.innerHTML='<div class="empty"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span>СЕТАПОВ НЕТ — РЫНОК ВО ФЛЭТЕ</span></div>';
    return;
  }
  body.innerHTML='<div class="setup-grid">'+setups.slice(0,6).map(renderSetupCard).join('')+'</div>';
}

function renderActivityPreview(acts){
  const body=document.getElementById('actBody');
  if(!acts.length){
    body.innerHTML='<div class="empty"><span>ЖУРНАЛ ПУСТ</span></div>';
    return;
  }
  body.innerHTML=acts.slice(0,6).map(renderActItem).join('');
}

function renderActItem(a){
  const dotCls=a.level==='critical'?'act-dot-crit':a.level==='warning'?'act-dot-warn':'act-dot-ok';
  const time=a.ts?new Date(a.ts).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  return '<div class="act-item"><span class="act-time">'+time+'</span><span class="act-text"><span class="act-dot '+dotCls+'"></span>'+escHtml(a.event||'')+'</span><span class="act-detail">'+(a.detail?escHtml(a.detail):'')+'</span></div>';
}

function badge(cls,text){
  return '<span class="badge badge-'+cls+'">'+escHtml(text||'')+'</span>';
}

function renderSetupCard(s){
  const side=(s.side||'LONG').toLowerCase();
  const dist=s.dist_to_entry!=null?s.dist_to_entry:0;
  return '<div class="setup-card '+side+'" onclick="showCoin(\''+escHtml(s.symbol)+'\')"><div class="setup-head"><div><span class="setup-sym">'+escHtml(s.short||s.symbol)+'</span> <span class="setup-side '+side+'">'+side.toUpperCase()+'</span></div><span class="tag tag-info">SC '+s.score+'</span></div><div class="setup-prices"><div class="setup-pr-cell"><div class="setup-pr-label">ВХОД</div><div class="setup-pr-val entry num">'+fmtPx(s.entry_low||s.price)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">ТЕЙК</div><div class="setup-pr-val tp num">'+fmtPx(s.tp)+'</div></div><div class="setup-pr-cell"><div class="setup-pr-label">СТОП</div><div class="setup-pr-val sl num">'+fmtPx(s.sl)+'</div></div></div><div class="setup-foot"><span>R:R <span class="setup-rr num">'+fmtN(s.rr,1)+'</span></span><span class="setup-dist num">'+pnlSign(dist)+dist.toFixed(1)+'%</span></div></div>';
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
  const side=d.side?d.side.toLowerCase():'';
  body.innerHTML='<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px"><div style="display:flex;align-items:center;gap:6px"><span class="sym" style="font-size:16px">'+escHtml(d.short||d.symbol)+'</span>'+(side?'<span class="tag tag-'+side+'">'+side.toUpperCase()+'</span>':'')+'</div>'+(d.score?'<span class="tag tag-info">SC '+d.score+'</span>':'')+'</div><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);margin-bottom:10px"><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">ЦЕНА</div><div class="num" style="font-size:13px;font-weight:600">'+fmtPx(d.price)+'</div></div><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">24Ч</div><div class="num" style="font-size:13px;font-weight:600;'+(d.chg>=0?'color:var(--green)':'color:var(--red)')+'">'+pnlSign(d.chg||0)+(d.chg||0).toFixed(2)+'%</div></div><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">RSI(14)</div><div class="num" style="font-size:13px;font-weight:600;color:'+rsiColor+'">'+fmtN(d.rsi,1)+'</div></div><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">ATR(1Ч)</div><div class="num" style="font-size:13px;font-weight:600">'+(d.atr_pct||'—')+'%</div></div><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">ОБЪЁМ</div><div class="num" style="font-size:13px;font-weight:600">'+fmtN(d.vol_ratio,2)+'x</div></div><div style="background:var(--bg3);padding:4px 8px"><div class="stat-label">МОМЕНТУМ</div><div class="num" style="font-size:13px;font-weight:600;color:'+momColor+'">'+(d.mom||0)+'/15</div></div></div>'+(d.sl&&d.tp?'<div class="modal-section"><div class="modal-section-title">УРОВНИ</div><table style="margin-bottom:2px"><thead><tr><th>ТИП</th><th class="r">ЦЕНА</th><th class="r">ДЕЛЬТА</th></tr></thead><tbody><tr><td>ВХОД</td><td class="num r">'+fmtPx(d.entry_low||d.price)+'</td><td class="num r" style="color:var(--text3)">—</td></tr><tr><td style="color:var(--green)">ТЕЙК</td><td class="num r" style="color:var(--green)">'+fmtPx(d.tp)+'</td><td class="num r" style="color:var(--green)">+'+fmtN(d.tp_pct,1)+'%</td></tr><tr><td style="color:var(--red)">СТОП</td><td class="num r" style="color:var(--red)">'+fmtPx(d.sl)+'</td><td class="num r" style="color:var(--red)">-'+fmtN(d.sl_pct,1)+'%</td></tr></tbody></table>'+(d.rr?'<div style="font-size:10px;color:var(--text3);text-align:right">R:R <b class="num" style="color:var(--text)">'+fmtN(d.rr,2)+'</b></div>':'')+'</div>':'')+(d.forecast?'<div class="modal-section"><div class="modal-section-title">СИГНАЛ</div><div style="padding:6px 8px;background:var(--bg3);border-left:2px solid var(--accent);font-size:11px'+escHtml(d.forecast)+'</div></div>':'');
  foot.innerHTML='<button class="btn btn-primary" style="flex:1" onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BINANCE:'+encodeURIComponent((d.symbol||'').replace('USDT',''))+'USDT.P\',\'_blank\')">TRADINGVIEW</button><button class="btn" onclick="showCoin(\''+escHtml(d.symbol)+'\')">ОБНОВИТЬ</button>';
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
