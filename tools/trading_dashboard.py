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

def collect_setups():
    try:
        from tradingos.signals.manual_scanner import score_symbol
        import httpx as hx
        res=[]
        with hx.Client(timeout=15) as c:
            for sym in pm.CANDIDATES:
                try:
                    sig=score_symbol(c,sym,min_score=50)
                    if not sig or sig.get("score",0)<65: continue
                    chg=pm.get_24h(sym).get("change",0)
                    setup="DIP" if chg<=-5 else "CORR" if chg<=-2 else "IMP" if chg>6 else "ACC"
                    res.append({"symbol":sig["symbol"],"short":sig["symbol"].replace("USDT",""),
                                "side":sig.get("side",""),"score":sig.get("score",0),"rr":sig.get("rr",0),
                                "price":sig.get("price",0),"sl":sig.get("sl",0),"tp":sig.get("final_tp",0),
                                "rsi":sig.get("rsi",0),"mom":sig.get("parts",{}).get("momentum",0),
                                "vol_ratio":sig.get("vol_ratio",0),"contour":sig.get("contour","NO_TRADE"),
                                "setup":setup,"squeeze":sig.get("squeeze_on",False),"smc":sig.get("smc_sweep","")})
                except: pass
        res.sort(key=lambda x:(-x["score"],-x["rr"]))
        return res[:8]
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

def main():
    def loop():
        while True: refresh(); time.sleep(_TTL)
    threading.Thread(target=loop,daemon=True).start()
    srv=HTTPServer(('0.0.0.0',PORT),H)
    ip="127.0.0.1"
    try:
        s=__import__('socket').socket(__import__('socket').AF_INET,__import__('socket').SOCK_DGRAM)
        s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]; s.close()
    except: pass
    print(f"\nTradingOS Control Center\n  http://localhost:{PORT}\n  http://{ip}:{PORT}\n")
    srv.serve_forever()

if __name__=="__main__": main()



# ════════════════════════════════════════════════════════════════════════════
# Frontend — Professional Trading Control Center
# ════════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingOS</title>
<style>
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;
  --text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;
  --green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#a371f7;
  --radius:8px;
}
.light{
  --bg:#f0f2f5;--bg2:#ffffff;--bg3:#f6f8fa;--border:#d0d7de;
  --text:#1f2328;--text2:#656d76;--accent:#0969da;
  --green:#1a7f37;--red:#cf222e;--yellow:#9a6700;--purple:#8250df;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;line-height:1.5}
a{color:var(--accent);text-decoration:none}

/* ── Top Bar ── */
.topbar{display:flex;align-items:center;gap:12px;padding:0 16px;height:48px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100}
.brand{font-size:15px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot-ok{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot-warn{background:var(--yellow);box-shadow:0 0 6px var(--yellow)}
.dot-crit{background:var(--red);box-shadow:0 0 6px var(--red);animation:blink 1.5s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.4}}
.nav-links{display:flex;gap:2px;margin-left:16px}
.nav-link{padding:6px 14px;border-radius:6px;color:var(--text2);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s}
.nav-link:hover{background:var(--bg3);color:var(--text)}
.nav-link.active{background:var(--accent);color:#fff}
.top-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.mode-pill{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.03em}
.mode-auto{background:rgba(63,185,80,.15);color:var(--green)}
.mode-manual{background:rgba(56,166,255,.15);color:var(--accent)}
.mode-kill{background:rgba(248,81,73,.15);color:var(--red)}
.theme-btn{background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
.theme-btn:hover{border-color:var(--accent)}

/* ── Layout ── */
.wrap{max-width:1280px;margin:0 auto;padding:16px;display:grid;gap:12px;grid-template-columns:1fr}
@media(min-width:900px){.wrap{grid-template-columns:1fr 320px}.sidebar{display:block}}
.sidebar{display:none}
@media(min-width:900px){.sidebar{display:block}}

/* ── Panels ── */
.panel{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.panel-hd{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg3)}
.panel-title{font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em}
.panel-body{padding:12px 14px}
.panel-body.no-pad{padding:0}

/* ── Status Hero ── */
.status-hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;padding:16px}
.stat-card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px}
.stat-label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.stat-value{font-size:22px;font-weight:700;font-family:'SF Mono',ui-monospace,monospace;letter-spacing:-.02em}
.stat-sub{font-size:11px;color:var(--text2);margin-top:2px}
.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:var(--text2)}

/* ── Control Bar ── */
.ctrl-bar{display:flex;gap:8px;flex-wrap:wrap;padding:12px 14px;border-top:1px solid var(--border)}
.ctrl-bar .btn{flex:1;min-width:0;justify-content:center}
.btn{padding:7px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:12px;font-weight:500;transition:all .15s;display:inline-flex;align-items:center;gap:5px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{background:#1f6feb}
.btn-danger{background:rgba(248,81,73,.12);color:var(--red);border-color:var(--red)}
.btn-danger:hover{background:rgba(248,81,73,.22)}
.btn-success{background:rgba(63,185,80,.12);color:var(--green);border-color:var(--green)}
.btn-success:hover{background:rgba(63,185,80,.22)}

/* ── Table ── */
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 14px;color:var(--text2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--bg3)}
td{padding:8px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--bg3)}
.sym{font-weight:600;font-family:'SF Mono',ui-monospace,monospace;font-size:12px}
.price{font-family:'SF Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:.03em}
.badge-long{background:rgba(63,185,80,.15);color:var(--green)}
.badge-short{background:rgba(248,81,73,.15);color:var(--red)}
.badge-warn{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-danger{background:rgba(248,81,73,.15);color:var(--red)}
.badge-info{background:rgba(56,166,255,.15);color:var(--accent)}
.badge-neutral{background:var(--bg3);color:var(--text2)}
.action-btn{padding:3px 8px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;font-size:11px}
.action-btn:hover{border-color:var(--accent);color:var(--accent)}
.action-btn.danger:hover{border-color:var(--red);color:var(--red)}

/* ── Setup Cards ── */
.setup-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}
.setup-card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:12px;cursor:pointer;transition:border-color .15s,box-shadow .15s}
.setup-card:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.setup-card.top{border-color:var(--green);box-shadow:0 0 8px rgba(63,185,80,.15)}
.setup-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.setup-sym{font-size:15px;font-weight:700;font-family:'SF Mono',ui-monospace,monospace}
.setup-score{font-size:12px;font-weight:600;padding:2px 8px;border-radius:4px}
.score-high{background:rgba(63,185,80,.15);color:var(--green)}
.score-mid{background:rgba(210,153,34,.15);color:var(--yellow)}
.score-low{background:var(--bg3);color:var(--text2)}
.setup-prices{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:8px}
.pr-cell{text-align:center}
.pr-label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.05em}
.pr-val{font-size:13px;font-weight:600;font-family:'SF Mono',ui-monospace,monospace}
.setup-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--text2)}
.setup-meta span{display:flex;align-items:center;gap:3px}
.setup-desc{font-size:11px;color:var(--text2);margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}
.setup-smc{font-size:11px;color:var(--purple);margin-top:4px}

