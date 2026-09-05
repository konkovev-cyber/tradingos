"""T6 Execution Truth Layer — слой физической реальности.

Не торгует. Доказывает, что торговля произошла правильно.
Обнаруживает phantom orders, SL/TP divergence, position drift.
Восстанавливает консистентность при расхождениях.
"""
import json
import uuid
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from pathlib import Path


# ── Truth State ────────────────────────────────────────────

class TruthState(str, Enum):
    INTENT = "INTENT"                    # решение принято
    SUBMITTED = "SUBMITTED"              # ордер отправлен
    ACCEPTED = "ACCEPTED"                # биржа приняла
    PARTIAL_FILL = "PARTIAL_FILL"        # частичное исполнение
    FILLED = "FILLED"                    # полное исполнение
    VERIFIED = "VERIFIED"                # верифицировано
    DRIFT_DETECTED = "DRIFT_DETECTED"    # расхождение с broker
    REPAIRING = "REPAIRING"              # восстановление
    REPAIRED = "REPAIRED"                # восстановлено
    CLOSED = "CLOSED"                    # позиция закрыта
    FAILED = "FAILED"                    # не удалось восстановить


class DriftType(str, Enum):
    NONE = "NONE"
    PHANTOM_POSITION = "PHANTOM_POSITION"      # internal есть, broker нет
    GHOST_POSITION = "GHOST_POSITION"          # broker есть, internal нет
    SL_MISSING = "SL_MISSING"                  # SL не установлен
    SL_DRIFT = "SL_DRIFT"                      # SL отличается от intent
    TP_MISSING = "TP_MISSING"
    TP_DRIFT = "TP_DRIFT"
    PRICE_DRIFT = "PRICE_DRIFT"                # fill ≠ intent price
    SIZE_DRIFT = "SIZE_DRIFT"                  # qty ≠ intent qty
    STATE_CORRUPTION = "STATE_CORRUPTION"      # неопознанное расхождение


# ── Execution Intent ───────────────────────────────────────

@dataclass
class ExecutionIntent:
    """Что система решила сделать."""
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    symbol: str = "BTCUSDT"
    side: str                 # BUY / SELL — обязательное поле (F5: без дефолта)
    quantity: float = 0.0
    entry_price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    order_type: str = "MARKET"     # MARKET / LIMIT
    strategy_mode: str = "TREND"
    policy_ref: str = ""           # reference to StrategyContext

    def to_dict(self) -> dict:
        return asdict(self)


# ── Execution Actual ───────────────────────────────────────

@dataclass
class ExecutionActual:
    """Что реально произошло на бирже."""
    broker_order_id: str = ""
    fill_price: float = 0.0
    fill_quantity: float = 0.0
    fill_time_ms: float = 0.0
    slippage_bps: float = 0.0
    commission: float = 0.0
    sl_actual: Optional[float] = None
    tp_actual: Optional[float] = None
    status: str = "PENDING"         # PENDING / FILLED / PARTIAL / REJECTED / CANCELLED
    reject_reason: str = ""
    raw_response: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Execution Truth Record ─────────────────────────────────

@dataclass
class ExecutionTruth:
    """Полная запись: intent + actual + diff + state."""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intent: ExecutionIntent = field(default_factory=ExecutionIntent)
    actual: ExecutionActual = field(default_factory=ExecutionActual)

    # Truth analysis
    truth_state: str = TruthState.INTENT
    drift_type: str = DriftType.NONE
    drift_score: float = 0.0       # 0 = perfect, 1 = severe
    drift_details: dict = field(default_factory=dict)

    # Repair
    repair_attempts: int = 0
    repair_history: list = field(default_factory=list)
    final_state: str = ""

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    verified_at: Optional[str] = None
    closed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["intent"] = self.intent.to_dict()
        d["actual"] = self.actual.to_dict()
        return d


# ── Verifier ───────────────────────────────────────────────

