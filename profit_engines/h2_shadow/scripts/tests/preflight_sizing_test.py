#!/usr/bin/env python3
"""H2 pre-flight synthetic test — sizing cap + min-notional gates + fail-closed path.

DRY-RUN ONLY: FakeClient.create_order/get_order/cancel_order raise if invoked.
No writes to state.json (never unpauses). Ledger redirected to /tmp.
Grounded in REAL read-only instrument facts fetched 2026-08-12 from Bybit
(v5/market/instruments-info + tickers), account equity = 79.58 (main-agent fact).
This file is a TEMP pre-flight harness: it is removed after the pre-flight run.
"""
import os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h2_logger
from h2_executor import H2Executor
from h2_logger import pause_state

# ---- redirect ledger so the real append-only ledger is never touched ----
LEDGER_TMP = os.path.join(h2_logger.ROOT, '.preflight_ledger_tmp.jsonl')
h2_logger.LEDGER = LEDGER_TMP
if os.path.exists(LEDGER_TMP):
    os.remove(LEDGER_TMP)

# ---- real instrument facts (fetched read-only 2026-08-12) ----
REAL = {
    # sym: (price, min_qty, qty_step, min_notional, tick_size)
    'BTCUSDT': dict(px=63713.85, min_qty=0.001, step=0.001, min_notional=5.0, tick=0.1),
    'ETHUSDT': dict(px=1886.045, min_qty=0.01, step=0.01, min_notional=5.0, tick=0.01),
    'SOLUSDT': dict(px=76.165, min_qty=0.1, step=0.1, min_notional=5.0, tick=0.01),
    'BNBUSDT': dict(px=614.05, min_qty=0.01, step=0.01, min_notional=5.0, tick=0.1),
    'XRPUSDT': dict(px=1.01885, min_qty=0.1, step=0.1, min_notional=5.0, tick=0.0001),
}
EQUITY = 79.58  # main-agent verified account equity


