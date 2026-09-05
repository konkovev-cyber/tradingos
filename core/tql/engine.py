"""TQL — Trading Query Language parser + executor."""
import re
from dataclasses import dataclass, field
from typing import Optional, Any


# ── AST Nodes ──────────────────────────────────────────────

@dataclass
class WhereClause:
    field: str
    op: str           # ==, !=, >, <, >=, <=, IN, LIKE, IS NULL
    value: Any = None
    negate: bool = False


@dataclass
class SelectQuery:
    source: str
    fields: list[str] = field(default_factory=lambda: ["*"])
    wheres: list[WhereClause] = field(default_factory=list)
    order_by: Optional[str] = None
    order_dir: str = "ASC"
    limit: int = 1000
    group_by: Optional[str] = None


@dataclass
class TraceQuery:
    type: str
    id: str


@dataclass
class TimelineQuery:
    type: str
    id: str
    includes: list[str] = field(default_factory=list)


@dataclass
class CountQuery:
    source: str
    wheres: list[WhereClause] = field(default_factory=list)


@dataclass
class ParseResult:
    kind: str           # "select", "trace", "timeline", "count"
    query: Any          # SelectQuery | TraceQuery | TimelineQuery | CountQuery
    error: Optional[str] = None


# ── Parser ────────────────────────────────────────────────

class TQLParser:
    OPERS = {"==", "!=", ">=", "<=", ">", "<", "IN", "LIKE", "IS NULL"}

    def parse(self, text: str) -> ParseResult:
        text = text.strip()
        upper = text.upper()

        if upper.startswith("TRACE "):
            return self._parse_trace(text[6:].strip())
        if upper.startswith("TIMELINE "):
            return self._parse_timeline(text[9:].strip())
        if upper.startswith("SELECT COUNT"):
            return self._parse_count(text)
        if upper.startswith("SELECT "):
            return self._parse_select(text[7:].strip())

        return ParseResult("error", None, f"Unknown command: {text.split()[0]}")

    def _parse_trace(self, arg: str) -> ParseResult:
        parts = arg.split("/", 1)
        if len(parts) != 2:
            return ParseResult("trace", None, "Usage: TRACE <type>/<id>")
        return ParseResult("trace", TraceQuery(type=parts[0], id=parts[1]))

    def _parse_timeline(self, arg: str) -> ParseResult:
        includes = []
        if " INCLUDE " in arg.upper():
            idx = arg.upper().index(" INCLUDE ")
            includes = [x.strip() for x in arg[idx + 9:].split(",")]
            arg = arg[:idx]
        parts = arg.split("/", 1)
        if len(parts) != 2:
            return ParseResult("timeline", None, "Usage: TIMELINE <type>/<id>")
        return ParseResult(
            "timeline",
            TimelineQuery(type=parts[0], id=parts[1], includes=includes),
        )

    def _parse_count(self, text: str) -> ParseResult:
        upper = text.upper()
        src = "events"
        if "FROM " in upper:
            i = upper.index("FROM ")
            end = upper.index(" WHERE ", i) if " WHERE " in upper[i:] else len(upper)
            src = text[i + 5:end].strip().lower()
        wheres = self._extract_wheres(text)
        return ParseResult("count", CountQuery(source=src, wheres=wheres))

    def _parse_select(self, text: str) -> ParseResult:
        upper = text.upper()

        # FROM
        src = "events"
        if "FROM " in upper:
            i = upper.index("FROM ")
            end = upper.index(" WHERE ", i) if " WHERE " in upper[i:] else len(upper)
            src = text[i + 5:end].strip().lower()

        wheres = self._extract_wheres(text)

        # ORDER BY
        order_by, order_dir = None, "ASC"
        if "ORDER BY " in upper:
            i = upper.index("ORDER BY ")
            rest = text[i + 9:].strip()
            tokens = rest.split()
            order_by = tokens[0]
            if len(tokens) > 1 and tokens[1].upper() in ("ASC", "DESC"):
                order_dir = tokens[1].upper()

        # LIMIT
        limit = 1000
        if "LIMIT " in upper:
            i = upper.index("LIMIT ")
            rest = text[i + 6:].strip().split()[0]
            try:
                limit = int(rest)
            except ValueError:
                pass

        return ParseResult(
            "select",
            SelectQuery(
                source=src, wheres=wheres, order_by=order_by,
                order_dir=order_dir, limit=limit,
            ),
        )

    def _extract_wheres(self, text: str) -> list[WhereClause]:
        upper = text.upper()
        if " WHERE " not in upper:
            return []
        i = upper.index(" WHERE ") + 7
        rest = text[i:]

        # Strip ORDER/LIMIT
        for kw in (" ORDER ", " LIMIT ", " GROUP "):
            if kw in rest.upper():
                rest = rest[:rest.upper().index(kw)]

        wheres = []
        # Split by AND
        for part in re.split(r'\s+AND\s+', rest, flags=re.IGNORECASE):
            part = part.strip()
            if not part:
                continue

            # IS NULL / IS NOT NULL
            if " IS NULL" in part.upper():
                field = part[:part.upper().index(" IS NULL")].strip()
                wheres.append(WhereClause(field=field, op="IS NULL"))
                continue
            if " IS NOT NULL" in part.upper():
                field = part[:part.upper().index(" IS NOT NULL")].strip()
                wheres.append(WhereClause(field=field, op="IS NOT NULL", negate=True))
                continue

            # IN (...)
            m = re.match(r'(\w+)\s+IN\s*\(([^)]+)\)', part, re.IGNORECASE)
            if m:
                vals = [v.strip().strip("\"'") for v in m.group(2).split(",")]
                wheres.append(WhereClause(field=m.group(1), op="IN", value=vals))
                continue

            # LIKE
            m = re.match(r'(\w+)\s+LIKE\s+["\']([^"\']+)["\']', part, re.IGNORECASE)
            if m:
                wheres.append(WhereClause(field=m.group(1), op="LIKE", value=m.group(2)))
                continue

            # Comparison operators
            m = re.match(r'(\w+)\s*(>=|<=|!=|==|>|<)\s*["\']?([^"\'\s]+)["\']?', part)
            if m:
                op = m.group(2)
                val = m.group(3)
                try:
                    val = float(val)
                except ValueError:
                    pass
                wheres.append(WhereClause(field=m.group(1), op=op, value=val))
                continue

            # NOW - Xd format (simple version)
            m = re.match(r'(\w+)\s*(>=|<=)\s*NOW\s*-\s*(\d+)([dhms])', part, re.IGNORECASE)
            if m:
                wheres.append(WhereClause(
                    field=m.group(1), op=m.group(2) + "_NOW",
                    value={"n": int(m.group(3)), "unit": m.group(4)},
                ))

        return wheres


