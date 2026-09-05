"""
Unit tests for strategies/deposit_guard.py (v3.3).
Run from /root/tradingos:  python3 -m pytest tests/test_deposit_guard.py -v
"""
import json
import os
import sys
import time

sys.path.insert(0, "/root/tradingos")

import pytest

from strategies.deposit_guard import DepositGuard

# Use temp state path to avoid clobbering production state
TEST_STATE = "/tmp/test_deposit_guard_state.json"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate each test with a fresh guard state (never touches production)."""
    if os.path.exists(TEST_STATE):
        os.remove(TEST_STATE)
    import types
    # Fake Path-like object with parent + mkdir + atomic replace
    parent = types.SimpleNamespace(mkdir=lambda parents=False, exist_ok=False: None)
    fake_path = types.SimpleNamespace(
        parent=parent,
        exists=lambda: os.path.exists(TEST_STATE),
        read_text=lambda: open(TEST_STATE).read(),
        with_suffix=lambda _x: types.SimpleNamespace(
            write_text=lambda t: open(TEST_STATE, "w").write(t),
            replace=lambda d: None,
        ),
    )
    # Redirect module STATE_PATH to temp for the duration of the test
    import strategies.deposit_guard as dg
    monkeypatch.setattr(dg, "STATE_PATH", fake_path)
    # Isolate from production trading_mode.json: _reload_limits() reads
    # kill_switch / daily_loss_pct etc from the REAL config. A manual
    # kill_switch (or any future freeze) would flip these tests to BLOCKED
    # and fail the suite — test-fragility, not a regression.
    clean_cfg = {
        "deposit_guard_enabled": True,
        "max_daily_loss_pct": 1.5,
        "max_total_loss_pct": 4.0,
        "max_open_risk_pct": 3.0,
        "min_limit_usd": 0.5,
        "min_available_margin_usd": 5.0,
        "kill_switch": False,
        "max_losing_streak": 5,
        # Enable risk-reduction planning (dry-run) so plan_risk_reduction tests
        # exercise the real path. Production default is enabled:true, dry_run:true.
        "risk_reduction": {
            "enabled": True,
            "dry_run": True,
            "require_manual_confirm": True,
            "target_open_risk_pct": 2.5,
            "max_reduce_per_cycle": 3,
        },
    }
    monkeypatch.setattr(dg, "TRADING_MODE_PATH", types.SimpleNamespace(
        read_text=lambda: json.dumps(clean_cfg),
    ))
    # Unit-test isolation: keep state in memory only.
    # _sync_from_disk() (added 2026-08-15) re-reads the persisted file and
    # would clobber in-test mutations; fcntl lock path is a SimpleNamespace
    # that open() rejects. Disable both so tests exercise logic, not disk.
    monkeypatch.setattr(dg, "fcntl", None)
    # Reset singleton with fresh state
    DepositGuard._instance = None
    g = DepositGuard.instance()
    g._sync_from_disk = lambda: None  # in-memory state is authoritative in tests
    # Mock live API (never hit exchange in tests)
    g._get_equity = lambda: 88.0
    g._get_open_risk = lambda: (0.0, 0)
    g._get_available_margin = lambda: 50.0  # generous free margin (v3.4)
    # Set utc_day to today so _check_day_rollover does NOT reset baseline mid-test
    from datetime import datetime, timezone
    g._state["utc_day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    g._state["day_start_equity"] = 88.0
    yield g
    DepositGuard._instance = None


# ─── 1. Daily state ────────────────────────────────────────────
def test_day_start_equity_set():
    g = DepositGuard.instance()
    assert g._state["day_start_equity"] == 88.0


def test_no_day_baseline_recovers():
    """FIX 2026-08-28: zero/missing day baseline no longer hard-blocks the day.

    Previously NO_DAY_BASELINE latched for the whole day (day rollover ran
    while the API was down → day_start_equity=0 → permanent block, user saw
    'Старт дня: $0.00, лимит $1.50'). Now can_open_position adopts the fresh
    equity it already holds as the baseline (loss-today restarts at 0).
    """
    g = DepositGuard.instance()
    g._state["day_start_equity"] = None
    ok, reason = g.can_open_position(current_equity=88.0)
    assert ok and reason == "OK"
    assert g._state["day_start_equity"] == 88.0
    assert not g._state["blocked_new_entries"]


def test_corrupt_state_recovers():
    """Corrupt baseline (None) recovers from fresh equity instead of blocking.

    Fail-safe is preserved upstream: equity fetch failure still hard-blocks
    (EQUITY_FETCH_ERROR), and kill switches still block regardless.
    """
    g = DepositGuard.instance()
    # Simulate a state where utc_day is set but baseline is missing/corrupt
    from datetime import datetime, timezone
    g._state["utc_day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    g._state["day_start_equity"] = None
    ok, reason = g.can_open_position(current_equity=88.0)
    assert ok
    assert reason == "OK"
    assert g._state["day_start_equity"] == 88.0


# ─── 2. Daily loss limit (equity-based) ────────────────────────
def test_daily_loss_limit_blocks():
    g = DepositGuard.instance()
    # equity 86.5 = -1.5 loss, daily limit 1.5% of 88 = 1.32 → blocked.
    # Production callers always pass proposed_risk_usd (trade_executor 0.5,
    # manual_signal estimated) — passing it here exercises the same path.
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=86.5)
    assert not ok and reason == "DAILY_LOSS_LIMIT"


def test_daily_loss_below_limit_allows():
    g = DepositGuard.instance()
    # equity 87.3 = -0.7 loss < 1.32 → allowed
    ok, reason = g.can_open_position(current_equity=87.3)
    assert ok and reason == "OK"


def test_proposed_risk_breaks_daily_limit():
    g = DepositGuard.instance()
    # equity 87.5 = -0.5 loss; proposed 1.0 → -1.5 >= 1.32 → blocked
    ok, reason = g.can_open_position(proposed_risk_usd=1.0, current_equity=87.5)
    assert not ok and reason == "PROPOSED_RISK_TOO_HIGH"


# ─── 3. Open risk ──────────────────────────────────────────────
def test_open_risk_within_limit_ok():
    g = DepositGuard.instance()
    g._get_open_risk = lambda: (1.0, 0)  # < 2.64
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert ok and reason == "OK"


def test_open_risk_over_limit():
    g = DepositGuard.instance()
    g._get_open_risk = lambda: (3.0, 0)  # > 2.64
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason == "OPEN_RISK_OVER_LIMIT"


def test_open_risk_critical():
    g = DepositGuard.instance()
    g._get_open_risk = lambda: (4.0, 0)  # > hard_kill 3.52
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason == "CRITICAL_OPEN_RISK"


# ─── 3b. Available margin buffer (v3.4) ────────────────────────
# Граница зафиксирована: блок при available < min_available_margin_usd
# (строго меньше). Ровно $5.00 -> разрешено, $4.99 -> блок.
def test_margin_above_threshold_allows():
    g = DepositGuard.instance()
    g._get_available_margin = lambda: 5.01
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert ok and reason == "OK"


def test_margin_exact_threshold_allows():
    """Boundary: available == threshold ($5.00) is ALLOWED (block is strictly <)."""
    g = DepositGuard.instance()
    g._get_available_margin = lambda: 5.00
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert ok and reason == "OK"


def test_margin_below_threshold_blocks():
    g = DepositGuard.instance()
    g._get_available_margin = lambda: 4.99
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")


def test_margin_zero_blocks():
    g = DepositGuard.instance()
    g._get_available_margin = lambda: 0.0
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")


def test_margin_block_does_not_touch_open_positions():
    """MARGIN_BUFFER blocks new entries but closing/reducing stays allowed."""
    g = DepositGuard.instance()
    g._get_available_margin = lambda: 0.0
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")
    assert g.can_close_position() == (True, "OK")
    assert g.can_reduce_position() == (True, "OK")


# ─── 3c. Available-margin SOURCE (v3.5: totalAvailableBalance, not
# availableToWithdraw) ───────────────────────────────────────────
# 2026-08-17: UTA wallet-balance returns the order-placement figure at
# ACCOUNT level (totalAvailableBalance); coin-level availableToWithdraw is a
# WITHDRAWAL figure and is routinely EMPTY — reading it as 0 caused a phantom
# permanent MARGIN_BUFFER block (live: avail=$0.00 vs totalAvailableBalance
# =$61.28). Fix contract (user-authorized): correct field primary; missing/
# invalid/fetch-error → 0.0 = BLOCK (fail-safe); NO equity−used_margin
# fallback (ignores haircuts/frozen orders per Bybit UTA docs); threshold $5
# and semantics unchanged.
def _bind_real_margin(g):
    """Вернуть реальный _get_available_margin (fixture подменяет его на 50.0)."""
    g._get_available_margin = lambda: DepositGuard._get_available_margin(g)


def _acc(**kw):
    """Account-level wallet-balance dict as Bybit returns it (strings)."""
    base = {
        "totalEquity": "80.87",
        "totalMarginBalance": "80.87",
        "totalAvailableBalance": "61.27",
        "totalInitialMargin": "19.60",
        "totalMaintenanceMargin": "1.02",
        "coin": [{
            "equity": "80.95", "walletBalance": "81.09",
            "availableToWithdraw": "",  # empty on UTA — the old phantom-zero bug
        }],
    }
    base.update(kw)
    return base


def test_valid_total_available_balance_used():
    """Валидный available → работает как раньше (разрешение)."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc()
    assert g._get_available_margin() == pytest.approx(61.27)
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert ok and reason == "OK"