class FakeClient:
    """Deterministic stub. Any real order path raises — proves create_order never runs."""
    def __init__(self):
        self.calls = {}

    def _c(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def ticker(self, sym):
        self._c('ticker')
        p = REAL[sym]['px']
        return {'bid': p, 'ask': p, 'mid': p, 'last': p}

    def instrument(self, sym):
        self._c('instrument')
        r = REAL[sym]
        return {'symbol': sym, 'status': 'Trading', 'min_qty': r['min_qty'],
                'qty_step': r['step'], 'min_notional': r['min_notional'],
                'tick_size': r['tick']}

    def wallet_usdt(self):
        self._c('wallet_usdt')
        return {'equity': EQUITY, 'wallet': EQUITY, 'available': EQUITY}

    def positions(self, symbol=None):
        self._c('positions')
        return []

    def open_orders(self, symbol=None, order_id=None):
        self._c('open_orders')
        return []

    def set_leverage(self, symbol, leverage):
        self._c('set_leverage')
        return True

    def create_order(self, **kw):
        raise AssertionError('create_order MUST NEVER be called in preflight')

    def get_order(self, symbol, order_id):
        raise AssertionError('get_order MUST NEVER be called')

    def cancel_order(self, symbol, order_id):
        raise AssertionError('cancel_order MUST NEVER be called')


def make_sig(sym, entry, bps):
    half = entry * bps / 10000.0 / 2
    now = int(time.time() * 1000)
    return {'sym': sym, 'event_ts': now - 2000, 'dir': 1, 'side': 'BUY',
            'entry': entry, 'range_hi': entry + half, 'range_lo': entry - half,
            'c_trig': entry, 'bar_i': 1000, 'vratio': 3.5, 'atr_pctl': 0.02,
            'bbw_pctl': 0.04, 'rcomp': 0.5}


def honest_min(sym):
    r = REAL[sym]
    return max(r['min_notional'], r['min_qty'] * r['px'])


results = {}

# ---- 0) kill-switch state check (READ-ONLY, no writes) ----
st = pause_state()
results['state_paused'] = {'paused': st.get('paused'),
                           'pass': st.get('paused') is True}
assert st.get('paused') is True, 'state.json must be paused=true'

# ---- 1) fresh-read fail-closed path: paused -> NO_TRADE at L1, ZERO API calls ----
fc = FakeClient()
ex = H2Executor(client=fc, dry_run=True)
r = ex.process_signal(make_sig('BTCUSDT', REAL['BTCUSDT']['px'], 40))
results['fresh_read_fail_closed'] = {
    'outcome': r.get('outcome'), 'reason': r.get('reason'),
    'api_calls': dict(fc.calls),
    'pass': r.get('outcome') == 'NO_TRADE' and 'paused' in (r.get('reason') or '')
            and not fc.calls,
}

# ---- 2) cap + min-gate tests: 5 symbols x 2 range cases ----
CAP = 50.0
results['sizing_cap'] = []
results['pretrade_gates'] = []
order_path_calls = []

for sym, r_ in REAL.items():
    for label, bps in (('tight_40bps', 40), ('wide_2000bps', 2000)):
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=True)
        sig = make_sig(sym, r_['px'], bps)
        sz, why = ex._sizing(sig)
        # cap enforcement: notional = min(raw, 50), never > 50
        cap_ok = (abs(sz['notional'] - min(sz['notional_raw'], CAP)) <= 1e-9
                  and sz['notional'] <= CAP + 1e-9)
        results['sizing_cap'].append({
            'sym': sym, 'case': label, 'range_bps': round(sz['range_bps'], 2),
            'notional_raw': round(sz['notional_raw'], 2),
            'notional': sz['notional'], 'capped': sz['notional_capped'],
            'cap': CAP, 'cap_ok': cap_ok,
        })
        # min-notional / lot gates (the real fail-closed code, lines ~236-242)
        pre = ex._pretrade(sig, sz, f'H2-{sym}-{label}')
        honest = honest_min(sym)
        qty = pre.get('qty', 0)
        notional_at_limit = qty * sz['limit_px'] if qty else 0
        results['pretrade_gates'].append({
            'sym': sym, 'case': label, 'ok': pre.get('ok'),
            'reason': pre.get('reason'), 'qty': qty,
            'notional_at_limit': round(notional_at_limit, 2),
            'honest_min': round(honest, 2),
            'below_any_min_gate': pre.get('ok') is False,
            'within_cap': notional_at_limit <= CAP + 1e-9,
        })
    order_path_calls.append(dict(fc.calls))

results['order_path_calls_seen'] = {
    'create_order': max(c.get('create_order', 0) for c in order_path_calls),
    'get_order': max(c.get('get_order', 0) for c in order_path_calls),
    'cancel_order': max(c.get('cancel_order', 0) for c in order_path_calls),
    'pass': all(c.get('create_order', 0) == 0 and c.get('get_order', 0) == 0
                and c.get('cancel_order', 0) == 0 for c in order_path_calls),
}

# ---- assertions ----
assert results['state_paused']['pass']
assert results['fresh_read_fail_closed']['pass'], 'fail-closed path failed'
for row in results['sizing_cap']:
    assert row['cap_ok'], f"cap violated: {row}"
for row in results['pretrade_gates']:
    if row['sym'] == 'BTCUSDT':
        assert row['below_any_min_gate'], f"BTC must be NO_TRADE: {row}"
    else:
        assert row['ok'], f"{row['sym']} should pass min gates: {row}"
        assert row['within_cap'], f"{row['sym']} exceeded cap: {row}"
assert results['order_path_calls_seen']['pass'], 'an order-path call was made!'

results['all_pass'] = True
print(json.dumps(results, indent=2))

# clean up temp ledger
if os.path.exists(LEDGER_TMP):
    os.remove(LEDGER_TMP)