# ── Executor ───────────────────────────────────────────────

class TQLExecutor:
    def __init__(self, data_lake):
        self.lake = data_lake

    def execute(self, parsed: ParseResult) -> dict:
        if parsed.error:
            return {"status": "error", "error": parsed.error}

        if parsed.kind == "select":
            return self._exec_select(parsed.query)
        if parsed.kind == "trace":
            return self._exec_trace(parsed.query)
        if parsed.kind == "timeline":
            return self._exec_timeline(parsed.query)
        if parsed.kind == "count":
            return self._exec_count(parsed.query)

        return {"status": "error", "error": f"Unknown kind: {parsed.kind}"}

    def _exec_select(self, q: SelectQuery) -> dict:
        filters = {}
        if q.source == "trades":
            filters["event_type"] = "TradeRecorded"
        elif q.source == "signals":
            filters["event_type"] = "SignalGenerated"
        elif q.source == "decisions":
            filters["event_type"] = "DecisionCreated"
        elif q.source == "orders":
            filters["event_type"] = "OrderSubmitted"
        elif q.source in ("features", "regimes", "edges", "evidence", "anomalies", "events"):
            filters["event_type"] = {
                "features": "FeatureComputed",
                "regimes": "RegimeDetected",
                "edges": "EdgeDiscovered",
                "evidence": "EvidenceGenerated",
                "anomalies": "AnomalyDetected",
            }.get(q.source)

        for wc in q.wheres:
            if wc.field == "symbol":
                filters["symbol"] = wc.value
            elif wc.field == "event_type" and wc.op == "==":
                filters["event_type"] = wc.value
            elif wc.field == "event_type" and wc.op == "IN":
                # Only first for now
                filters["event_type"] = wc.value[0] if wc.value else None

        rows = self.lake.query(
            event_type=filters.get("event_type"),
            symbol=filters.get("symbol"),
            limit=q.limit,
        )

        # Additional filtering (in-memory for MVP)
        filtered = []
        for row in rows:
            if self._matches_wheres(row, q.wheres):
                filtered.append(row)

        return {
            "status": "ok",
            "total": len(filtered),
            "data": filtered[:q.limit],
        }

    def _matches_wheres(self, row: dict, wheres: list[WhereClause]) -> bool:
        for wc in wheres:
            if wc.field in ("event_type", "symbol", "schema_version",
                            "source_module", "severity", "timestamp"):
                val = row.get(wc.field, "")
            else:
                payload = row.get("payload", "{}")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                val = payload.get(wc.field)

            if wc.op == "IS NULL" and val is not None:
                return False
            if wc.op == "IN" and val not in wc.value:
                return False
            if wc.op == "LIKE" and val is not None:
                import fnmatch
                if not fnmatch.fnmatch(str(val), wc.value):
                    return False
            if wc.op == "==" and str(val) != str(wc.value):
                return False
            if wc.op == "!=" and str(val) == str(wc.value):
                return False
            if wc.op in (">", ">=", "<", "<="):
                try:
                    if wc.op == ">" and float(val) <= float(wc.value):
                        return False
                    if wc.op == ">=" and float(val) < float(wc.value):
                        return False
                    if wc.op == "<" and float(val) >= float(wc.value):
                        return False
                    if wc.op == "<=" and float(val) > float(wc.value):
                        return False
                except (ValueError, TypeError):
                    return False
        return True

    def _exec_trace(self, q: TraceQuery) -> dict:
        # Simple: search by event_id or trace_id
        rows = self.lake.query(limit=10000)
        found = [r for r in rows if r["event_id"] == q.id or r["trace_id"] == q.id]
        return {"status": "ok", "total": len(found), "data": found}

    def _exec_timeline(self, q: TimelineQuery) -> dict:
        rows = self.lake.get_trace(q.id)
        return {"status": "ok", "total": len(rows), "data": rows}

    def _exec_count(self, q: CountQuery) -> dict:
        filters = {}
        if q.source == "trades":
            filters["event_type"] = "TradeRecorded"
        elif q.source == "signals":
            filters["event_type"] = "SignalGenerated"
        elif q.source == "events":
            pass
        c = self.lake.count(event_type=filters.get("event_type"))
        return {"status": "ok", "count": c}
