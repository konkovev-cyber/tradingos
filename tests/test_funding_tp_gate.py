"""Unit tests for funding TP economic gate (VELVET-инцидент 2026-08-29, R:R fix 2026-08-30).

Проблема: TP = 3 выплаты funding (tp_pct = |funding_bps|*3/10000) может быть
меньше комиссии round-trip (0.22% на демо). VELVET: funding 3.1 bps → TP 0.093%
< 0.22% fees → gross $0.84 − $1.98 = net −$1.14. Сделка убыточна математически.

2026-08-30: добавлен жёсткий порог funding_min_funding_bps=20. При 11-16 bps
нужен WR 69-81% для безубытка (комиссия 0.22% + R:R 1.33) — нереально;
реальный WR авто-funding ~62-65%. С 20 bps достаточно ~64%.
"""
import sys

ROOT = "/root/tradingos"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from telegram_control.manual_signal import _funding_tp_ok


def test_velvet_scenario_blocked():
    """VELVET 3.1 bps — далеко ниже порога 20 bps → block."""
    ok, why = _funding_tp_ok(3.1)
    assert ok is False
    assert "20" in why


def test_clousdt_scenario_blocked():
    """CLOUSDT 8.4 bps — тоже ниже 20 bps → block (на грани, в минус)."""
    ok, _ = _funding_tp_ok(8.4)
    assert ok is False


def test_below_20_bps_blocked():
    """11-19 bps блокируются жёстким порогом даже при проходе fee-гейта."""
    for bps in (11.0, 13.0, 16.0, 19.0):
        ok, why = _funding_tp_ok(bps)
        assert ok is False, f"{bps} bps должен быть blocked"
        assert "20" in why


def test_20_bps_allowed():
    """20 bps ровно на пороге → allow."""
    ok, why = _funding_tp_ok(20.0)
    assert ok is True
    assert why == ""


def test_high_funding_allowed():
    """funding 25 bps → allow."""
    ok, why = _funding_tp_ok(25.0)
    assert ok is True
    assert why == ""


def test_zero_funding_blocked():
    ok, _ = _funding_tp_ok(0.0)
    assert ok is False


def test_negative_funding_symmetric():
    """Отрицательный funding (LONG-направление) — симметричный гейт по модулю."""
    ok_f, _ = _funding_tp_ok(-25.0)
    ok_p, _ = _funding_tp_ok(25.0)
    assert ok_f is ok_p is True


def test_custom_cfg_loose():
    """Порог можно ослабить через конфиг (funding_min_funding_bps=0, mult=1.0)."""
    loose = {"funding_fee_round_trip_pct": 0.22, "funding_min_edge_mult": 1.0,
             "funding_min_funding_bps": 0.0}
    ok, _ = _funding_tp_ok(8.0, loose)   # 0.24% ≥ 0.22% → allow
    assert ok is True
    ok, _ = _funding_tp_ok(7.0, loose)   # 0.21% < 0.22% → block
    assert ok is False


def test_custom_cfg_strict():
    """Усиленный порог через конфиг (mult=2.0, без hard bps)."""
    strict = {"funding_fee_round_trip_pct": 0.22, "funding_min_edge_mult": 2.0,
              "funding_min_funding_bps": 0.0}
    ok, _ = _funding_tp_ok(15.0, strict)  # 0.45% ≥ 0.44% → allow
    assert ok is True
    ok, _ = _funding_tp_ok(14.0, strict)  # 0.42% < 0.44% → block
    assert ok is False


def test_boundary_fee_gate():
    """Ровно на fee-пороге (издержки×1.5) — allow (не строгое <)."""
    cfg = {"funding_fee_round_trip_pct": 0.22, "funding_min_edge_mult": 1.5,
           "funding_min_funding_bps": 0.0}
    ok, _ = _funding_tp_ok(11.0, cfg)
    assert ok is True


def test_max_cap_50():
    """Верхний кап 50 bps (2026-08-31): экстремум = сквиз-ловушка → block.
    ZKP −108bps давал −$21 на LONG сквизе."""
    cfg = {"funding_fee_round_trip_pct": 0.22, "funding_min_edge_mult": 1.5,
           "funding_min_funding_bps": 0.0, "funding_max_funding_bps": 50.0}
    for bps in (55.0, 62.0, 108.0):
        ok, why = _funding_tp_ok(bps, cfg)
        assert ok is False, f"{bps} bps должен быть blocked (cap 50)"
        assert "кап" in why
    ok, _ = _funding_tp_ok(50.0, cfg)
    assert ok is True