/* ── Coin Detail Modal ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;display:flex;align-items:center;justify-content:center;padding:16px}
.modal-overlay.hidden{display:none}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:12px;width:100%;max-width:560px;max-height:90vh;overflow-y:auto;box-shadow:0 16px 48px rgba(0,0,0,.4)}
.modal-hd{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)}
.modal-title{font-size:16px;font-weight:700;font-family:'SF Mono',ui-monospace,monospace}
.modal-close{background:none;border:none;color:var(--text2);cursor:pointer;font-size:18px;padding:4px}
.modal-close:hover{color:var(--text)}
.modal-body{padding:16px}
.modal-section{margin-bottom:16px}
.modal-section-title{font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.modal-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px}
.modal-row:last-child{border-bottom:none}
.modal-row .label{color:var(--text2)}
.modal-row .value{font-family:'SF Mono',ui-monospace,monospace;font-weight:500}
.modal-actions{display:flex;gap:8px;margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}

/* ── Activity Feed ── */
.act-list{max-height:240px;overflow-y:auto}
.act-item{display:flex;gap:10px;padding:8px 14px;border-bottom:1px solid var(--border);font-size:12px;align-items:flex-start}
.act-item:last-child{border-bottom:none}
.act-time{color:var(--text2);font-family:'SF Mono',ui-monospace,monospace;font-size:11px;white-space:nowrap;min-width:52px}
.act-dot{width:6px;height:6px;border-radius:50%;margin-top:5px;flex-shrink:0}
.act-dot-ok{background:var(--green)}.act-dot-warn{background:var(--yellow)}.act-dot-crit{background:var(--red)}
.act-text{flex:1;color:var(--text)}.act-detail{color:var(--text2);margin-left:auto;white-space:nowrap}

/* ── Search ── */
.search-wrap{position:relative}
.search-input{width:100%;padding:8px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:13px;outline:none}
.search-input:focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-dim)}
.search-dd{position:absolute;top:100%;left:0;right:0;background:var(--bg2);border:1px solid var(--border);border-radius:6px;margin-top:4px;max-height:200px;overflow-y:auto;z-index:200;display:none;box-shadow:0 8px 24px rgba(0,0,0,.3)}
.search-dd.show{display:block}
.search-item{padding:8px 12px;cursor:pointer;font-size:13px;font-family:'SF Mono',ui-monospace,monospace}
.search-item:hover{background:var(--bg3)}

