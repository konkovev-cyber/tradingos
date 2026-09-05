#!/usr/bin/env python3
"""tradingos-cli — Research Collector + TQL queries."""
import sys
import json
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data_lake.sqlite_backend import DataLake
from core.tql.engine import TQLParser, TQLExecutor
from core.features.registry import FeatureRegistry
from core.signals.engine import SignalEngine
from core.strategy.router import StrategyRouter
from core.execution.truth import ExecutionTruthEngine, ExecutionIntent, ExecutionActual, ExecutionLedger
from core.governor.risk import GovernorEngine, GovernorDecision, RiskMetrics, GovernorLedger
from core.learning.engine import LearningEngine, OutcomeRecord, FeatureWeights
from core.stability.engine import StabilityEngine
from core.portfolio.engine import PortfolioEngine, AssetSnapshot
from core.hardening.engine import HardeningEngine, SystemState, CircuitBreakerOpenError
from core.live.engine import LiveExecutionEngine, DeploymentStage, FailureModeMap, OrderIntent
from research.collector import (
    ResearchCollector, Phase3Adapter, SourceConfig,
)

DB_PATH = Path(__file__).parent.parent / "tradingos_data.db"
MARKER_DB = Path(__file__).parent.parent / "tradingos_markers.db"
PHASE3_LOG_DIR = Path(__file__).parent.parent.parent / "trading_brain_v4" / "shadow_logs"
DEFAULT_SYMBOL = "BTCUSDT"


def format_table(rows: list[dict], columns: list[str] | None = None) -> str:
    if not rows:
        return "No results."
    if not columns:
        all_keys = []
        for r in rows:
            for k in r.keys():
                if k not in all_keys:
                    all_keys.append(k)
        columns = all_keys[:8]
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val = row.get(col, "")
            if val is None:
                val = "null"
            widths[col] = max(widths[col], len(str(val)[:50]))
    header = " | ".join(col.ljust(widths[col]) for col in columns)
    sep = "-+-".join("-" * widths[col] for col in columns)
    lines = [header, sep]
    for row in rows:
        vals = []
        for col in columns:
            v = row.get(col, "")
            if v is None:
                v = "null"
            v = str(v)[:50]
            vals.append(v.ljust(widths[col]))
        lines.append(" | ".join(vals))
    return "\n".join(lines)


def cmd_collect():
    """One-shot collection from all sources."""
    dal = DataLake(DB_PATH)
    collector = ResearchCollector(dal, MARKER_DB)

    phase3 = Phase3Adapter(PHASE3_LOG_DIR, MARKER_DB)
    collector.add_source(phase3)

    stats = collector.collect_once()
    print(f"Collected: {stats.events_received} events")
    print(f"Written:   {stats.events_written} events")
    print(f"Skipped:   {stats.events_skipped} events")
    print(f"Errors:    {stats.events_errors} events")
    print(f"Quality:   {collector.get_stats()['quality']}")
    dal.close()


def cmd_daemon():
    """Run collector as daemon."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    dal = DataLake(DB_PATH)
    collector = ResearchCollector(dal, MARKER_DB)

    phase3 = Phase3Adapter(PHASE3_LOG_DIR, MARKER_DB)
    collector.add_source(phase3)

    collector.run_daemon(interval=60)


def cmd_query(args: str):
    dal = DataLake(DB_PATH)
    parser = TQLParser()
    executor = TQLExecutor(dal)
    parsed = parser.parse(args)
    if parsed.error:
        print(f"Error: {parsed.error}")
        dal.close()
        return
    start = time.time()
    result = executor.execute(parsed)
    elapsed = (time.time() - start) * 1000
    if result["status"] == "error":
        print(f"Error: {result['error']}")
        dal.close()
        return
    data = result.get("data", [])
    total = result.get("total", 0)
    count = result.get("count")
    if count is not None:
        print(f"Count: {count} ({elapsed:.1f}ms)")
    elif data:
        print(format_table(data))
        print(f"\n{total} rows ({elapsed:.1f}ms)")
    else:
        print(f"0 results ({elapsed:.1f}ms)")
    dal.close()


def cmd_stats():
    dal = DataLake(DB_PATH)
    total = dal.count()
    print(f"Total events: {total}")
    for etype in ["CandleClosed", "RegimeDetected", "SignalGenerated",
                   "PositionOpened", "PositionClosed", "TradeRecorded",
                   "Heartbeat", "FeatureComputed", "EdgeDiscovered"]:
        c = dal.count(event_type=etype)
        if c:
            print(f"  {etype}: {c}")
    dal.close()


def cmd_health():
    """Show collector health (persistent stats from Data Lake)."""
    dal = DataLake(DB_PATH)
    collector = ResearchCollector(dal, MARKER_DB)
    phase3 = Phase3Adapter(PHASE3_LOG_DIR, MARKER_DB)
    collector.add_source(phase3)
    health = collector.get_persistent_stats()
    print(json.dumps(health, indent=2, default=str))
    dal.close()


def cmd_features():
    """Compute and display current features."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    features = reg.compute_all(symbol=DEFAULT_SYMBOL)
    print(f"{'Feature':<25} {'Value':>12}  {'Version':<8}  Metadata")
    print("-" * 80)
    for name, fv in sorted(features.items()):
        meta = ", ".join(f"{k}={v}" for k, v in fv.metadata.items()) if fv.metadata else ""
        print(f"{name:<25} {fv.value:>12.2f}  {fv.feature_version:<8}  {meta}")
    print(f"\n{len(features)} features computed")
    dal.close()


