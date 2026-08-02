#!/usr/bin/env python3
"""
Stage 1 — Shadow Execution Audit.
Запускается при первом accepted signal.
Проверяет полный боевой контур БЕЗ отправки ордера.
"""
from __future__ import annotations

import json, subprocess, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
SIGNAL_EVENT_PATH = ROOT / "memory" / "first_signal_event.json"
DECISION_JSON = Path("/root/trading_brain_v4/research/execution/decision.json")
EXECUTOR = Path("/root/trading_brain_v4/research/execution/executor_v0.py")
REPORT_PATH = ROOT / "docs" / "migration" / "STAGE1_SHADOW_AUDIT_REPORT.md"

checks = []
PASS = 0; FAIL = 0

def check(ok: bool, label: str, detail: str = ""):
    global PASS, FAIL
    if ok:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        FAIL += 1
    checks.append({"label": label, "pass": ok, "detail": detail})


def main():
    global PASS, FAIL

    print("=" * 60)
    print("STAGE 1 — SHADOW EXECUTION AUDIT")
    print("=" * 60)

    # ── Load first signal event ──────────────────────────────────────
    if not SIGNAL_EVENT_PATH.exists():
        print("\n❌ No first_signal_event.json found.")
        print("   Run observation until first accepted signal appears.")
        sys.exit(1)

    signal = json.loads(SIGNAL_EVENT_PATH.read_text())
    print(f"\nSignal: {signal.get('symbol','?')} {signal.get('direction','?')} "
          f"conf={signal.get('confidence',0):.3f}")

    # =================================================================
    # CHECKLIST
    # =================================================================

    # 1. Signal integrity
    print(f"\n{'='*60}")
    print("1. SIGNAL INTEGRITY")
    print(f"{'='*60}")
    check(signal.get("direction") in ("BUY", "SELL"),
          "direction = BUY/SELL", signal.get("direction",""))
    check(signal.get("confidence", 0) >= 0.55,
          "confidence >= 0.55", f"{signal.get('confidence',0):.3f}")
    check(signal.get("entry", 0) > 0,
          "entry > 0", f"{signal.get('entry',0):.6f}")
    check(signal.get("stop_loss", 0) > 0,
          "SL > 0", f"{signal.get('stop_loss',0):.6f}")
    check(signal.get("take_profit", 0) > 0,
          "TP > 0", f"{signal.get('take_profit',0):.6f}")
    check(signal["stop_loss"] != signal["entry"],
          "SL != entry", f"both {signal['entry']}")
    risk_pct = abs(signal["entry"] - signal["stop_loss"]) / signal["entry"] * 100
    check(risk_pct < 10,
          "risk within limits", f"{risk_pct:.2f}%")

    # 2. decision.json generation
    print(f"\n{'='*60}")
    print("2. DECISION.JSON")
    print(f"{'='*60}")

    decision = {
        "trace_id": str(uuid.uuid4()),
        "decision_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "quantity": 10,
        "entry_price": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "action": "OPEN_POSITION",
        "source": "tradingos_signal_engine",
        "confidence": signal["confidence"],
        "reason": f"first_forward_signal; ADX={signal.get('adx',0):.0f} RSI={signal.get('rsi',0):.0f}",
        "mode": "SHADOW",
    }

    DECISION_JSON.parent.mkdir(parents=True, exist_ok=True)
    DECISION_JSON.write_text(json.dumps(decision, indent=2, ensure_ascii=False))

    d = json.loads(DECISION_JSON.read_text())
    check(d["symbol"] == signal["symbol"], "symbol matches", f"{d['symbol']} vs {signal['symbol']}")
    check(d["direction"] == signal["direction"], "direction matches")
    check(d["entry_price"] == signal["entry"], "entry matches")
    check(d["stop_loss"] == signal["stop_loss"], "SL matches")
    check(d["take_profit"] == signal["take_profit"], "TP matches")

    # 3. Guardian validation
    print(f"\n{'='*60}")
    print("3. GUARDIAN VALIDATION")
    print(f"{'='*60}")

    # Run executor dry-run and capture verdict
    executor_path = str(EXECUTOR)
    if not EXECUTOR.exists():
        check(False, "Executor not found", str(EXECUTOR))
        write_report(signal, decision)
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, executor_path, "--dry-run", str(DECISION_JSON)],
        capture_output=True, text=True, timeout=30,
    )
    guardian_allowed = result.returncode == 0 and "Guardian ALLOWED" in result.stdout
    check(guardian_allowed, f"Guardian verdict: {'ALLOWED' if guardian_allowed else 'DENIED'}", result.stdout[:200])
    if not guardian_allowed:
        check(False, "Guardian blocked — check risk parameters", result.stderr[:200])

    # 4. Executor dry-run
    print(f"\n{'='*60}")
    print("4. EXECUTOR DRY-RUN")
    print(f"{'='*60}")
    check(result.returncode == 0, f"Executor exit code = {result.returncode}")
    try:
        payload = json.loads(result.stdout.strip())
        check(payload.get("accepted") == True, "Payload accepted = true", str(payload.get("accepted")))
        check(payload.get("reason") == "Dry-run passed", "Reason = Dry-run passed")
        check("DRY-" in payload.get("order_id", ""), "Order ID generated")
    except (json.JSONDecodeError, KeyError) as e:
        check(False, "Payload parse failed", str(e))

    # 5. Logging
    print(f"\n{'='*60}")
    print("5. LOGGING INTEGRITY")
    print(f"{'='*60}")
    check(SIGNAL_EVENT_PATH.exists(), "first_signal_event.json created")
    check(DECISION_JSON.exists(), "decision.json created")
    check(0 not in [signal.get("entry",0), signal.get("stop_loss",0), signal.get("take_profit",0)],
          "no zero values in signal")
    check(result.stderr == "" or "error" not in result.stderr.lower(),
          "no errors in stderr", result.stderr[:200] if result.stderr else "none")

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"STAGE 1 RESULT: {PASS}/{PASS+FAIL} checks passed")
    print(f"{'='*60}")

    if FAIL == 0:
        print("\n✅ ALL CHECKS PASSED")
        print("   Stage 2 authorized (manual approval required).")
    else:
        print(f"\n⚠️  {FAIL} checks FAILED")
        print("   RETURN to Observation. DO NOT enable live execution.")

    write_report(signal, decision)