/* ── Misc ── */
.empty-state{text-align:center;padding:32px;color:var(--text2)}
.empty-state .icon{font-size:32px;margin-bottom:8px;opacity:.5}
.empty-state .title{font-size:14px;font-weight:500;color:var(--text)}
.empty-state .sub{font-size:12px;margin-top:4px}
.skeleton{background:linear-gradient(90deg,var(--bg3) 25%,var(--border) 50%,var(--bg3) 75%);background-size:200%;animation:shimmer 1.5s infinite;border-radius:4px;height:14px;margin:6px 0}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.toast{position:fixed;bottom:20px;right:20px;padding:10px 16px;border-radius:8px;font-size:13px;z-index:300;animation:slideIn .2s ease;box-shadow:0 4px 12px rgba(0,0,0,.3)}
.toast-ok{background:var(--green);color:#fff}
.toast-err{background:var(--red);color:#fff}
.toast-info{background:var(--accent);color:#fff}
@keyframes slideIn{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
</style>
</head>
<body>
<div id="app">
<!-- Top Bar -->
<div class="topbar">
  <div class="brand"><span class="dot" id="statusDot"></span>TradingOS</div>
  <div class="nav-links">
    <div class="nav-link active" data-page="dashboard" onclick="switchPage('dashboard')">Dashboard</div>
    <div class="nav-link" data-page="analysis" onclick="switchPage('analysis')">Analysis</div>
    <div class="nav-link" data-page="scanner" onclick="switchPage('scanner')">Scanner</div>
  </div>
  <div class="top-right">
    <span class="mode-pill" id="modePill">—</span>
    <span class="mode-pill" id="killPill">KILL:—</span>
    <span style="color:var(--text2);font-size:12px;font-family:monospace" id="clock"></span>
    <button class="theme-btn" onclick="toggleTheme()">◐</button>
  </div>
</div>

<div class="wrap">
<!-- ═══ DASHBOARD PAGE ═══ -->
<div class="page active" id="page-dashboard">
  <!-- Status Hero -->
  <div class="panel" id="heroPanel">
    <div class="panel-hd">
      <span class="panel-title">System Status</span>
      <span style="font-size:11px;color:var(--text2)" id="lastUpdate">—</span>
    </div>
    <div class="status-hero">
      <div class="stat-card">
        <div class="stat-label">Status</div>
        <div id="heroStatus" style="font-size:18px;font-weight:700">—</div>
        <div class="stat-sub" id="heroMode">Mode: —</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Equity</div>
        <div class="stat-value" id="heroEquity">—</div>
        <div class="stat-sub" id="heroAvailable">Available: —</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Unrealised PnL</div>
        <div class="stat-value" id="heroPnl">—</div>
        <div class="stat-sub" id="heroRisk">Open risk: —</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">BTC</div>
        <div class="stat-value" id="heroBtc">—</div>
        <div class="stat-sub" id="heroBtcChg">—</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Setups</div>
        <div class="stat-value" id="heroSetups">—</div>
        <div class="stat-sub">Active signals</div>
      </div>
    </div>
    <div class="ctrl-bar" id="ctrlBar">
      <button class="btn btn-success" id="btnResume" onclick="ctrlAction('resume')" style="display:none">▶ Resume Trading</button>
      <button class="btn btn-danger" id="btnPause" onclick="ctrlAction('pause')">⏸ Pause Trading</button>
      <button class="btn" id="btnAuto" onclick="ctrlAction('auto')">Auto Mode</button>
      <button class="btn" id="btnManual" onclick="ctrlAction('manual')">Manual Mode</button>
      <button class="btn btn-danger" onclick="ctrlAction('kill')">⚠ Emergency Stop</button>
    </div>
  </div>

  <!-- Main Grid -->
  <div class="grid" style="grid-template-columns:1fr 300px">
    <!-- Positions -->
    <div class="panel">
      <div class="panel-hd"><span class="panel-title">Open Positions</span><span class="panel-count" id="posCount">0</span></div>
      <div class="panel-body no-pad">
        <table><thead><tr>
          <th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Liq%</th><th></th>
        </tr></thead><tbody id="posBody"></tbody></table>
        <div class="empty-state" id="posEmpty"><div class="icon">📭</div><div class="title">No open positions</div></div>
      </div>
    </div>
    <!-- Sidebar -->
    <div style="display:flex;flex-direction:column;gap:12px">
      <!-- Market -->
      <div class="panel">
        <div class="panel-hd"><span class="panel-title">Market</span></div>
        <div id="marketBody" style="padding:8px 14px"></div>
      </div>
      <!-- Activity -->
      <div class="panel">
        <div class="panel-hd"><span class="panel-title">Activity</span></div>
        <div class="act-list" id="actBody" style="padding:0"></div>
      </div>
    </div>
  </div>

  <!-- Orders + Setups -->
  <div class="grid" style="grid-template-columns:1fr 1fr;margin-top:0">
    <div class="panel">
      <div class="panel-hd"><span class="panel-title">Open Orders</span><span class="panel-count" id="ordCount">0</span></div>
      <div class="panel-body no-pad">
        <table><thead><tr><th>Symbol</th><th>Type</th><th>Price</th><th>Qty</th><th></th></tr></thead><tbody id="ordBody"></tbody></table>
        <div class="empty-state" id="ordEmpty"><div class="icon">📭</div><div class="title">No open orders</div></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-hd"><span class="panel-title">Active Setups</span><span class="panel-count" id="setCount">0</span></div>
      <div class="panel-body" id="setBody" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px"></div>
    </div>
  </div>
</div>

<!-- ═══ ANALYSIS PAGE ═══ -->
<div class="page" id="page-analysis">
  <div class="panel">
    <div class="panel-hd"><span class="panel-title">Coin Analysis</span></div>
    <div style="padding:16px">
      <div class="search-wrap" style="margin-bottom:16px">
        <input class="search-input" id="coinSearchInput" placeholder="Search symbol (e.g. BTC, ETH, APT)..." oninput="onSearchInput(this.value)" onkeydown="if(event.key==='Enter')doSearch()">
        <div class="dropdown" id="searchDd"></div>
      </div>
      <div id="analysisBody"><div class="empty-state"><div class="icon">🔬</div><div class="title">Enter a symbol to analyse</div><div class="sub">e.g. BTCUSDT, ETHUSDT, APTUSDT</div></div></div>
    </div>
  </div>
</div>

<!-- ═══ SCANNER PAGE ═══ -->
<div class="page" id="page-scanner">
  <div class="panel">
    <div class="panel-hd">
      <span class="panel-title">Scanner — All Candidates</span>
      <span class="panel-count" id="scanTotal">0 candidates</span>
    </div>
    <div style="padding:12px 14px">
      <div class="search-wrap" style="margin-bottom:12px">
        <input class="search-input" id="scanSearch" placeholder="Filter symbols..." oninput="filterScan(this.value)" onkeydown="if(event.key==='Enter')filterScan(this.value)">
        <div class="dropdown" id="scanDd"></div>
      </div>
      <div id="scanBody"><div class="state">Loading<span class="loading-dots"></span></div></div>
    </div>
  </div>
</div>
</div>

<!-- Modal -->
<div class="modal-overlay hidden" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-hd"><span class="modal-title" id="modalTitle">Details</span><button class="theme-btn" onclick="closeModal()" style="background:var(--bg3);border:1px solid var(--border);color:var(--text);cursor:pointer;border-radius:6px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:14px">✕</button></div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<script>
const API='/api';
let currentState=null;
let refreshTimer=null;

// ── Theme ──
function toggleTheme(){
  document.documentElement.classList.toggle('light');
  localStorage.setItem('theme',document.documentElement.classList.contains('light')?'light':'dark');
}
(function(){const t=localStorage.getItem('theme');if(t==='light')document.documentElement.classList.add('light')})();

// ── Clock ──
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})},1000);