def test_withdraw_field_empty_but_correct_field_used():
    """availableToWithdraw пуст, но totalAvailableBalance валиден → используется
    правильный available (это и был live-кейс фантомного нуля)."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance="20.00",
                                           coin=[{"availableToWithdraw": None}])
    assert g._get_available_margin() == pytest.approx(20.00)


def test_correct_field_missing_blocks():
    """totalAvailableBalance отсутствует/пуст → BLOCK (fail-safe), никакой
    вычисленной подмены."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance="")
    assert g._get_available_margin() == 0.0
    assert g._last_margin_snapshot["source_field"] == "totalAvailableBalance:MISSING"
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")


def test_correct_field_invalid_blocks():
    """totalAvailableBalance нечисловой → BLOCK."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance="abc")
    assert g._get_available_margin() == 0.0
    assert g._last_margin_snapshot["source_field"] == "totalAvailableBalance:INVALID"


def test_wallet_fetch_error_blocks():
    """Fetch error → 0.0 = BLOCK (старое fail-safe поведение сохранено)."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: None
    assert g._get_available_margin() == 0.0


def test_boundary_from_source_499_blocks_500_allows():
    """Граница $4.99/$5.00 из РЕАЛЬНОГО источника (не из мока метода)."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance="4.99")
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance="5.00")
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert ok and reason == "OK"


def test_diag_snapshot_fields_populated():
    """Диагностический snapshot содержит все поля для лога."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc()
    g._get_available_margin()
    snap = g._last_margin_snapshot
    assert snap["source_field"] == "totalAvailableBalance"
    assert snap["equity"] == "80.87"
    assert snap["margin_balance"] == "80.87"
    assert snap["available_balance"] == "61.27"
    assert snap["used_margin"] == "19.60"