def write_report(signal: dict, decision: dict):
    """Write Stage 1 audit report."""
    lines = [
        "# STAGE 1 — SHADOW EXECUTION AUDIT REPORT",
        "",
        f"Date: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Signal",
        "",
        f"- Symbol: {signal.get('symbol','?')}",
        f"- Direction: {signal.get('direction','?')}",
        f"- Confidence: {signal.get('confidence',0):.4f}",
        f"- Entry: {signal.get('entry',0):.6f}",
        f"- SL: {signal.get('stop_loss',0):.6f}",
        f"- TP: {signal.get('take_profit',0):.6f}",
        f"- ADX: {signal.get('adx',0):.0f}",
        f"- RSI: {signal.get('rsi',0):.0f}",
        "",
        "## Checklists",
        "",
    ]
    for c in checks:
        icon = "✅" if c["pass"] else "❌"
        detail = f" — {c['detail']}" if c["detail"] else ""
        lines.append(f"- {icon} {c['label']}{detail}")

    lines += [
        "",
        f"## Result: {PASS}/{PASS+FAIL} PASS",
        "",
        "## Verdict",
        "",
    ]

    if FAIL == 0:
        lines += [
            "✅ ALL CHECKS PASSED",
            "",
            "### Next: Stage 2 — First $1 Live Trade",
            "",
            "**Manual approval required.**",
            "**Guardian ENABLED.**",
            "**Max risk $1.**",
            "**Execution OFF until manual confirm.**",
        ]
    else:
        lines += [
            "❌ SHADOW AUDIT FAILED",
            "",
            "### Action: RETURN to Observation",
            "",
            "Do NOT enable live execution until all checks pass.",
        ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))
    print(f"\n  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