// ── Navigation ──
function switchPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l=>l.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  document.querySelector(`.nav-link[data-page="${name}"]`).classList.add('active');
  if(name==='scanner') loadScanner();
}

// ── Toast ──
function toast(msg,type='info'){
  const t=document.createElement('div');t.className='toast toast-'+type;t.textContent=msg;
  document.body.appendChild(t);setTimeout(()=>t.remove(),3000);
}

// ── API ──
async function api(path,opts={}){
  const r=await fetch(API+path,opts);
  return r.json();
}

// ── Format helpers ──
function fmtPx(v){
  if(v==null)return'—';v=parseFloat(v);if(isNaN(v))return'—';
  if(Math.abs(v)>=1000)return v.toLocaleString('en-US',{maximumFractionDigits:2});
  if(Math.abs(v)>=1)return v.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  return v.toFixed(6).replace(/0+$/,'').replace(/\.$/,'');
}
function fmtN(v,d=2){return v==null?'—':parseFloat(v).toFixed(d)}
function pnlCls(v){return v>=0?'positive':'negative'}
function badge(cls,text){return `<span class="badge badge-${cls}">${text}</span>`}

// ── Render Dashboard ──
async function loadDashboard(){
  const d=await api('/api');
  if(!d)return;
  currentState=d;
  // Status hero
  const ctl=d.control||{};
  const dot=document.getElementById('statusDot');
  dot.className='dot '+(ctl.cls==='ok'?'dot-ok':ctl.cls==='critical'?'dot-crit':'dot-warn');
  document.getElementById('heroStatus').textContent=ctl.overall||'—';
  document.getElementById('heroStatus').style.color=ctl.cls==='ok'?'var(--green)':ctl.cls==='critical'?'var(--red)':'var(--yellow)';
  document.getElementById('heroMode').textContent=`Mode: ${ctl.mode||'—'} · Orders ${ctl.orders_disabled?'❌':'✅'}`;
  // Money
  const m=d.money||{};
  document.getElementById('heroEquity').textContent='$'+fmtPx(m.equity);
  document.getElementById('heroAvailable').textContent='Avail: $'+fmtPx(m.available);
  const pnlEl=document.getElementById('heroPnl');
  pnlEl.textContent=(m.pnl_unrealised>=0?'+':'')+'$'+fmtPx(m.pnl_unrealised);
  pnlEl.className='stat-value '+pnlCls(m.pnl_unrealised);
  document.getElementById('heroRisk').textContent='Open risk: $'+fmtPx(m.open_risk);
  // Market
  document.getElementById('heroBtc').textContent='$'+fmtPx((d.market||[])[0]?.price||0);
  const btcChg=(d.market||[])[0]?.chg||0;
  const btcEl=document.getElementById('btcChg');
  if(btcEl){btcEl.textContent=(btcChg>=0?'+':'')+btcChg.toFixed(2)+'%';btcEl.style.color=btcChg>=0?'var(--green)':'var(--red)';}
  // Positions
  const pos=(d.positions||[]);
  document.getElementById('posCount').textContent=pos.length;
  const posBody=document.getElementById('posBody');
  const posEmpty=document.getElementById('posEmpty');
  if(!pos.length){posBody.innerHTML='';posEmpty.style.display='';}
  else{posEmpty.style.display='none';posBody.innerHTML=pos.map(p=>{
    const pnl=p.upnl||0;const pnlPct=p.entry?((p.mark-p.entry)/p.entry*100):0;
    const liqDist=p.liq&&p.mark?Math.abs((p.liq-p.mark)/p.mark*100):0;
    const liqCls=liqDist>50?'positive':liqDist>20?'':'negative';
    return `<tr class="pos-${p.side?.toLowerCase()}">
      <td><span class="sym">${p.symbol}</span></td>
      <td>${badge(p.side==='LONG'?'long':'short',p.side)}</td>
      <td class="price">${fmtN(p.qty,4)}</td>
      <td class="price">${fmtPx(p.entry)}</td>
      <td class="price">${fmtPx(p.mark)}</td>
      <td class="${pnlCls(pnl)}" style="font-weight:600">${pnl>=0?'+':''}$${fmtN(pnl,2)}</td>
      <td class="${liqCls}">${liqDist.toFixed(0)}%</td>
      <td><button class="action-btn danger" onclick="closePos('${p.symbol}','${p.side}')">Close</button></td>
    </tr>`;
  }).join('');}
  // Orders
  const ord=(d.orders||[]).filter(o=>o.type&&['LIMIT','STOP_LIMIT','TAKE_PROFIT_LIMIT'].includes(o.type.toUpperCase()));
  document.getElementById('ordCount').textContent=ord.length;
  const ordBody=document.getElementById('ordBody');
  const ordEmpty=document.getElementById('ordEmpty');
  if(!ord.length){ordBody.innerHTML='';ordEmpty.style.display='';}
  else{ordEmpty.style.display='none';ordBody.innerHTML=ord.map(o=>`<tr>
    <td><span class="sym">${o.symbol}</span></td>
    <td>${badge(o.side==='BUY'?'long':'short',o.side)}</td>
    <td class="price">${fmtPx(o.price)}</td>
    <td class="price">${fmtN(o.qty,4)}</td>
    <td><button class="action-btn danger" onclick="cancelOrd('${o.symbol}',${o.price})">Cancel</button></td>
  </tr>`).join('');}
  // Setups
  const sets=d.setups||[];
  document.getElementById('setCount').textContent=sets.length;
  const setBody=document.getElementById('setBody');
  if(!sets.length){setBody.innerHTML='<div class="empty-state" style="padding:20px"><div class="icon">🔍</div><div class="title">No qualifying setups</div><div class="sub">Scanner found no signals meeting criteria (score≥65, mom≥6)</div></div>';}
  else{setBody.innerHTML=sets.map(s=>renderSetupCard(s)).join('');}
  // Market strip
  const mk=document.getElementById('marketBody');
  if(d.market&&d.market.length){
    mk.innerHTML=d.market.slice(0,5).map(m=>`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:13px"><span style="font-weight:600">${m.symbol.replace('USDT','')}</span><span class="price">$${fmtPx(m.price)}</span><span style="color:${m.chg>=0?'var(--green)':'var(--red)'}">${m.chg>=0?'+':''}${m.chg.toFixed(2)}%</span></div>`).join('');
  }
  // Activity
  const act=document.getElementById('actBody');
  const acts=(d.activity||[]).slice(0,8);
  if(!acts.length){act.innerHTML='<div class="empty-state" style="padding:16px"><div class="title">No recent activity</div></div>';}
  else{act.innerHTML=acts.map(a=>{const dotCls=a.level==='critical'?'act-dot-crit':a.level==='warning'?'act-dot-warn':'act-dot-ok';const time=new Date(a.ts).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});return `<div class="act-item"><span class="act-dot ${dotCls}"></span><span class="act-text">${escHtml(a.event)}</span><span class="act-detail">${escHtml(a.detail||'')}</span><span class="act-time">${time}</span></div>`}).join('');}
  document.getElementById('lastUpdate').textContent='Updated '+new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
  // Control buttons
  const ctl2=d.control||{};
  document.getElementById('btnPause').style.display=ctl2.paused?'none':'';
  document.getElementById('btnResume').style.display=ctl2.paused?'':'none';
  document.getElementById('btnAuto').style.opacity=ctl2.mode==='AUTO'?'.5':'1';
  document.getElementById('btnManual').style.opacity=ctl2.mode==='MANUAL'?'.5':'1';
  const kp=document.getElementById('killPill');
  kp.textContent='KILL '+(ctl2.kill_switch?'ON':'OFF');
  kp.className='mode-pill '+(ctl2.kill_switch?'mode-'+'red':'mode-'+'green');
}