def test_no_computed_fallback_from_equity_minus_margin():
    """При отсутствии правильного поля guard НЕ вычисляет equity − used_margin
    (игнорирует haircuts/frozen orders) — остаётся BLOCK."""
    g = DepositGuard.instance()
    _bind_real_margin(g)
    g._fetch_wallet_account = lambda: _acc(totalAvailableBalance=None)
    # equity=80.87, used=19.60 → «наивный» fallback дал бы ~61.27 и ALLOW;
    # контракт: значение остаётся 0.0
    assert g._get_available_margin() == 0.0
    ok, reason = g.can_open_position(proposed_risk_usd=0.5, current_equity=88.0)
    assert not ok and reason.startswith("MARGIN_BUFFER:")


# (existing-positions-never-touched pinned above: test_margin_block_does_not_touch_open_positions)


# ─── 4. Missing SL fallback ────────────────────────────────────
def test_missing_sl_uses_fallback():
    g = DepositGuard.instance()
    # Position without SL → counted as notional * fallback_pct, not zero
    g._get_positions_detail = lambda: [{
        "symbol": "X", "side": "Buy", "qty": 10, "entry": 1.0, "mark": 1.0,
        "sl": 0, "notional": 10.0, "risk_usd": 10.0 * 2 / 100,  # 0.20
        "unrealized_pnl": 0, "missing_sl": True,
    }]
    plan = g.plan_risk_reduction()
    assert plan["current_risk"] >= 0.2  # fallback applied, not zero


