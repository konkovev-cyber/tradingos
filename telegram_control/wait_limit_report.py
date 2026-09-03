#!/usr/bin/env python3
"""
wait_limit_report.py — v2: WAIT economics, counterfactual, L1 vs L2.

ГЛАВНЫЙ ПОКАЗАТЕЛЬ:
    WAIT advantage = (суммарный limit PnL на всех исходах)
                   − (суммарный market PnL на тех ЖЕ сигналах)
    В Р от риска на сигнал. Это не «доказанный edge» — это измерительная
    метрика; интерпретируем только с n≥8 (первый checkpoint), содержательно
    ~20, архитектурно ~50+.

ВАЖНО про валидность counterfactual (owner review 2026-08-29):
  - market и limit считаются на ОДНИХ сигналах (одни PLACED), с одинаковым
    SL/TP и одинаковым риск-бюджетом (qty от риск-бюджета в постановке);
  - никогда не мешаем L1 и L2 в одну статистику — только раздельно;
  - MISSED: market PnL = по цене сигнала (вошли бы рынком), limit PnL =
    гипотетический филл в зоне; zone_reached_after — контроль cherry-pick;
  - INVALIDATED (B1): market гипотетика отдельно — B1 ценен НЕ прибылью,
    а предотвращением плохих филлов (вход в проход, а не в откат).

Входы:
  - memory/wait_limit_outcomes.jsonl — все исходы (FILLED/PARTIAL_FILL/
    EXPIRED/CANCELLED/STRUCTURE_BREAK) с filled_price, signal_price,
    ladder_level, гипотетикой market/limit PnL, MFE/MAE (после закрытия).
  - memory/manual_signals.jsonl — SIGNAL_SENT WAIT_LIMIT (сколько предложено).

Fee (Bybit USDT-margined linear, per side): maker 0.02% (лимитка),
taker 0.055% (маркет). NET = gross − 2×fee.
"""
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
OUTCOMES = ROOT / "memory" / "wait_limit_outcomes.jsonl"
SIGNALS = ROOT / "memory" / "manual_signals.jsonl"

MAKER_FEE = 0.0002
TAKER_FEE = 0.00055
MIN_SAMPLE = 8


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def usd(x, nd=2) -> str:
    try:
        return f"{x:,.{nd}f}$"
    except (TypeError, ValueError):
        return "—"


def risk_of(o: dict) -> float:
    """Риск-бюджет сделки в $: qty × |signal_price − SL| (нормировка R)."""
    sig = float(o.get("signal_price") or 0) or float(o.get("limit_entry") or 0)
    sl = float(o.get("sl") or 0)
    q = float(o.get("qty", 0) or 0)
    return abs(sig - sl) * q if (sig and sl and q) else 0.0


def in_r(pnl_usd: float, risk: float) -> float:
    return pnl_usd / risk if risk > 0 else 0.0


def net_after_fee(gross_usd: float, notional: float, fee_rate: float) -> float:
    return gross_usd - notional * 2 * fee_rate