function renderSetupCard(s){
  const dist=s.dist_to_entry||0;
  const dEmoji=dist<=0.2?'🟢':dist<=1?'🟡':'⚪';
  const scoreCls=s.score>=80?'score-high':s.score>=70?'score-mid':'score-low';
  return `<div class="setup-card" onclick="showCoin('${s.symbol}')">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <span class="setup-sym">${s.side==='LONG'?'🟢':'🔴'} <code>${s.short}</code> ${s.side}</span>
      <span class="badge ${scoreCls}">score ${s.score}</span>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;font-size:12px;font-family:monospace">
      <div><span style="color:var(--text2)">Entry </span><code>${fmtPx(s.entry_low)}–${fmtPx(s.entry_high)}</code></div>
      <div><span style="color:var(--text2)">TP </span><code style="color:var(--green)">${fmtPx(s.tp)}</code></div>
      <div><span style="color:var(--text2)">SL </span><code style="color:var(--red)">${fmtPx(s.sl)}</code></div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;font-size:11px;color:var(--text2)">
      <span>R:R <b style="color:var(--text)">${s.rrr}</b> · ${s.desc}</span>
      <span>${dEmoji} ${dist>=0?'+':''}${dist.toFixed(1)}% to entry</span>
    </div>
    ${s.smc_hint?`<div style="font-size:11px;color:var(--purple);margin-top:4px">💡 ${escHtml(s.smc_hint)}</div>`:''}
  </div>`;
}