def cmd_snapshot():
    """Show feature snapshot as JSON."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    reg.compute_all(symbol=DEFAULT_SYMBOL)
    snapshot = reg.to_snapshot()
    print(json.dumps(snapshot, indent=2, default=str))
    dal.close()


def cmd_signals():
    """Compute and display signal vector."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    engine = SignalEngine()

    features = reg.compute_all(symbol=DEFAULT_SYMBOL)

    # Build snapshot from computed features
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "features": {},
    }
    for name, fv in features.items():
        snapshot["features"][name] = fv.to_dict()

    vec = engine.compute(snapshot)

    print(f"{'Signal':<25} {'Value':>8}")
    print("-" * 40)
    print(f"{'trend':<25} {vec.trend:>8.3f}")
    print(f"{'reversal':<25} {vec.reversal:>8.3f}")
    print(f"{'momentum':<25} {vec.momentum:>8.3f}")
    print(f"{'volatility_pressure':<25} {vec.volatility_pressure:>8.3f}")
    print(f"{'market_trust':<25} {vec.market_trust:>8.3f}")
    print("-" * 40)
    print(f"{'directional_bias':<25} {vec.directional_bias:>+8.3f}")
    print(f"{'confidence':<25} {vec.confidence:>8.3f}")
    print(f"{'signal_quality':<25} {vec.signal_quality:>8.3f}")
    print(f"{'regime':<25} {vec.regime:>8}")
    print(f"{'features_used':<25} {vec.feature_count:>8}")
    print(f"\n{vec.summary()}")
    dal.close()


def cmd_signal_json():
    """Signal vector as JSON."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    engine = SignalEngine()

    features = reg.compute_all(symbol=DEFAULT_SYMBOL)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "features": {},
    }
    for name, fv in features.items():
        snapshot["features"][name] = fv.to_dict()

    vec = engine.compute(snapshot)
    print(json.dumps(vec.to_dict(), indent=2, default=str))
    dal.close()


def cmd_policy():
    """Full pipeline: features → signals → policy."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    sig_engine = SignalEngine()
    router = StrategyRouter()

    # 1. Features
    features = reg.compute_all(symbol=DEFAULT_SYMBOL)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "features": {name: fv.to_dict() for name, fv in features.items()},
    }

    # 2. Signals
    vec = sig_engine.compute(snapshot)
    signal_dict = vec.to_dict()

    # 3. Policy
    ctx = router.route(signal_dict, snapshot)

    print("=" * 60)
    print("  STRATEGY POLICY")
    print("=" * 60)
    print(f"  Mode:          {ctx.strategy_mode}  (conf={ctx.mode_confidence:.2f})")
    print(f"  Risk:          {ctx.risk_mode}  (score={ctx.risk_score:.2f})")
    print(f"  Intent:        {ctx.position_intent}")
    print(f"  Capital:       {ctx.capital_fraction:.0%} of equity")
    print(f"  Position size: {ctx.position_size_pct:.0%} per position")
    print(f"  Entry type:    {ctx.entry_type}")
    print(f"  Max positions: {ctx.max_positions}")
    print(f"  Cooldown:      {ctx.cooldown_seconds}s")
    print(f"  TP/SL:         {ctx.tp_style}/{ctx.sl_style}")
    print(f"  Reversal:      {'allowed' if ctx.allow_reversal_entries else 'blocked'}")
    print()
    print("  Mode scores:")
    for mode, score in sorted(ctx.mode_scores.items(), key=lambda x: -x[1]):
        bar = "#" * int(score * 40)
        print(f"    {mode:<8} {score:.3f} {bar}")
    print()
    print(f"  Reasoning: {ctx.reasoning}")
    print("=" * 60)
    dal.close()


def cmd_policy_json():
    """Policy as JSON."""
    dal = DataLake(Path(__file__).parent.parent / "tradingos_data.db")
    reg = FeatureRegistry(dal)
    sig_engine = SignalEngine()
    router = StrategyRouter()

    features = reg.compute_all(symbol=DEFAULT_SYMBOL)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "features": {name: fv.to_dict() for name, fv in features.items()},
    }
    vec = sig_engine.compute(snapshot)
    ctx = router.route(vec.to_dict(), snapshot)
    print(json.dumps(ctx.to_dict(), indent=2, default=str))
    dal.close()


def cmd_pipeline():
    """Full pipeline: collect → features → signals → policy."""
    # 1. Collect
    print("[1/4] Collecting events...")
    cmd_collect()

    # 2. Features
    print("\n[2/4] Computing features...")
    cmd_features()

    # 3. Signals
    print("\n[3/4] Computing signals...")
    cmd_signals()

    # 4. Policy
    print("\n[4/4] Selecting policy...")
    cmd_policy()