class ExecutionVerifier:
    """Сравнивает intent с actual, определяет drift."""

    SLIPPAGE_THRESHOLD_BPS = 50     # max acceptable slippage
    FILL_DELAY_THRESHOLD_MS = 5000  # max fill delay

    def verify(self, truth: ExecutionTruth) -> ExecutionTruth:
        """Полная верификация truth record."""
        intent = truth.intent
        actual = truth.actual

        drifts = []

        # 1. Price drift
        if actual.fill_price > 0 and intent.entry_price > 0:
            price_diff_bps = abs(actual.fill_price - intent.entry_price) / intent.entry_price * 10000
            if price_diff_bps > self.SLIPPAGE_THRESHOLD_BPS:
                drifts.append({
                    "type": DriftType.PRICE_DRIFT,
                    "severity": min(price_diff_bps / 100, 1.0),
                    "details": {
                        "expected": intent.entry_price,
                        "actual": actual.fill_price,
                        "diff_bps": round(price_diff_bps, 1),
                    },
                })

        # 2. Size drift
        if actual.fill_quantity > 0 and intent.quantity > 0:
            if abs(actual.fill_quantity - intent.quantity) / intent.quantity > 0.01:
                drifts.append({
                    "type": DriftType.SIZE_DRIFT,
                    "severity": 0.5,
                    "details": {
                        "expected": intent.quantity,
                        "actual": actual.fill_quantity,
                    },
                })

        # 3. SL drift
        if intent.stop_loss is not None:
            if actual.sl_actual is None:
                drifts.append({
                    "type": DriftType.SL_MISSING,
                    "severity": 0.9,
                    "details": {"expected_sl": intent.stop_loss},
                })
            elif abs(actual.sl_actual - intent.stop_loss) / intent.stop_loss > 0.001:
                drifts.append({
                    "type": DriftType.SL_DRIFT,
                    "severity": 0.6,
                    "details": {
                        "expected": intent.stop_loss,
                        "actual": actual.sl_actual,
                    },
                })

        # 4. TP drift
        if intent.take_profit is not None:
            if actual.tp_actual is None:
                drifts.append({
                    "type": DriftType.TP_MISSING,
                    "severity": 0.7,
                    "details": {"expected_tp": intent.take_profit},
                })
            elif abs(actual.tp_actual - intent.take_profit) / intent.take_profit > 0.001:
                drifts.append({
                    "type": DriftType.TP_DRIFT,
                    "severity": 0.4,
                    "details": {
                        "expected": intent.take_profit,
                        "actual": actual.tp_actual,
                    },
                })

        # 5. Fill delay
        if actual.fill_time_ms > self.FILL_DELAY_THRESHOLD_MS:
            drifts.append({
                "type": DriftType.PRICE_DRIFT,
                "severity": min(actual.fill_time_ms / 30000, 1.0),
                "details": {"delay_ms": actual.fill_time_ms},
            })

        # 6. Rejection
        if actual.status == "REJECTED":
            drifts.append({
                "type": DriftType.STATE_CORRUPTION,
                "severity": 1.0,
                "details": {"reject_reason": actual.reject_reason},
            })

        # Compute overall drift score
        if drifts:
            max_severity = max(d["severity"] for d in drifts)
            truth.drift_type = drifts[0]["type"]
            truth.drift_score = max_severity
            truth.drift_details = {"drifts": drifts}
            truth.truth_state = TruthState.DRIFT_DETECTED
        else:
            truth.drift_type = DriftType.NONE
            truth.drift_score = 0.0
            if actual.status == "FILLED":
                truth.truth_state = TruthState.FILLED
            elif actual.status == "PARTIAL":
                truth.truth_state = TruthState.PARTIAL_FILL
            else:
                truth.truth_state = TruthState.VERIFIED

        truth.verified_at = datetime.now(timezone.utc).isoformat()
        return truth


# ── Phantom Detector ───────────────────────────────────────

class PhantomDetector:
    """Обнаруживает phantom и ghost позиции."""

    def check_phantom(
        self,
        internal_positions: dict,
        broker_positions: dict,
    ) -> list[dict]:
        """Найти расхождения между internal и broker state."""
        anomalies = []

        # Phantom: internal есть, broker нет
        for pos_id, pos in internal_positions.items():
            symbol = pos.get("symbol", "")
            if symbol not in broker_positions:
                anomalies.append({
                    "type": DriftType.PHANTOM_POSITION,
                    "position_id": pos_id,
                    "symbol": symbol,
                    "severity": 0.9,
                    "details": {
                        "internal": pos,
                        "broker": None,
                    },
                })

        # Ghost: broker есть, internal нет
        internal_symbols = {p.get("symbol", "") for p in internal_positions.values()}
        for symbol, broker_pos in broker_positions.items():
            if symbol not in internal_symbols:
                anomalies.append({
                    "type": DriftType.GHOST_POSITION,
                    "symbol": symbol,
                    "severity": 0.8,
                    "details": {
                        "internal": None,
                        "broker": broker_pos,
                    },
                })

        return anomalies


# ── SL/TP Guardian ─────────────────────────────────────────