def main() -> None:
    outcomes = load_jsonl(OUTCOMES)
    signals = load_jsonl(SIGNALS)

    print("=" * 66)
    print("WAIT ECONOMICS v2 — counterfactual (WAIT vs MARKET), L1/L2 раздельно")
    print(f"сгенерирован: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 66)

    if not outcomes:
        print("\n(журнал исходов пуст — данные появятся после первых лимиток)")
        return

    n_wait = sum(1 for s in signals
                 if s.get("event") == "SIGNAL_SENT"
                 and s.get("trade_decision") == "WAIT_LIMIT")

    # ── 1. Общие счётчики ────────────────────────────────────
    n_all = len(outcomes)
    counts = {}
    for o in outcomes:
        counts[o.get("reason", "?")] = counts.get(o.get("reason", "?"), 0) + 1
    n_filled = counts.get("FILLED", 0) + counts.get("PARTIAL_FILL", 0)
    n_expired = counts.get("EXPIRED", 0)
    n_canc = counts.get("CANCELLED", 0)
    n_sb = counts.get("STRUCTURE_BREAK", 0)
    fill_rate = n_filled / max(n_all, 1) * 100

    print("\n1. СЧЁТЧИКИ")
    print(f"   WAIT-сигналов: {n_wait} | поставлено лимиток: {n_all}")
    print(f"   FILLED={counts.get('FILLED',0)} PARTIAL={counts.get('PARTIAL_FILL',0)} "
          f"EXPIRED={n_expired} CANCELLED={n_canc} INVALIDATED(B1)={n_sb}")
    print(f"   Fill rate (≥1 части): {fill_rate:.1f}%")
    if n_all < MIN_SAMPLE:
        print(f"   ⚠️ n={n_all} < {MIN_SAMPLE} — первый checkpoint. Ниже — только структура, не выводы.")

    # ── 2. Per-level (L1 / L2) ───────────────────────────────
    print("\n2. УРОВНИ (L1 / L2 раздельно)")
    for lvl in (1, 2):
        grp = [o for o in outcomes if int(o.get("ladder_level") or 1) == lvl]
        if not grp:
            print(f"   L{lvl}: (нет исходов)")
            continue
        g_f = sum(1 for o in grp if o.get("reason") in ("FILLED", "PARTIAL_FILL"))
        g_rate = g_f / len(grp) * 100
        # entry improvement в R
        ei = []
        for o in grp:
            if o.get("reason") not in ("FILLED", "PARTIAL_FILL"):
                continue
            fill = float(o.get("filled_price") or 0)
            sig = float(o.get("signal_price") or 0) or float(o.get("limit_entry") or 0) or fill
            sl = float(o.get("sl") or 0)
            risk = abs(sig - sl) if sl else 1e-9
            if fill > 0 and sig > 0:
                side = str(o.get("side", "LONG")).upper()
                diff = (sig - fill) if side in ("LONG", "BUY") else (fill - sig)
                ei.append(diff / risk)
        # NET filled + MFE/MAE
        net_mk = net_lm = 0.0
        mfe_v, mae_v = [], []
        for o in grp:
            risk = risk_of(o)
            h = o.get("hypothetical") or {}
            if h.get("market_pnl_usd") is not None:
                nt = float(o.get("limit_entry") or 0) * float(o.get("qty") or 0)
                net_mk += net_after_fee(h["market_pnl_usd"], nt, TAKER_FEE)
            if h.get("limit_pnl_usd") is not None:
                nt = float(o.get("limit_entry") or 0) * float(o.get("qty") or 0)
                net_lm += net_after_fee(h["limit_pnl_usd"], nt, MAKER_FEE)
            if o.get("mfe_pct") is not None:
                mfe_v.append(float(o["mfe_pct"]))
            if o.get("mae_pct") is not None:
                mae_v.append(float(o["mae_pct"]))
        s_mk = s_lm = 0
        for o in grp:
            s_mk += (o.get("hypothetical") or {}).get("market_pnl_usd", 0) or 0
            s_lm += (o.get("hypothetical") or {}).get("limit_pnl_usd", 0) or 0
        print(f"   L{lvl}: n={len(grp)} | fill {g_rate:.0f}% | "
              f"imp_med={statistics.median(ei):+.2f}R" if ei else
              f"   L{lvl}: n={len(grp)} | fill {g_rate:.0f}% | imp: (нет филлов)")
        if ei:
            better = sum(1 for i in ei if i > 0)
            print(f"        улучшили цену: {100*better/max(len(ei),1):.0f}% филлов")
        print(f"        market-гипотеза (все исх.): {usd(s_mk)} | limit-гипотеза: {usd(s_lm)}")
        if mfe_v:
            print(f"        MFE med {statistics.median(mfe_v):+.2f}% | MAE med {statistics.median(mae_v):+.2f}%"
                  f" ({len(mfe_v)} закрытых)")
        else:
            print("        MFE/MAE: (нет закрытых позиций)")

    # ── 3. Важный counterfactual ─────────────────────────────
    print("\n3. COUNTERFACTUAL (одинаковый риск-бюджет, same signals)")
    rows = [o for o in outcomes if (o.get("hypothetical") or {}).get("market_pnl_usd") is not None]
    if not rows:
        print("   (нет данных по гипотетике)")
    else:
        tot_mk = tot_lm = 0.0
        tot_risk = 0.0
        for o in rows:
            rk = risk_of(o)
            h = o.get("hypothetical") or {}
            tot_mk += h["market_pnl_usd"]
            tot_lm += h["limit_pnl_usd"]
            tot_risk += rk
        adv = tot_lm - tot_mk
        adv_r = adv / tot_risk if tot_risk else 0.0
        mk_r = tot_mk / tot_risk if tot_risk else 0.0
        lm_r = tot_lm / tot_risk if tot_risk else 0.0
        print(f"   signal_count: {len(rows)} | суммарный риск: {usd(tot_risk)}")
        print(f"   MARKET NET (вход по сигналу): {usd(tot_mk)} ≈ {mk_r:+.2f}R/signal-equivalent")
        print(f"   LIMIT NET (филл в зоне)     : {usd(tot_lm)} ≈ {lm_r:+.2f}R/signal-equivalent")
        print(f"   ────────────────────────────────")
        print(f"   WAIT ADVANTAGE: {usd(adv)} ≈ {adv_r:+.2f}R на ед. риска")
        print("   ВАЛИДНОСТЬ: одинаковый риск-бюджет (qty от risk), одинаковые "
              "SL/TP, одинаковые сигналы; fee раздельно maker/taker.")

    # ── 4. Секции FILLED / MISSED / INVALIDATED ──────────────
    print("\n4. СЕКЦИИ (market vs limit на той же группе)")
    filled_o = [o for o in outcomes if o.get("reason") in ("FILLED", "PARTIAL_FILL")]
    missed_o = [o for o in outcomes if o.get("reason") in ("EXPIRED", "CANCELLED")]
    inv_o = [o for o in outcomes if o.get("reason") == "STRUCTURE_BREAK"]
    for title, grp in (("FILLED", filled_o), ("MISSED", missed_o), ("INVALIDATED(B1)", inv_o)):
        if not grp:
            print(f"   {title}: (нет)")
            continue
        m, l = 0.0, 0.0
        risk_t = 0.0
        zr = 0
        for o in grp:
            h = o.get("hypothetical") or {}
            if h.get("market_pnl_usd") is not None:
                m += h["market_pnl_usd"]
            if h.get("limit_pnl_usd") is not None:
                l += h["limit_pnl_usd"]
            risk_t += risk_of(o)
            if h.get("zone_reached_after"):
                zr += 1
        m_r = m / risk_t if risk_t else 0.0
        l_r = l / risk_t if risk_t else 0.0
        extra = ""
        if title == "INVALIDATED(B1)":
            extra = "  ← предотвращены плохие филлы (вход в проход)"
        if title == "MISSED" and zr:
            extra = f"  | зона достигнута после: {zr}/{len(grp)} ({100*zr/max(len(grp),1):.0f}%)"
        print(f"   {title}: n={len(grp)} | market {usd(m)} ({m_r:+.2f}R) | "
              f"limit {usd(l)} ({l_r:+.2f}R){extra}")

    # ── 5. Вердикт ───────────────────────────────────────────
    print("\n5. ИНТЕРПРЕТАЦИЯ")
    if n_all < MIN_SAMPLE:
        print(f"   n={n_all} < {MIN_SAMPLE} — первый checkpoint на n≥8. Сейчас это "
              "только proof-of-measurement, НЕ доказательство edge.")
        print("   Дальше: n≈20 первый содержательный вывод, n≈50+ архитектурное решение.")
        return
    if rows:
        adv = sum((o.get("hypothetical") or {}).get("limit_pnl_usd", 0) or 0
                  for o in rows) - sum((o.get("hypothetical") or {}).get("market_pnl_usd", 0) or 0
                  for o in rows)
        if adv > 0:
            print("   WAIT advantage положительна — цена входа перевешивает пропуски "
                  f"({usd(adv)}).")
        else:
            print("   WAIT advantage отрицательна — лимитка режет edge пропущенными "
                  f"входами ({usd(adv)}). Смотреть дистанцию/зрелость зон.")


if __name__ == "__main__":
    main()