def cmd_demo():
    """Demonstrate T6 execution truth layer."""
    engine = ExecutionTruthEngine()

    print("=" * 60)
    print("  T6 EXECUTION TRUTH — DEMO")
    print("=" * 60)

    # 1. Create intent
    intent = ExecutionIntent(
        symbol=DEFAULT_SYMBOL, side="BUY", quantity=0.001,
        entry_price=62750.0, stop_loss=62500.0, take_profit=63200.0,
        order_type="MARKET", strategy_mode="TREND",
    )
    truth = engine.process_intent(intent)
    print(f"\n[1] INTENT created: {truth.intent.intent_id[:8]}...")
    print(f"    Side: {intent.side} {intent.quantity} @ {intent.entry_price}")
    print(f"    SL: {intent.stop_loss} TP: {intent.take_profit}")

    # 2. Simulate fill with slippage
    actual = ExecutionActual(
        broker_order_id="BYB-12345",
        fill_price=62755.3,          # +5.3 slippage
        fill_quantity=0.001,
        fill_time_ms=1200,
        slippage_bps=0.8,
        sl_actual=62500.0,           # SL correct
        tp_actual=63198.0,           # TP off by 2
        status="FILLED",
    )
    truth = engine.process_fill(truth, actual)
    print(f"\n[2] FILLED: {actual.broker_order_id}")
    print(f"    Fill: {actual.fill_price} (slippage: {actual.slippage_bps}bps)")
    print(f"    SL actual: {actual.sl_actual} TP actual: {actual.tp_actual}")

    # 3. Verify
    print(f"\n[3] VERIFICATION:")
    print(f"    State: {truth.truth_state}")
    print(f"    Drift type: {truth.drift_type}")
    print(f"    Drift score: {truth.drift_score:.3f}")
    if truth.drift_details.get("drifts"):
        for d in truth.drift_details["drifts"]:
            print(f"    - {d['type']}: severity={d['severity']:.2f}")

    # 4. SL/TP Guardian check
    print(f"\n[4] SL/TP GUARDIAN:")
    from core.execution.truth import SLTPGuardian
    guardian = SLTPGuardian()
    problems = guardian.verify_sl_tp({
        "entry_price": intent.entry_price,
        "stop_loss": actual.sl_actual,
        "take_profit": actual.tp_actual,
        "side": intent.side,
    })
    if problems:
        for p in problems:
            print(f"    - {p['type']}: severity={p['severity']:.2f}")
    else:
        print(f"    No issues found")

    # 5. Reconciliation demo
    print(f"\n[5] RECONCILIATION:")
    from core.execution.truth import Reconciler
    reconciler = Reconciler()
    internal = {
        "pos_1": {"symbol": "BTCUSDT", "side": "BUY", "entry_price": 62750},
        "pos_2": {"symbol": "ETHUSDT", "side": "SELL", "entry_price": 3500},
    }
    broker = {
        "BTCUSDT": {"side": "BUY", "qty": 0.001},
        # ETHUSDT missing — ghost position
    }
    report = reconciler.reconcile(internal, broker, [])
    print(f"    Internal: {report['internal_count']} positions")
    print(f"    Broker: {report['broker_count']} positions")
    print(f"    Health: {report['health']}")
    print(f"    Anomalies: {len(report['anomalies'])}")
    for a in report["anomalies"]:
        print(f"      - {a['type']}: {a.get('symbol', '')} (severity={a['severity']:.2f})")
    print(f"    Repairs needed: {len(report['repair_plan'])}")
    for r in report["repair_plan"]:
        print(f"      - {r['action']}: {r['reason']}")

    # 6. Stats
    print(f"\n[6] LEDGER STATS:")
    stats = engine.get_stats()
    print(f"    Total records: {stats['total']}")
    print(f"    By state: {stats['by_state']}")
    print(f"    Avg drift: {stats['avg_drift_score']}")

    print("\n" + "=" * 60)


def cmd_governor():
    """Risk governor check — можно ли торговать."""
    engine = GovernorEngine()
    ledger = GovernorLedger()

    # Build metrics from current state
    metrics = RiskMetrics(
        daily_pnl=0.0,
        total_pnl=0.0,
        peak_equity=10000.0,
        current_equity=10000.0,
        max_drawdown_pct=0.0,
        current_drawdown_pct=0.0,
        consecutive_losses=0,
        max_consecutive_losses=0,
        avg_drift_score=0.0,
        execution_errors_24h=0,
        volatility_pct=0.17,
        regime_instability=0.0,
        liquidity_score=0.9,
        open_positions=0,
        position_concentration_pct=0.0,
        system_health_score=1.0,
    )

    decision = engine.evaluate(metrics)
    ledger.write(decision)

    print("=" * 60)
    print("  RISK CONSTITUTION — GOVERNOR DECISION")
    print("=" * 60)
    print(f"  Level:              {decision.level}")
    print(f"  Allowed actions:    {decision.allowed_actions}")
    print(f"  Position size mult: {decision.position_size_multiplier:.2f}")
    print(f"  Max new positions:  {decision.max_new_positions}")
    print(f"  Freeze new orders:  {decision.freeze_new_orders}")
    print(f"  Flatten all:        {decision.flatten_all}")
    print(f"  Triggered rules:    {decision.triggered_rules or 'none'}")
    print(f"  Reasoning:          {decision.reasoning}")
    print("=" * 60)

    # Show stats
    stats = ledger.stats()
    print(f"\n  Ledger: {stats['total_decisions']} decisions, {stats['by_level']}")


def cmd_governor_stress():
    """Stress test: simulate various risk scenarios."""
    engine = GovernorEngine()
    scenarios = [
        ("Normal market", RiskMetrics(
            daily_pnl=50, current_equity=10000, peak_equity=10000,
            liquidity_score=0.9, system_health_score=1.0,
        )),
        ("Big drawdown", RiskMetrics(
            daily_pnl=-200, current_equity=9500, peak_equity=10000,
            current_drawdown_pct=5.0, liquidity_score=0.9, system_health_score=1.0,
        )),
        ("Max drawdown breach", RiskMetrics(
            daily_pnl=-500, current_equity=8800, peak_equity=10000,
            current_drawdown_pct=12.0, liquidity_score=0.9, system_health_score=1.0,
        )),
        ("Consecutive losses", RiskMetrics(
            daily_pnl=-50, current_equity=9700, peak_equity=10000,
            consecutive_losses=6, liquidity_score=0.9, system_health_score=1.0,
        )),
        ("Execution instability", RiskMetrics(
            current_equity=9900, peak_equity=10000,
            avg_drift_score=0.7, execution_errors_24h=8,
            liquidity_score=0.9, system_health_score=1.0,
        )),
        ("Liquidity collapse", RiskMetrics(
            current_equity=9900, peak_equity=10000,
            liquidity_score=0.1, system_health_score=1.0,
        )),
        ("System failure", RiskMetrics(
            current_equity=9900, peak_equity=10000,
            system_health_score=0.3, module_failures=2,
            liquidity_score=0.9,
        )),
    ]

    print("=" * 70)
    print("  RISK GOVERNOR — STRESS TEST")
    print("=" * 70)
    print(f"  {'Scenario':<25} {'Level':<10} {'Actions':<20} {'SizeMult':<10}")
    print("-" * 70)

    for name, metrics in scenarios:
        decision = engine.evaluate(metrics)
        actions = "/".join(decision.allowed_actions[:2])
        print(f"  {name:<25} {decision.level:<10} {actions:<20} {decision.position_size_multiplier:.2f}")

    print("=" * 70)