# ─── 5. Dry-run plan ───────────────────────────────────────────
def test_dry_run_plan_priority_and_projection():
    g = DepositGuard.instance()
    g._get_positions_detail = lambda: [
        {"symbol": "A", "side": "Buy", "qty": 1, "entry": 1, "mark": 1, "sl": 0.9,
         "notional": 100, "risk_usd": 1.62, "unrealized_pnl": -0.1, "missing_sl": False},
        {"symbol": "B", "side": "Buy", "qty": 1, "entry": 1, "mark": 1, "sl": 0.9,
         "notional": 100, "risk_usd": 1.50, "unrealized_pnl": 0.0, "missing_sl": False},
        {"symbol": "C", "side": "Buy", "qty": 1, "entry": 1, "mark": 1, "sl": 0,
         "notional": 100, "risk_usd": 2.0, "unrealized_pnl": -0.5, "missing_sl": True},
    ]
    plan = g.plan_risk_reduction()
    assert plan["dry_run"] is True
    assert plan["actions"]  # non-empty
    # Missing SL position prioritized first
    assert plan["actions"][0]["symbol"] == "C"
    # Projection fields present
    assert "expected_risk_after" in plan
    assert "below_hard_kill" in plan
    assert "next_cycle_needed" in plan


def test_dry_run_no_orders_placed():
    """plan_risk_reduction must never place orders (dry-run)."""
    g = DepositGuard.instance()
    g._get_positions_detail = lambda: [{
        "symbol": "A", "side": "Buy", "qty": 1, "entry": 1, "mark": 1, "sl": 0.9,
        "notional": 100, "risk_usd": 1.62, "unrealized_pnl": 0, "missing_sl": False},
    ]
    # Monkeypatch cancel/create to detect any order attempt
    calls = []
    g._cancel_opening_orders = lambda: calls.append("cancel")
    g.plan_risk_reduction()
    assert calls == []  # no cancel/create attempted


# ─── 6. can_close / can_reduce always allowed ──────────────────
def test_can_close_always_true():
    g = DepositGuard.instance()
    g._state["kill_switch"] = True
    assert g.can_close_position() == (True, "OK")
    assert g.can_reduce_position() == (True, "OK")


# ─── 7. Escalation ─────────────────────────────────────────────
def test_critical_escalation_manual_kill():
    g = DepositGuard.instance()
    g._get_open_risk = lambda: (4.0, 0)
    g._state["critical_since"] = time.time() - 70 * 60  # 70 min ago → level 3
    g._state["critical_timeout_level"] = 0
    ok, reason = g.can_open_position(current_equity=88.0)
    assert not ok and reason == "CRITICAL_OPEN_RISK"
    assert g._state["critical_timeout_level"] == 3
    assert g._state["manual_kill"] is True


def test_critical_cleared_when_risk_drops():
    g = DepositGuard.instance()
    g._state["critical_since"] = time.time() - 10 * 60
    g._state["requires_manual_review"] = True
    g._get_open_risk = lambda: (1.0, 0)  # back within limits
    g.can_open_position(current_equity=88.0)
    assert g._state["critical_since"] is None
    assert g._state["requires_manual_review"] is False