class SLTPGuardian:
    """Проверяет корректность SL/TP на позициях."""

    MAX_SL_DISTANCE_PCT = 5.0       # max SL distance from entry
    MIN_SL_DISTANCE_PCT = 0.1       # min SL distance
    MAX_TP_DISTANCE_PCT = 20.0      # max TP distance

    def verify_sl_tp(self, position: dict) -> list[dict]:
        """Проверить SL/TP позиции. Возвращает список проблем."""
        problems = []
        entry = position.get("entry_price", 0)
        sl = position.get("stop_loss")
        tp = position.get("take_profit")
        side = position.get("side", "BUY")

        if entry <= 0:
            return problems

        # SL checks
        if sl is not None and sl > 0:
            sl_dist_pct = abs(entry - sl) / entry * 100
            if sl_dist_pct < self.MIN_SL_DISTANCE_PCT:
                problems.append({
                    "type": "SL_TOO_TIGHT",
                    "severity": 0.7,
                    "sl": sl,
                    "entry": entry,
                    "distance_pct": round(sl_dist_pct, 2),
                })
            elif sl_dist_pct > self.MAX_SL_DISTANCE_PCT:
                problems.append({
                    "type": "SL_TOO_WIDE",
                    "severity": 0.5,
                    "sl": sl,
                    "entry": entry,
                    "distance_pct": round(sl_dist_pct, 2),
                })
            # SL on wrong side
            if side == "BUY" and sl > entry:
                problems.append({
                    "type": "SL_WRONG_SIDE",
                    "severity": 1.0,
                    "sl": sl,
                    "entry": entry,
                })
            elif side == "SELL" and sl < entry:
                problems.append({
                    "type": "SL_WRONG_SIDE",
                    "severity": 1.0,
                    "sl": sl,
                    "entry": entry,
                })
        else:
            problems.append({
                "type": "SL_MISSING",
                "severity": 0.9,
                "entry": entry,
            })

        # TP checks
        if tp is not None and tp > 0:
            tp_dist_pct = abs(tp - entry) / entry * 100
            if tp_dist_pct > self.MAX_TP_DISTANCE_PCT:
                problems.append({
                    "type": "TP_TOO_FAR",
                    "severity": 0.3,
                    "tp": tp,
                    "entry": entry,
                    "distance_pct": round(tp_dist_pct, 2),
                })

        return problems


# ── Reconciler ─────────────────────────────────────────────

class Reconciler:
    """Сравнивает internal state с broker state, генерирует repair plan."""

    def reconcile(
        self,
        internal_positions: dict,
        broker_positions: dict,
        execution_ledger: list[dict],
    ) -> dict:
        """Полная сверка. Возвращает reconciliation report."""
        phantom_detector = PhantomDetector()
        anomalies = phantom_detector.check_phantom(internal_positions, broker_positions)

        # Check SL/TP for each internal position
        sltp_problems = []
        for pos_id, pos in internal_positions.items():
            problems = SLTPGuardian().verify_sl_tp(pos)
            if problems:
                sltp_problems.append({
                    "position_id": pos_id,
                    "symbol": pos.get("symbol", ""),
                    "problems": problems,
                })

        # Build repair plan
        repair_plan = []
        for anomaly in anomalies:
            if anomaly["type"] == DriftType.PHANTOM_POSITION:
                repair_plan.append({
                    "action": "CLOSE_PHANTOM",
                    "position_id": anomaly["position_id"],
                    "reason": "phantom position (not on broker)",
                })
            elif anomaly["type"] == DriftType.GHOST_POSITION:
                repair_plan.append({
                    "action": "SYNC_GHOST",
                    "symbol": anomaly["symbol"],
                    "reason": "ghost position (on broker, not internal)",
                })

        for sltp in sltp_problems:
            for problem in sltp["problems"]:
                if problem["type"] == "SL_MISSING":
                    repair_plan.append({
                        "action": "SET_SL",
                        "position_id": sltp["position_id"],
                        "reason": "SL not set on position",
                    })
                elif problem["type"] == "SL_WRONG_SIDE":
                    repair_plan.append({
                        "action": "FIX_SL",
                        "position_id": sltp["position_id"],
                        "reason": "SL on wrong side of entry",
                    })

        # Summary
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "internal_count": len(internal_positions),
            "broker_count": len(broker_positions),
            "anomalies": anomalies,
            "sltp_problems": sltp_problems,
            "repair_plan": repair_plan,
            "health": "OK" if not anomalies and not sltp_problems else "DEGRADED",
            "drift_score": max(
                (a["severity"] for a in anomalies),
                default=0.0,
            ),
        }


# ── Execution Ledger ───────────────────────────────────────