def cmd_learn():
    """Run T7 learning cycle with demo outcomes."""
    engine = LearningEngine()

    # Seed with demo outcomes for testing
    import random
    random.seed(42)
    regimes = ["TREND", "RANGE", "TREND", "TREND", "RANGE"]
    modes = ["TREND", "GRID", "TREND", "SCALP", "GRID"]

    for i in range(15):
        regime = random.choice(regimes)
        mode = random.choice(modes)
        is_win = random.random() > 0.4
        pnl = random.uniform(0.002, 0.015) if is_win else random.uniform(-0.012, -0.001)

        outcome = OutcomeRecord(
            trade_id=f"demo_{i:03d}",
            timestamp=(datetime.now(timezone.utc) - timedelta(hours=15-i)).isoformat(),
            symbol=DEFAULT_SYMBOL,
            regime=regime,
            strategy_mode=mode,
            direction="LONG" if random.random() > 0.5 else "SHORT",
            entry_price=62500 + random.uniform(-500, 500),
            exit_price=62500 + random.uniform(-500, 500),
            pnl=round(pnl, 6),
            pnl_pct=round(pnl * 100, 4),
            duration_seconds=random.randint(300, 3600),
            slippage_bps=random.uniform(0.1, 2.0),
            drift_score=random.uniform(0, 0.3),
            signal_confidence=random.uniform(0.5, 0.95),
            feature_snapshot={
                "regime_strength": {"value": random.uniform(0.6, 1.0)},
                "adx_trend_strength": {"value": random.uniform(20, 80)},
                "volume_notional": {"value": random.uniform(500e6, 2e9)},
                "trend_consistency": {"value": random.uniform(40, 100)},
                "atr_smoothed": {"value": random.uniform(100, 300)},
                "cycle_speed": {"value": random.uniform(0, 30)},
                "event_density_1h": {"value": random.uniform(1, 10)},
                "price_last": {"value": 62500 + random.uniform(-500, 500)},
            },
            signal_snapshot={
                "trend": random.uniform(0.3, 1.0),
                "reversal": random.uniform(0, 0.5),
                "momentum": random.uniform(0.3, 1.0),
                "volatility_pressure": random.uniform(0, 0.5),
                "market_trust": random.uniform(0.4, 0.9),
            },
        )
        engine.record_outcome(outcome)

    # Run learning
    result = engine.learn()

    print("=" * 60)
    print("  T7 LEARNING LOOP")
    print("=" * 60)
    print(f"  Status:            {result['status']}")
    print(f"  Outcomes analyzed: {result.get('outcomes_analyzed', 0)}")
    print(f"  Weights version:   {result.get('new_version', 0)}")
    print(f"  Adjustments:       {result.get('adjustments', 0)}")
    print(f"  Best regime:       {result.get('best_regime', 'N/A')}")
    print(f"  Worst regime:      {result.get('worst_regime', 'N/A')}")

    if result.get("drift_trend"):
        dt = result["drift_trend"]
        print(f"  Drift trend:       {dt['trend']} (avg={dt['avg']:.3f})")

    if result.get("regime_memory"):
        print("\n  Regime Memory:")
        for regime, mem in result["regime_memory"].items():
            print(f"    {regime}: trades={mem['total_trades']} win_rate={mem['win_rate']:.1%} avg_pnl={mem['avg_pnl']:.6f}")

    if result.get("weights"):
        w = result["weights"]
        print("\n  Feature Weights:")
        for k in ["regime_strength", "adx_trend_strength", "volume_notional",
                   "trend_consistency", "atr_smoothed"]:
            print(f"    {k:<25} {w.get(k, 0):.3f}")
        print("\n  Signal Weights:")
        for k in ["trend_weight", "reversal_weight", "momentum_weight"]:
            print(f"    {k:<25} {w.get(k, 0):.3f}")

    print("=" * 60)


def cmd_learn_stats():
    """Show learning stats."""
    engine = LearningEngine()
    stats = engine.get_stats()

    print("=" * 60)
    print("  T7 LEARNING STATS")
    print("=" * 60)
    print(f"  Total outcomes:    {stats['total_outcomes']}")
    print(f"  Win rate:          {stats['win_rate']:.1%}")
    print(f"  Total PnL:         {stats['total_pnl']:.6f}")
    print(f"  Avg PnL:           {stats['avg_pnl']:.6f}")
    print(f"  Weights version:   {stats['weights_version']}")
    print(f"  Adjustments:       {stats['total_adjustments']}")

    if stats.get("regime_memory"):
        print("\n  Regime Memory:")
        for regime, mem in stats["regime_memory"].items():
            print(f"    {regime}: trades={mem['total_trades']} "
                  f"win_rate={mem['win_rate']:.1%} "
                  f"avg_pnl={mem['avg_pnl']:.6f} "
                  f"best={mem['best_trade_pnl']:.6f} "
                  f"worst={mem['worst_trade_pnl']:.6f}")

    if stats.get("drift_trend"):
        dt = stats["drift_trend"]
        print(f"\n  Drift trend: {dt['trend']} (avg={dt['avg']:.3f}, slope={dt['slope']:.4f})")

    print("=" * 60)


def cmd_full_pipeline():
    """Full pipeline: collect → features → signals → policy → governor → learn."""
    print("[1/6] Collecting events...")
    cmd_collect()

    print("\n[2/6] Computing features...")
    cmd_features()

    print("\n[3/6] Computing signals...")
    cmd_signals()

    print("\n[4/6] Selecting policy...")
    cmd_policy()

    print("\n[5/6] Risk governor check...")
    cmd_governor()

    print("\n[6/6] Learning cycle...")
    cmd_learn()