function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

// ── Coin Analysis ──
async function loadCoin(sym){
  sym=sym.toUpperCase().replace(/[^A-Z0-9]/g,'').replace('USDT$','').replace('USDC$','');
  if(!sym.endsWith('USDT'))sym+='USDT';
  const d=await api('/api/coin/'+sym);
  if(d.error){document.getElementById('analysisBody').innerHTML=`<div class="empty-state"><div class="icon">❌</div><div class="title">${escHtml(d.error)}</div></div>`;return;}
  coinData=d;
  renderCoinDetail(d);
}

function renderCoinDetail(d){
  const el=document.getElementById('analysisBody');
  const rsiColor=d.rsi<30?'var(--green)':d.rsi>70?'var(--red)':'var(--text)';
  const momColor=d.mom>=10?'var(--green)':d.mom>=6?'var(--yellow)':'var(--red)';
  const fcColors={bullish:'var(--green)',bearish:'var(--red)',neutral:'var(--text2)',squeeze:'var(--purple)',dip:'var(--accent)',breakout:'var(--green)'};
  const fcIcons={bullish:'🟢',bearish:'🔴',neutral:'⚪',squeeze:'⚡',dip:'💎',breakout:'🚀'};
  el.innerHTML=`
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <h2 style="font-size:22px;font-weight:700;font-family:monospace">${d.short||d.symbol}</h2>
      <span class="badge ${d.side==='LONG'?'badge-green':'badge-red'}">${d.side||'—'}</span>
      <span class="badge badge-info">Score ${d.score||'—'}</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px">
      <div class="stat-card"><div class="stat-label">Price</div><div class="stat-value">$${fmtPx(d.price)}</div></div>
      <div class="stat-card"><div class="stat-label">24h Change</div><div class="stat-value" style="color:${d.chg>=0?'var(--green)':'var(--red)'}">${d.chg>=0?'+':''}${d.chg?.toFixed(2)||'—'}%</div></div>
      <div class="stat-card"><div class="stat-label">RSI(14)</div><div class="stat-value" style="color:${rsiColor}">${d.rsi?.toFixed(1)||'—'}</div></div>
      <div class="stat-card"><div class="stat-label">ATR(1h)%</div><div class="stat-value">${d.atr_pct||'—'}%</div></div>
      <div class="stat-card"><div class="stat-label">Vol Ratio</div><div class="stat-value">${d.vol_ratio?.toFixed(2)||'—'}</div></div>
      <div class="stat-card"><div class="stat-label">Distance to EMA20</div><div class="stat-value" style="color:${Math.abs(d.dist_e20)||0<=0.15?'var(--green)':'var(--yellow)'}">${d.dist_e20!=null?(d.dist_e20>=0?'+':'')+d.dist_e20.toFixed(2)+'%':'—'}</div></div>
    </div>
    ${d.score?`
    <div class="card" style="margin-bottom:12px">
      <div class="card-header"><span class="card-title">Scanner Signal</span></div>
      <div style="padding:0 0 12px">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px;font-size:12px">
          <div><span style="color:var(--text2)">Score: </span><b>${d.score}</b></div>
          <div><span style="color:var(--text2)">Momentum: </span><b style="color:${momColor}">${d.mom}/15</b></div>
          <div><span style="color:var(--text2)">H1 Trend: </span><b>${d.h1_trend||'—'}</b></div>
          <div><span style="color:var(--text2)">H4 Trend: </span><b>${d.h4_trend||'—'}</b></div>
          <div><span style="color:var(--text2)">D1 Trend: </span><b>${d.d1_trend||'—'}</b></div>
          <div><span style="color:var(--text2)">MTF Agree: </span><b>${d.mtf_agree==null?'—':d.mtf_agree?'✅':'❌'}</b></div>
          <div><span style="color:var(--text2)">SMC: </span><b style="color:var(--purple)">${d.smc||'—'}</b>${d.smc_str?` <span style="color:var(--text2)">(${d.smc_str})</span>`:''}</div>
          <div><span style="color:var(--text2)">Squeeze: </span><b>${d.squeeze?'🔥 ON':'OFF'}</b></div>
        </div>
        ${d.forecast?`<div style="margin-top:8px;padding:8px 12px;border-radius:6px;background:${fcColors[d.forecast_class]||'var(--bg3)'}22;border-left:3px solid ${fcColors[d.forecast_class]||'var(--text2)'};font-size:13px">${fcIcons[d.forecast_class]||'●'} ${escHtml(d.forecast)}</div>`:''}
      </div>
    </div>`:''}
    ${d.sl&&d.tp?`
    <div class="card" style="margin-bottom:12px">
      <div class="card-header"><span class="card-title">Trade Levels</span><span class="badge badge-green">R:R ${d.rr?.toFixed(2)||'—'}</span></div>
      <div style="padding:0 0 12px">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;text-align:center">
          <div><div style="font-size:11px;color:var(--text2)">ENTRY ZONE</div><div style="font-size:16px;font-weight:700;font-family:monospace">$${fmtPx(d.entry_low)}–${fmtPx(d.entry_high)}</div><div style="font-size:11px;color:var(--text2)">Dist: ${d.dist_to_entry>=0?'+':''}${d.dist_to_entry?.toFixed(1)||'—'}%</div></div>
          <div><div style="font-size:11px;color:var(--green)">TAKE PROFIT</div><div style="font-size:16px;font-weight:700;font-family:monospace;color:var(--green)">$${fmtPx(d.tp)}</div><div style="font-size:11px;color:var(--green)">+${d.tp_pct?.toFixed(1)||'—'}%</div></div>
          <div><div style="font-size:11px;color:var(--red)">STOP LOSS</div><div style="font-size:16px;font-weight:700;font-family:monospace;color:var(--red)">$${fmtPx(d.sl)}</div><div style="font-size:11px;color:var(--red)">-${d.sl_pct?.toFixed(1)||'—'}%</div></div>
        </div>
        <div style="margin-top:8px;font-size:12px;color:var(--text2)">
          ${d.desc||''} · Vol $${(d.vol/1e6).toFixed(1)}M · 7d range $${fmtPx(d.range_7d?.split('–')[0])}–$${fmtPx(d.range_7d?.split('–')[1])}
        </div>
      </div>
    </div>`:''}
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" onclick="window.open('https://www.tradingview.com/chart/?symbol=BINANCE:${d.symbol.replace('USDT','')}USDT.P','_blank')">📊 TradingView</button>
      <button class="btn btn-ghost" onclick="loadCoin('${d.symbol}')">🔄 Refresh</button>
    </div>`;
}