class ExecutionLedger:
    """Журнал всех execution truth records."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Path(__file__).parent.parent.parent / "tradingos_execution.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_truth (
                record_id   TEXT PRIMARY KEY,
                intent_id   TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                truth_state TEXT NOT NULL,
                drift_type  TEXT NOT NULL,
                drift_score REAL NOT NULL,
                fill_price  REAL,
                sl_actual   REAL,
                tp_actual   REAL,
                created_at  TEXT NOT NULL,
                verified_at TEXT,
                closed_at   TEXT,
                intent_json TEXT NOT NULL,
                actual_json TEXT NOT NULL,
                drift_json  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_et_symbol ON execution_truth(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_et_state ON execution_truth(truth_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_et_created ON execution_truth(created_at)")
        conn.commit()
        conn.close()

    def write(self, truth: ExecutionTruth):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("""
            INSERT OR REPLACE INTO execution_truth
            (record_id, intent_id, symbol, side, truth_state, drift_type,
             drift_score, fill_price, sl_actual, tp_actual,
             created_at, verified_at, closed_at, intent_json, actual_json, drift_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            truth.record_id, truth.intent.intent_id, truth.intent.symbol,
            truth.intent.side, truth.truth_state, truth.drift_type,
            truth.drift_score, truth.actual.fill_price,
            truth.actual.sl_actual, truth.actual.tp_actual,
            truth.created_at, truth.verified_at, truth.closed_at,
            json.dumps(truth.intent.to_dict()),
            json.dumps(truth.actual.to_dict()),
            json.dumps(truth.drift_details) if truth.drift_details else None,
        ))
        conn.commit()
        conn.close()

    def query(self, symbol=None, state=None, limit=100):
        conn = sqlite3.connect(str(self._path), timeout=10)
        sql = "SELECT * FROM execution_truth WHERE 1=1"
        params = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if state:
            sql += " AND truth_state = ?"
            params.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        cols = ["record_id", "intent_id", "symbol", "side", "truth_state",
                "drift_type", "drift_score", "fill_price", "sl_actual", "tp_actual",
                "created_at", "verified_at", "closed_at", "intent_json", "actual_json", "drift_json"]
        return [dict(zip(cols, row)) for row in rows]

    def stats(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        total = conn.execute("SELECT COUNT(*) FROM execution_truth").fetchone()[0]
        by_state = {}
        for row in conn.execute("SELECT truth_state, COUNT(*) FROM execution_truth GROUP BY truth_state"):
            by_state[row[0]] = row[1]
        by_drift = {}
        for row in conn.execute("SELECT drift_type, COUNT(*) FROM execution_truth WHERE drift_type != 'NONE' GROUP BY drift_type"):
            by_drift[row[0]] = row[1]
        avg_drift = conn.execute("SELECT AVG(drift_score) FROM execution_truth").fetchone()[0] or 0
        conn.close()
        return {
            "total": total,
            "by_state": by_state,
            "by_drift": by_drift,
            "avg_drift_score": round(avg_drift, 3),
        }


# ── Execution Truth Engine (ядро T6) ──────────────────────

class ExecutionTruthEngine:
    """Основной engine T6: intent → verify → reconcile → repair."""

    def __init__(self, ledger: Optional[ExecutionLedger] = None):
        self._verifier = ExecutionVerifier()
        self._reconciler = Reconciler()
        self._ledger = ledger or ExecutionLedger()

    def process_intent(self, intent: ExecutionIntent) -> ExecutionTruth:
        """Начать обработка intent (создать truth record)."""
        truth = ExecutionTruth(intent=intent)
        truth.truth_state = TruthState.INTENT
        self._ledger.write(truth)
        return truth

    def process_fill(
        self,
        truth: ExecutionTruth,
        actual: ExecutionActual,
    ) -> ExecutionTruth:
        """Обработать fill от биржи, верифицировать."""
        truth.actual = actual
        truth.truth_state = TruthState.SUBMITTED
        self._verifier.verify(truth)
        self._ledger.write(truth)
        return truth

    def reconcile(
        self,
        internal_positions: dict,
        broker_positions: dict,
    ) -> dict:
        """Полная сверка internal vs broker."""
        report = self._reconciler.reconcile(
            internal_positions, broker_positions, [],
        )
        return report

    def get_truth(self, record_id: str) -> Optional[ExecutionTruth]:
        rows = self._ledger.query(limit=10000)
        for row in rows:
            if row["record_id"] == record_id:
                truth = ExecutionTruth()
                truth.record_id = row["record_id"]
                truth.truth_state = row["truth_state"]
                truth.drift_type = row["drift_type"]
                truth.drift_score = row["drift_score"]
                truth.created_at = row["created_at"]
                truth.verified_at = row["verified_at"]
                truth.intent = ExecutionIntent(**json.loads(row["intent_json"]))
                truth.actual = ExecutionActual(**json.loads(row["actual_json"]))
                if row["drift_json"]:
                    truth.drift_details = json.loads(row["drift_json"])
                return truth
        return None

    def get_stats(self) -> dict:
        return self._ledger.stats()