def cmd_stability():
    """Show stability status."""
    stability = StabilityEngine()
    state = stability.get_state()

    print("=" * 60)
    print("  STABILITY LAYER STATUS")
    print("=" * 60)
    freeze = state["freeze"]
    print(f"  Frozen:           {freeze['is_frozen']}")
    if freeze["is_frozen"]:
        print(f"  Reason:           {freeze['reason']}")
    print(f"  Freeze count:     {freeze['freeze_count']}")
    print(f"  Rollback snaps:   {state['rollback_snapshots']}")
    print(f"  Audit entries:    {state['audit_entries']}")
    print("=" * 60)


def cmd_audit():
    """Show audit trail."""
    stability = StabilityEngine()
    entries = stability.get_audit_trail(limit=10)

    print("=" * 60)
    print("  AUDIT TRAIL (last 10)")
    print("=" * 60)
    if not entries:
        print("  No entries yet")
    for e in entries:
        old = json.dumps(e["old_json"])[:50] if e["old_json"] else "-"
        new = json.dumps(e["new_json"])[:50] if e["new_json"] else "-"
        print(f"  [{e['timestamp'][:19]}] {e['action']}: {e['reason'] or e['triggered_by']}")
    print("=" * 60)


def cmd_stability_demo():
    """Demonstrate stability features."""
    stability = StabilityEngine()

    print("=" * 60)
    print("  STABILITY DEMO")
    print("=" * 60)

    # 1. Freeze check — insufficient data
    print("\n[1] Freeze: insufficient data")
    result = stability.check_stability(
        outcomes_count=3, recent_win_rate=0.5, previous_win_rate=0.5,
        drift_trend="stable", governor_level="GREEN", current_regime="TREND",
    )
    print(f"    Frozen: {result['frozen']} Reason: {result['freeze_reason']}")

    # 2. Freeze check — noise regime
    print("\n[2] Freeze: chaos regime")
    result = stability.check_stability(
        outcomes_count=20, recent_win_rate=0.5, previous_win_rate=0.5,
        drift_trend="stable", governor_level="GREEN", current_regime="CHAOS",
    )
    print(f"    Frozen: {result['frozen']} Reason: {result['freeze_reason']}")

    # 3. Unfreeze — good conditions
    print("\n[3] Unfreeze: good conditions")
    result = stability.check_stability(
        outcomes_count=20, recent_win_rate=0.65, previous_win_rate=0.50,
        drift_trend="stable", governor_level="GREEN", current_regime="TREND",
    )
    print(f"    Frozen: {result['frozen']} Reason: {result['freeze_reason'] or 'none'}")

    # 4. Noise filter
    print("\n[4] Noise filter:")
    normal = [0.01, 0.02, -0.01, 0.015, -0.005, 0.008, -0.012, 0.011]
    outlier = normal + [0.5]  # 50% outlier
    filtered = stability.filter_outcomes([
        type('O', (), {'pnl': p, 'is_win': p > 0, 'regime': 'TREND',
             'feature_snapshot': {}, 'signal_snapshot': {}, 'policy_snapshot': {}})()
        for p in outlier
    ])
    print(f"    Input: {len(outlier)} outcomes, filtered: {len(filtered)} (removed {len(outlier) - len(filtered)} outliers)")

    # 5. Weight snapshot + rollback check
    print("\n[5] Weight snapshot + rollback:")
    stability.save_weight_snapshot(
        weights={"trend": 0.3, "reversal": 0.2},
        metrics={"win_rate": 0.6, "avg_pnl": 0.001},
        version=1,
    )
    stability.save_weight_snapshot(
        weights={"trend": 0.4, "reversal": 0.15},
        metrics={"win_rate": 0.55, "avg_pnl": -0.001},
        version=2,
    )
    needs_rollback = stability.check_stability(
        outcomes_count=20, recent_win_rate=0.45, previous_win_rate=0.55,
        drift_trend="worsening", governor_level="GREEN", current_regime="TREND",
    )
    print(f"    Needs rollback: {needs_rollback['needs_rollback']}")
    target = stability.get_rollback_target()
    if target:
        print(f"    Rollback to: version={target.get('version', '?')} win_rate={target.get('win_rate', '?')}")

    # 6. Audit trail
    print("\n[6] Audit trail:")
    entries = stability.get_audit_trail(limit=5)
    for e in entries:
        print(f"    [{e['timestamp'][:19]}] {e['action']}: {e['reason'] or ''}")

    print("\n" + "=" * 60)


def cmd_portfolio():
    """Portfolio Intelligence — multi-market allocation demo."""
    engine = PortfolioEngine()

    # Simulate 5 assets
    import random
    random.seed(42)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    snapshots = {}

    for sym in symbols:
        base_price = {"BTCUSDT": 62750, "ETHUSDT": 3500, "SOLUSDT": 150,
                      "BNBUSDT": 580, "XRPUSDT": 0.52}[sym]
        price = base_price * (1 + random.uniform(-0.02, 0.02))

        # Feed prices for correlation
        for _ in range(30):
            engine._corr.update(sym, price * (1 + random.uniform(-0.005, 0.005)))
            price *= (1 + random.uniform(-0.003, 0.003))

        snapshots[sym] = AssetSnapshot(
            symbol=sym,
            price=price,
            regime=random.choice(["TREND", "RANGE", "TREND", "RANGE"]),
            regime_strength=random.uniform(0.5, 1.0),
            trend=random.uniform(0.3, 1.0),
            momentum=random.uniform(0.3, 1.0),
            volatility=random.uniform(0.1, 0.6),
            signal_confidence=random.uniform(0.5, 0.95),
            directional_bias=random.uniform(-1, 1),
            strategy_mode=random.choice(["TREND", "SCALP", "GRID"]),
            risk_mode=random.choice(["AGGRESSIVE", "NORMAL"]),
        )

    result = engine.process(snapshots)

    print("=" * 60)
    print("  PORTFOLIO INTELLIGENCE — MULTI-MARKET ALLOCATION")
    print("=" * 60)

    print("\n  Capital Allocation:")
    for sym, w in sorted(result["weights"].items(), key=lambda x: -x[1]):
        bar = "#" * int(w * 60)
        print(f"    {sym:<10} {w:>6.1%}  {bar}")

    print(f"\n  Diversification:  {result['diversification_score']:.3f}")
    print(f"  Avg correlation:  {result['correlation_avg']:.3f}")
    print(f"  Total exposure:   {result['exposure']['total_exposure']:.1%}")
    print(f"  Exposure healthy: {result['exposure']['healthy']}")

    gov = result["governor"]
    print(f"\n  Portfolio risk:   {gov['level']}")
    print(f"  Asset count:      {gov['asset_count']}")
    print(f"  Regime dist:      {gov['regime_distribution']}")

    if gov["alerts"]:
        print("\n  Alerts:")
        for a in gov["alerts"]:
            print(f"    [{a['level']}] {a['rule']}: {a.get('symbols', a.get('regime', ''))}")

    # Correlation matrix
    print("\n  Correlation Matrix:")
    matrix = engine.get_correlation_matrix(symbols)
    header = f"  {'':>10}" + "".join(f"{s[:6]:>8}" for s in symbols)
    print(header)
    for sym_a in symbols:
        row = f"  {sym_a:<10}"
        for sym_b in symbols:
            c = matrix[sym_a][sym_b]
            row += f"{c:>8.3f}"
        print(row)

    print("=" * 60)