// ── Scanner Page ──
async function loadScanner(){
  const d=await api('/api');
  const d2=await api('/api/setups');
  document.getElementById('scanTotal').textContent=d2.length+' setups · '+d.candidates?.length+' scanned';
  const el=document.getElementById('scanBody');
  if(!d2.length){el.innerHTML='<div class="empty-state"><div class="icon">🔍</div><div class="title">No qualifying setups</div><div class="sub">All candidates failed scanner filters — market is flat or quiet</div></div>';return;}
  el.innerHTML=d2.map(s=>`
    <div class="setup-card" onclick="showCoin('${s.symbol}')">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="setup-sym">${s.side==='LONG'?'🟢':'🔴'} <code>${s.short}</code> ${s.side}</span>
        <span class="badge ${s.score>=80?'badge-green':s.score>=70?'badge-info':'badge-neutral'}">score ${s.score}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:8px;font-size:11px;font-family:monospace">
        <div><span style="color:var(--text2)">Entry </span><code>${fmtPx(s.entry_low)}–${fmtPx(s.entry_high)}</code></div>
        <div><span style="color:var(--green)">TP </span><code>${fmtPx(s.tp)}</code> <span style="color:var(--green)">+${s.tp_pct}%</span></div>
        <div><span style="color:var(--red)">SL </span><code>${fmtPx(s.sl)}</code> <span style="color:var(--red)">-${s.sl_pct}%</span></div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:6px;font-size:11px;color:var(--text2)">
        <span>R:R ${s.rr}:1 · ${s.setup||s.desc}</span>
        <span>RSI ${s.rsi?.toFixed(0)} · Mom ${s.momentum}</span>
      </div>
    </div>`).join('');
}

