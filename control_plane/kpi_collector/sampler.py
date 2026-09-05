"""
control_plane/kpi_collector/sampler.py
Sample collection from Reality Engine + Paper + Action Shadow.

READ-ONLY inputs. Writes samples into state.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from .models import ActionSample

PAPER_PATH = Path("/root/tradingos/control_plane/paper/paper_simulation.json")
REALITY_PATH = Path("/root/tradingos/control_plane/reality/reality_result.json")
ACTION_LOG = Path("/root/tradingos/logs/position_action_shadow.jsonl")
PG_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return {}


def _load_action_shadow_samples() -> List[ActionSample]:
    """
    Build samples from Action Shadow log.
    Each APPROVED unique action = 1 sample.
    Paper + Reality data joined by symbol.
    """
    if not ACTION_LOG.exists():
        return []

    paper = _load_json(PAPER_PATH)
    reality = _load_json(REALITY_PATH)
    paper_scenarios = paper.get("scenarios", [])
    reality_vs_hold = reality.get("vs_hold", 0)
    reality_gross = reality.get("gross_result", 0)
    reality_net = reality.get("net_result", 0)

    seen_keys: Set = set()
    samples: List[ActionSample] = []

    try:
        with ACTION_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("action", {}).get("verdict") != "APPROVE":
                        continue
                    sym = rec.get("symbol", "")
                    side = rec.get("side", "")
                    atype = rec.get("action", {}).get("type", "")
                    key = (sym, side, atype)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    gross = reality_gross if sym == reality.get("symbol", "") else 0
                    net = reality_net if sym == reality.get("symbol", "") else 0
                    hold = net - reality_vs_hold if sym == reality.get("symbol", "") else 0
                    if gross == 0 and paper_scenarios:
                        for s in paper_scenarios:
                            if s.get("action", "").startswith("MOVE"):
                                gross = s.get("action_pnl", 0)
                                break
                        if gross == 0 and paper_scenarios:
                            gross = paper_scenarios[0].get("action_pnl", 0)
                    samples.append(ActionSample(
                        symbol=sym,
                        side=side,
                        action_type=atype,
                        gross_pnl=gross,
                        net_pnl=net,
                        hold_pnl=hold,
                        regime="UNKNOWN",
                    ))
                except Exception:
                    continue
    except Exception:
        pass

    return samples


def collect_samples() -> List[ActionSample]:
    """Public entry: collect new samples from available sources."""
    return _load_action_shadow_samples()