def cmd_portfolio_json():
    """Portfolio allocation as JSON."""
    engine = PortfolioEngine()
    import random
    random.seed(42)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    snapshots = {}

    for sym in symbols:
        base_price = {"BTCUSDT": 62750, "ETHUSDT": 3500, "SOLUSDT": 150,
                      "BNBUSDT": 580, "XRPUSDT": 0.52}[sym]
        price = base_price * (1 + random.uniform(-0.02, 0.02))

        for _ in range(30):
            engine._corr.update(sym, price * (1 + random.uniform(-0.005, 0.005)))
            price *= (1 + random.uniform(-0.003, 0.003))

        snapshots[sym] = AssetSnapshot(
            symbol=sym, price=price,
            regime=random.choice(["TREND", "RANGE"]),
            regime_strength=random.uniform(0.5, 1.0),
            trend=random.uniform(0.3, 1.0),
            momentum=random.uniform(0.3, 1.0),
            volatility=random.uniform(0.1, 0.6),
            signal_confidence=random.uniform(0.5, 0.95),
            directional_bias=random.uniform(-1, 1),
            strategy_mode=random.choice(["TREND", "SCALP"]),
            risk_mode="NORMAL",
        )

    result = engine.process(snapshots)
    print(json.dumps(result, indent=2, default=str))


def cmd_system_status():
    """Full system status: all modules + hardening."""
    engine = HardeningEngine()
    boot_result = engine.boot()

    print("=" * 60)
    print("  TRADINGOS v2 — SYSTEM STATUS")
    print("=" * 60)
    print(f"  State:     {boot_result['final_state']}")
    print(f"  Recovery:  {boot_result['recovery']}")

    if boot_result.get("previous_state"):
        ps = boot_result["previous_state"]
        print(f"  Previous:  {ps.get('system_state', 'unknown')}")

    consistency = boot_result["consistency"]
    print(f"\n  Consistency:  {consistency['passed']}/{consistency['total']} checks passed")
    if consistency["failed"]:
        print(f"  Failed:       {consistency['failed']}")

    status = engine.get_system_status()
    health = status["health"]
    print(f"\n  Health:       {health['health']}")
    print(f"  Metrics:      {health['total_metrics']}")
    print(f"  Alerts:       {health['recent_alerts']} (high: {health['high_alerts']})")

    cbs = status["circuit_breakers"]
    if cbs:
        print(f"\n  Circuit Breakers:")
        for name, cb in cbs.items():
            print(f"    {name}: {cb['state']} (failures={cb['failure_count']})")

    print("=" * 60)


def cmd_hardening_demo():
    """Demonstrate hardening features."""
    engine = HardeningEngine()

    print("=" * 60)
    print("  HARDENING DEMO")
    print("=" * 60)

    # 1. Boot
    print("\n[1] Boot sequence:")
    result = engine.boot()
    print(f"    State: {result['final_state']}")
    print(f"    Recovery: {result['recovery']}")
    print(f"    Consistency: {result['consistency']['passed']}/{result['consistency']['total']}")

    # 2. Circuit breaker
    print("\n[2] Circuit breaker:")
    cb = engine.get_circuit_breaker("data_lake")
    print(f"    Initial state: {cb.state}")

    # Simulate failures
    for i in range(6):
        try:
            cb.call(lambda: 1/0)  # will raise ZeroDivisionError
        except Exception:
            pass
    print(f"    After 6 failures: {cb.state}")

    # 3. Health monitoring
    print("\n[3] Health monitoring:")
    engine.record_health({"memory_mb": 128, "cpu_pct": 15, "latency_ms": 45})
    engine.record_health({"memory_mb": 130, "cpu_pct": 18, "latency_ms": 50})
    engine.record_health({"memory_mb": 520, "cpu_pct": 95, "latency_ms": 8000})  # alert!

    health = engine._health.get_health()
    print(f"    Health: {health['health']}")
    print(f"    Alerts: {health['recent_alerts']} (high: {health['high_alerts']})")

    # 4. Metric trends
    print("\n[4] Metric trends:")
    for name in ["memory_mb", "cpu_pct", "latency_ms"]:
        trend = engine._health.get_metric_trend(name)
        print(f"    {name}: {trend['trend']} (current={trend['current']})")

    # 5. System status
    print("\n[5] System status:")
    status = engine.get_system_status()
    print(f"    State: {status['state']}")
    print(f"    Health: {status['health']['health']}")
    print(f"    Snapshot: {status['crash_recovery']['has_snapshot']}")

    # 6. Graceful shutdown
    print("\n[6] Graceful shutdown:")
    engine.shutdown()
    print(f"    State after shutdown: {engine._system_state}")

    print("\n" + "=" * 60)