// ── Controls ──
async function ctrlAction(action){
  if(action==='kill'){
    if(!confirm('⚠️ EMERGENCY KILL SWITCH\n\nThis will disable all trading immediately.\nAre you sure?'))return;
    if(!confirm('Confirm: force kill switch ON?'))return;
  }else if(action==='pause'){
    if(!confirm('Pause trading? No new orders will be placed.'))return;
  }else if(action==='auto'){
    if(!confirm('Switch to AUTO mode? Trading will resume automatically.'))return;
  }else if(action==='manual'){
    if(!confirm('Switch to MANUAL mode? Auto trading will stop.'))return;
  }
  const r=await fetch(API+'/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
  const d=await r.json();
  toast(d.msg||d.error?'⚠ '+d.error:d.msg,'ok');
  loadDashboard();
}

async function closePos(sym,side){
  if(!confirm(`Close ${sym} ${side}?`))return;
  const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'close',symbol:sym,side})});
  const d=await r.json();toast(d.msg||d.error?'⚠ '+d.error:d.msg,d.error?'err':'ok');loadDashboard();
}
async function cancelOrd(sym,price){
  if(!confirm(`Cancel ${sym} limit order @ ${price}?`))return;
  const r=await fetch(API+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'cancel',symbol:sym,price})});
  const d=await r.json();toast(d.msg||d.error?'⚠ '+d.error:d.msg,d.error?'err':'ok');loadDashboard();
}

// ── Search ──
function onSearchInput(q){
  const dd=document.getElementById('coinDropdown');
  if(!q||q.length<1){dd.classList.remove('show');dd.innerHTML='';return;}
  const matches=CANDIDATES.filter(s=>s.includes(q.toUpperCase())).slice(0,10);
  dd.innerHTML=matches.map(s=>`<div class="search-item" onclick="selectCoin('${s}')">${s.replace('USDT','')}</div>`).join('');
  dd.classList.add('show');
}
function doSearch(){
  const v=document.getElementById('coinSearchInput').value.toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(v&&!v.endsWith('USDT'))v+='USDT';
  if(v){switchPage('analysis');loadCoin(v);document.getElementById('coinDropdown').classList.remove('show');}
}
function selectCoin(sym){
  document.getElementById('coinSearchInput').value=sym.replace('USDT','');
  loadCoin(sym);document.getElementById('coinDropdown').classList.remove('show');
}
document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))document.getElementById('coinDropdown').classList.remove('show')});

// ── Init ──
let CANDIDATES=[];
fetch('/api').then(r=>r.json()).then(d=>{CANDIDATES=d.candidates||[]}).catch(()=>{});
loadDashboard();
setInterval(loadDashboard,10000);
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
    threading.Thread(target=lambda: [_refresh_loop(), None][1], daemon=True).start()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
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