def cmd_live_demo():
    """Live execution demo — staged rollout."""
    engine = LiveExecutionEngine()

    print("=" * 60)
    print("  LIVE EXECUTION LAYER — DEPLOYMENT DEMO")
    print("=" * 60)

    # 1. Current stage
    print(f"\n  Current stage: {engine._stage.value}")

    # 2. Execute paper order
    print("\n[1] Paper order execution:")
    intent = OrderIntent(
        intent_id="demo_001",
        symbol=DEFAULT_SYMBOL,
        side="BUY",
        quantity=0.001,
        order_type="MARKET",
        limit_price=62750,
        strategy_mode="TREND",
    )
    result = engine.execute_order(intent)
    print(f"    Status: {result.status}")
    print(f"    Fill: {result.fill_price} (slippage: {result.slippage_bps}bps)")
    print(f"    Broker ID: {result.broker_order_id}")

    # 3. Transition check
    print("\n[2] Transition to PAPER_LIVE:")
    check = engine.can_transition(DeploymentStage.PAPER_LIVE)
    print(f"    Allowed: {check['allowed']}")
    if check.get("requirements"):
        for req in check["requirements"]:
            print(f"    - {req['name']}: {req['description']}")

    # 4. Failure modes
    print("\n[3] Failure Mode Map:")
    print(FailureModeMap.summary())

    # 5. Status
    print("\n[4] Execution status:")
    status = engine.get_status()
    print(f"    Stage: {status['stage']}")
    print(f"    Open positions: {status['executor']['open_positions']}")
    print(f"    Total executions: {status['executor']['total_executions']}")

    print("\n" + "=" * 60)


def cmd_failure_modes():
    """Show all failure modes."""
    print("=" * 60)
    print("  FAILURE MODE MAP — LIVE EXECUTION")
    print("=" * 60)
    for m in FailureModeMap.get_all():
        print(f"\n  [{m['severity']:>8}] {m['mode']}")
        print(f"    What: {m['description']}")
        print(f"    How:  {m['detection']}")
        print(f"    Fix:  {m['mitigation']}")
    print("\n" + "=" * 60)


def cmd_deploy_check():
    """Check deployment readiness for each stage."""
    engine = LiveExecutionEngine()

    print("=" * 60)
    print("  DEPLOYMENT READINESS CHECK")
    print("=" * 60)

    for stage in [DeploymentStage.PAPER_LIVE, DeploymentStage.MICRO_LIVE, DeploymentStage.FULL_LIVE]:
        check = engine.can_transition(stage)
        status = "READY" if check["allowed"] else "NOT READY"
        print(f"\n  {stage.value}: {status}")
        for req in check.get("requirements", []):
            print(f"    [PASS] {req['name']}: {req['description']}")

    print("\n" + "=" * 60)


# ── Crypto Commands ────────────────────────────────────────

def cmd_crypto_collect():
    """Collect OHLCV from Bybit testnet → Data Lake."""
    import asyncio
    import importlib.util
    import os
    from pathlib import Path

    _ca_spec = importlib.util.spec_from_file_location(
        "crypto_adapter", str(Path(__file__).parent.parent / "adapters" / "crypto_adapter.py")
    )
    _ca_mod = importlib.util.module_from_spec(_ca_spec)
    _ca_spec.loader.exec_module(_ca_mod)
    CryptoAdapter = _ca_mod.CryptoAdapter

    symbol = DEFAULT_SYMBOL

    print("=" * 60)
    print("  CRYPTO COLLECT — Bybit testnet")
    print("=" * 60)

    adapter = CryptoAdapter(
        api_key=os.environ.get("BYBIT_API_KEY", ""),
        api_secret=os.environ.get("BYBIT_API_SECRET", ""),
        testnet=True,
    )

    async def run():
        ok = await adapter.initialize()
        if not ok:
            print("  FAIL: cannot connect to Bybit testnet")
            return
        written = await adapter.collect_once(symbol, timeframe="15m", limit=200)
        print(f"  Wrote {written} events for {symbol}")
        stats = adapter.get_stats()
        print(f"  ATR: {stats['atr']}  ADX: {stats['adx']}")
        adapter.close()

    asyncio.run(run())


def cmd_crypto_sweep():
    """Run LiquiditySweep strategy on Data Lake data."""
    import importlib.util
    import json
    from pathlib import Path

    _ls_spec = importlib.util.spec_from_file_location(
        "liquidity_sweep", str(Path(__file__).parent.parent / "core" / "strategy" / "liquidity_sweep.py")
    )
    _ls_mod = importlib.util.module_from_spec(_ls_spec)
    _ls_spec.loader.exec_module(_ls_mod)
    LiquiditySweepStrategy = _ls_mod.LiquiditySweepStrategy

    symbol = DEFAULT_SYMBOL

    print("=" * 60)
    print("  LIQUIDITY SWEEP — Strategy Scan")
    print("=" * 60)

    lake = DataLake(DB_PATH)
    strategy = LiquiditySweepStrategy(lookback=50, sweep_threshold=0.0015)

    rows = lake.query(event_type="CandleClosed", symbol=symbol, limit=200)
    rows.reverse()

    signals = []
    for row in rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        market_data = {
            "symbol": symbol,
            "high": payload.get("high", 0),
            "low": payload.get("low", 0),
            "close": payload.get("close", 0),
            "volume": payload.get("volume", 0),
            "regime": payload.get("regime", "UNKNOWN"),
        }
        signal = strategy.analyze(market_data)
        if signal:
            signals.append(signal)

    stats = strategy.get_stats()
    print(f"  Bars analyzed:  {stats['total_analyzed']}")
    print(f"  Signals:        {stats['accepted']}")
    print(f"  Accept rate:    {stats['accept_rate']:.2%}")
    print(f"  Sweeps found:   {stats['sweeps_detected']}")

    if signals:
        print(f"\n  Last {min(10, len(signals))} signals:")
        for s in signals[-10:]:
            meta = s.metadata or {}
            print(f"    {s.direction:5s} conf={s.confidence:.3f} "
                  f"type={meta.get('sweep_type','?'):15s} "
                  f"strength={meta.get('strength',0):.6f}")

    lake.close()


def cmd_crypto_daemon():
    """Continuously collect from Bybit testnet."""
    import asyncio
    import importlib.util
    from pathlib import Path

    _ca_spec = importlib.util.spec_from_file_location(
        "crypto_adapter", str(Path(__file__).parent.parent / "adapters" / "crypto_adapter.py")
    )
    _ca_mod = importlib.util.module_from_spec(_ca_spec)
    _ca_spec.loader.exec_module(_ca_mod)
    CryptoAdapter = _ca_mod.CryptoAdapter

    symbol = DEFAULT_SYMBOL
    interval = 60

    print(f"Crypto daemon: collecting {symbol} every {interval}s")

    adapter = CryptoAdapter(
        api_key=os.environ.get("BYBIT_API_KEY", ""),
        api_secret=os.environ.get("BYBIT_API_SECRET", ""),
        testnet=True,
    )

    async def run():
        ok = await adapter.initialize()
        if not ok:
            print("  FAIL: cannot connect")
            return
        try:
            while True:
                written = await adapter.collect_once(symbol, timeframe="15m", limit=50)
                print(f"[{datetime.now().isoformat()}] Wrote {written} events")
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            adapter.close()

    asyncio.run(run())


def cmd_crypto_shadow():
    """Run A/B shadow observation (48h experiment)."""
    import subprocess
    import sys

    symbol = DEFAULT_SYMBOL
    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / "observe_crypto.py"),
        "--symbol", symbol,
        "--timeframe", "5m",
        "--interval", "60",
        "--profiles", "A,B",
    ]
    print(f"Starting A/B shadow: {symbol} 5m profiles=A,B")
    print(f"Command: {' '.join(cmd)}")
    print("Press Ctrl+C to stop")
    print()
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("Stopped.")


def main():
    if len(sys.argv) < 2:
        print("""TradingOS CLI

Commands:
  collect          One-shot collect from all sources
  daemon           Run collector continuously
  query "TQL..."   Run TQL query
  stats            Show Data Lake statistics
  health           Show collector health
  features         Compute and show current features
  snapshot         Feature snapshot as JSON
  signals          Compute and show signal vector
  signal-json      Signal vector as JSON
  policy           Full features → signals → policy
  policy-json      Policy as JSON
  pipeline         Full collect → features → signals → policy
  demo             T6 execution truth demo
  governor         Risk governor check
  governor-stress  Stress test risk scenarios
  learn            Run T7 learning cycle
  learn-stats      Show learning statistics
  full             Full 6-step pipeline
  stability        Show stability status
  audit            Show audit trail
  stability-demo   Demonstrate stability features
  portfolio        Multi-market capital allocation
  portfolio-json   Portfolio allocation as JSON
  status           Full system status
  hardening-demo   Demonstrate hardening features
  live-demo        Live execution demo
  failure-modes    Show all failure modes
  deploy-check     Check deployment readiness

  crypto-collect   Collect OHLCV from Bybit testnet → Data Lake
  crypto-sweep     Run LiquiditySweep strategy on Data Lake
  crypto-daemon    Continuously collect from Bybit testnet
  crypto-shadow    Run A/B shadow observation (48h experiment)

Examples:
  tradingos full
  tradingos live-demo
  tradingos failure-modes
  tradingos deploy-check
  tradingos crypto-collect --symbol BTCUSDT
  tradingos crypto-sweep --symbol ETHUSDT
  tradingos crypto-shadow --symbol BTCUSDT --timeframe 5m --profiles A,B
""")
        return

    cmd = sys.argv[1]
    # Support --symbol XXXUSDT flag
    remaining = sys.argv[2:]
    global DEFAULT_SYMBOL
    for i, arg in enumerate(remaining):
        if arg == "--symbol" and i + 1 < len(remaining):
            DEFAULT_SYMBOL = remaining[i + 1]
            remaining = remaining[:i] + remaining[i + 2:]
            break
    args = " ".join(remaining) if remaining else ""

    if cmd == "collect":
        cmd_collect()
    elif cmd == "daemon":
        cmd_daemon()
    elif cmd == "query":
        cmd_query(args)
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "health":
        cmd_health()
    elif cmd == "features":
        cmd_features()
    elif cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "signals":
        cmd_signals()
    elif cmd == "signal-json":
        cmd_signal_json()
    elif cmd == "policy":
        cmd_policy()
    elif cmd == "policy-json":
        cmd_policy_json()
    elif cmd == "pipeline":
        cmd_pipeline()
    elif cmd == "demo":
        cmd_demo()
    elif cmd == "governor":
        cmd_governor()
    elif cmd == "governor-stress":
        cmd_governor_stress()
    elif cmd == "learn":
        cmd_learn()
    elif cmd == "learn-stats":
        cmd_learn_stats()
    elif cmd == "full":
        cmd_full_pipeline()
    elif cmd == "stability":
        cmd_stability()
    elif cmd == "audit":
        cmd_audit()
    elif cmd == "stability-demo":
        cmd_stability_demo()
    elif cmd == "portfolio":
        cmd_portfolio()
    elif cmd == "portfolio-json":
        cmd_portfolio_json()
    elif cmd == "status":
        cmd_system_status()
    elif cmd == "hardening-demo":
        cmd_hardening_demo()
    elif cmd == "live-demo":
        cmd_live_demo()
    elif cmd == "failure-modes":
        cmd_failure_modes()
    elif cmd == "deploy-check":
        cmd_deploy_check()
    elif cmd == "crypto-collect":
        cmd_crypto_collect()
    elif cmd == "crypto-sweep":
        cmd_crypto_sweep()
    elif cmd == "crypto-daemon":
        cmd_crypto_daemon()
    elif cmd == "crypto-shadow":
        cmd_crypto_shadow()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
