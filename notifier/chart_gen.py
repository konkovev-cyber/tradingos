"""
TradingOS Chart v2 — «визуализируй реальные рыночные данные, а не рисуй картинку».

Принципы (ТЗ пользователя):
1. Свечи — ТОЛЬКО реальные OHLC с Bybit (kline API), окно ~40-50 свечей ДО входа
   + ~20 ПОСЛЕ. Синтетика — только аварийный фолбэк.
2. Portrait 900x1200, график ~70% изображения, инфо-карточка снизу ~25%.
3. Торговые объекты: ENTRY (синяя), SL (красная), TP (зелёная), текущая цена.
   Вся торговая идея ВСЕГДА в масштабе (ylim по min(SL,low)..max(TP,high) + 10%).
4. Текст НЕ поверх свечей: шапка сверху, карточка снизу.
5. OPEN: текущая цена + unreal PnL + MFE/MAE. CLOSED: выход + причина + realized R.
6. Никаких индикаторов по умолчанию (только prob/score/ADX в шапке, если переданы).

Светлая тема (#F8FAFC), зелёные/красные свечи.
"""
from __future__ import annotations
import io, logging, random, math, time
from typing import Optional
from datetime import timedelta
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

log = logging.getLogger("TradingOS.ChartGen")

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Inter", "SF Pro", "DejaVu Sans", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

# ─── LIGHT THEME ────────────────────────────────────────────
BG   = "#F8FAFC"
CARD = "#FFFFFF"
TXT  = "#0F172A"
SEC  = "#64748B"
GRN  = "#059669"
RED  = "#DC2626"
ENT  = "#3B82F6"
GRID = (0, 0, 0, 0.05)


def _fp(p: float) -> str:
    if p is None:
        return "—"
    return f"{float(p):.10f}".rstrip("0").rstrip(".")


_INTERVAL_MIN = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60, "240": 240}


def _interval_minutes(interval: str) -> int:
    return _INTERVAL_MIN.get(str(interval), 15)


# ─── ДАННЫЕ: реальные OHLC ──────────────────────────────────
async def _fetch_real_klines(symbol: str, interval: str = "15",
                             entry_time=None, pre_bars: int = 50,
                             post_bars: int = 200):
    """Реальные свечи Bybit вокруг входа. Returns (klines, entry_idx) or None."""
    import httpx
    tf_min = _interval_minutes(interval)
    if entry_time is None:
        return None
    try:
        if isinstance(entry_time, (int, float)):
            entry_ms = int(entry_time) * 1000 if entry_time < 1e12 else int(entry_time)
        else:
            entry_ms = int(pd.Timestamp(entry_time).timestamp() * 1000)
    except Exception:
        return None
    start_ms = entry_ms - pre_bars * tf_min * 60_000
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": str(interval),
        "limit": min(pre_bars + post_bars, 1000),
    }
    if start_ms:
        params["start"] = str(start_ms)
    try:
        resp = await httpx.AsyncClient(timeout=12).get(
            "https://api.bybit.com/v5/market/kline", params=params)
        data = resp.json()
        if data.get("retCode") != 0:
            return None
        rows = data["result"].get("list", [])
        if not rows:
            return None
        kls = []
        for row in reversed(rows):
            try:
                raw_ts = int(row[0])
            except (IndexError, ValueError, TypeError):
                continue
            ts_s = raw_ts / 1000 if raw_ts > 1e12 else raw_ts
            if ts_s * 1000 < start_ms:
                continue
            kls.append({
                "timestamp": int(ts_s),
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": float(row[5]) if row[5] else 0.0,
            })
        if len(kls) < 10:
            return None
        entry_ms_s = entry_ms // 1000
        entry_idx = 0
        for i, k in enumerate(kls):
            if k["timestamp"] >= entry_ms_s - tf_min * 60:
                entry_idx = i
                break
        return kls, entry_idx
    except Exception as e:
        log.warning(f"real klines fetch failed {symbol}: {e}")
        return None


# ─── ФОЛБЭК: синтетика (только если реальных данных нет) ────
def _generate_synthetic_klines(entry_price, exit_price, entry_time, exit_time,
                               sl, tp, is_long, mfe_r=0.0, mae_r=0.0):
    """Аварийный фолбэк: минимальные правдоподобные свечи по MFE/MAE."""
    if entry_time is None or exit_time is None:
        return None
    duration_min = max(30, (exit_time - entry_time).total_seconds() / 60)
    tf_min = _interval_minutes("15" if duration_min < 720 else "60")
    num = max(24, min(60, int(duration_min / tf_min)))
    r = abs(entry_price - sl) if (sl and sl > 0) else entry_price * 0.01
    mfe = min(mfe_r or 0.5, 5.0)
    mae = min(mae_r or 0.3, 5.0)
    kls = []
    ts = int(entry_time.timestamp())
    t0 = ts - (num // 2) * tf_min * 60
    mid = num // 2
    for i in range(num):
        if is_long:
            target = entry_price + (mfe * r) * max(0, i - mid) / max(mid, 1)
        else:
            target = entry_price - (mfe * r) * max(0, i - mid) / max(mid, 1)
        noise = entry_price * 0.002 * random.gauss(0, 1)
        close = target + noise if i > mid else entry_price + noise * 0.4
        open_p = kls[-1]["close"] if kls else entry_price
        hi = max(open_p, close) + abs(noise) * 0.6
        lo = min(open_p, close) - abs(noise) * 0.6
        kls.append({"timestamp": t0 + i * tf_min * 60, "open": open_p,
                    "high": hi, "low": lo, "close": close, "volume": 1000.0})
    return kls


# ─── РЕНДЕР ─────────────────────────────────────────────────
def _draw_candles(ax, o, h, l, c, bw):
    n = len(o)
    for i in range(n):
        up = c[i] >= o[i]
        clr = GRN if up else RED
        ax.plot([i, i], [l[i], h[i]], color=clr, linewidth=1.2,
                solid_capstyle="butt", zorder=2)
        body_lo = min(o[i], c[i])
        body_hi = max(o[i], c[i])
        bh = max(body_hi - body_lo, (max(h) - min(l)) * 0.0008)
        ax.add_patch(Rectangle((i - bw / 2, body_lo), bw, bh,
                               facecolor=clr, edgecolor=clr, linewidth=0.8, zorder=3))


def _level_line(ax, price, color, label, x_from, x_to, lw=2.0, ls=(0, (6, 3)), above=True):
    ax.axhline(y=price, color=color, linewidth=lw, linestyle=ls, alpha=0.85, zorder=5)
    ax.annotate(
        f" {label} {_fp(price)} ", xy=((x_from + x_to) / 2, price),
        xytext=(0, 8 if above else -14), textcoords="offset points",
        fontsize=9, fontweight="bold", color=color, ha="center",
        bbox=dict(boxstyle="round,pad=0.18", fc="#FFFFFF", ec=color, lw=0.8), zorder=12)


def _render(symbol, df, entry_idx, side, lev, epx, sl, tp, ext,
            etm, xtm, is_open, reason, mfe_r, mae_r, qty, cur_price,
            pnl, prob, score, adx):
    """Главный рендер v2: portrait 900x1200, реальные свечи, карточка снизу."""
    n = len(df)
    o, h, l, c = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    is_long = side.upper() in ("BUY", "LONG")
    sym = symbol.upper()
    for sf in ["-USDT", "/USDT", "USDT"]:
        if sym.endswith(sf):
            sym = sym[:-len(sf)]
            break
    ei = max(0, min(entry_idx, n - 1))

    # ─── ТЕКУЩАЯ ЦЕНА / ТОЧКА ВЫХОДА (до масштаба — нужна для tp_clamped) ──
    last_price = cur_price if (cur_price and cur_price > 0) else float(c[-1])

    # ─── МАСШТАБ: вся торговая идея + 10% ────────────────────
    # v2.1: для OPEN далёкий TP (>8% от текущей цены) НЕ растягивает ось —
    # рисуется маркером на границе, а не расплющивает свечи.
    tp_clamped = False
    if is_open and tp and tp > 0 and last_price > 0 and abs(tp - last_price) / last_price > 0.08:
        tp_clamped = True

    lows = float(np.min(l))
    highs = float(np.max(h))
    y_lo, y_hi = lows, highs
    for p_ in (epx, sl, tp if not tp_clamped else 0.0, ext):
        if p_ and p_ > 0:
            y_lo, y_hi = min(y_lo, p_), max(y_hi, p_)
    rng = y_hi - y_lo
    if rng <= 0:
        rng = epx * 0.02 or 1.0
    margin = rng * 0.10
    y_lo -= margin
    y_hi += margin

    fig = plt.figure(figsize=(16, 9), facecolor=BG, dpi=100)

    # ─── ШАПКА (текст не поверх свечей) ──────────────────────
    sc = GRN if is_long else RED
    fig.text(0.06, 0.955, f"{sym}/USDT", fontsize=24, fontweight="bold", color=TXT)
    fig.text(0.06, 0.918, f"{'▲' if is_long else '▼'} {'LONG' if is_long else 'SHORT'} ×{max(1, lev)}",
             fontsize=13, fontweight="bold", color=sc)
    meta = "  |  ".join(x for x in [
        f"prob {prob:.2f}" if prob else "",
        f"score {score:.0f}" if score else "",
        f"ADX {adx:.0f}" if adx else "",
    ] if x)
    if meta:
        fig.text(0.06, 0.888, meta, fontsize=10, color=SEC, weight="bold")

    # правая часть шапки — статус и PnL
    if is_open:
        if epx and epx > 0 and last_price > 0:
            mv = (last_price / epx - 1.0) * 100 if is_long else (epx / last_price - 1.0) * 100
            upct = mv * max(1, lev)
            uusd = (last_price - epx) * qty if is_long else (epx - last_price) * qty
        else:
            upct, uusd = 0.0, 0.0
        pc = GRN if upct >= 0 else RED
        fig.text(0.94, 0.955, f"{upct:+.2f}%", fontsize=22, color=pc,
                 ha="right", weight="bold")
        fig.text(0.94, 0.918, f"≈ ${uusd:+.2f}" if qty else "unreal",
                 fontsize=12, color=SEC, ha="right")
        fig.text(0.94, 0.888, "ОТКРЫТА", fontsize=10, color="#2563EB",
                 ha="right", weight="bold")
    else:
        pc = GRN if (pnl is not None and pnl >= 0) else RED
        fig.text(0.94, 0.955, f"{'+' if (pnl is not None and pnl >= 0) else ''}{pnl or 0:.2f}$",
                 fontsize=22, color=pc, ha="right", weight="bold")
        fig.text(0.94, 0.918, reason or "ЗАКРЫТА", fontsize=12, color=pc, ha="right", weight="bold")
        if xtm is not None:
            try:
                fig.text(0.94, 0.888, pd.to_datetime(xtm, utc=True).strftime("%Y-%m-%d %H:%M"),
                         fontsize=9, color=SEC, ha="right")
            except Exception:
                pass

    # ─── ГРАФИК (верхняя часть; данные сделки — текстом в Telegram) ──
    ax = fig.add_axes([0.05, 0.08, 0.90, 0.78])
    ax.set_facecolor(CARD)
    ax.set_ylim(y_lo, y_hi)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color=GRID, linewidth=0.5)
    ax.grid(True, axis="x", color=GRID, linewidth=0.3, linestyle=":")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(axis="y", labelsize=9, colors=SEC, length=0, pad=4)
    ax.tick_params(axis="x", labelsize=8, colors=SEC, length=0, pad=4)
    ax.set_xticks([])

    # подписи времени: первая / последняя (вход — отдельно, под вертикальной линией)
    ts_vals = df.index
    for xpos, anchor in ((0, "left"), (n - 1, "right")):
        try:
            lbl = ts_vals[xpos].strftime("%m-%d %H:%M")
        except Exception:
            continue
        ax.annotate(lbl, xy=(xpos, y_lo), xytext=(0, 4),
                    textcoords="offset points", fontsize=8, color=SEC,
                    ha=anchor, weight="bold")

    bw = 0.65 if n <= 80 else 0.5
    _draw_candles(ax, o, h, l, c, bw)

    # вертикальная линия входа + время входа под ней (v2.1)
    ax.axvline(x=ei, color=ENT, linewidth=1.0, linestyle=(0, (2, 3)), alpha=0.35, zorder=4)
    try:
        ets_lbl = ts_vals[ei].strftime("%d-%m %H:%M")
    except Exception:
        ets_lbl = ""
    if ets_lbl:
        # v2.2: дату входа поднимаем выше нижней строки дат (иначе накладывается
        # в правом нижнем углу на дату последней свечи)
        ax.text(ei, y_lo + rng * 0.10, ets_lbl, fontsize=8, color=ENT,
                ha="center", va="bottom", weight="bold", zorder=12)

    # уровни: TP, SL, ENTRY, текущая цена
    if tp and tp > 0:
        if tp_clamped:
            # TP на границе — не растягиваем свечи
            _level_line(ax, y_hi, GRN, f"TP {_fp(tp)} ↗", 0, n - 1, above=True)
        else:
            _level_line(ax, tp, GRN, "TP", 0, n - 1, above=True)
    if sl and sl > 0:
        _level_line(ax, sl, RED, "SL", 0, n - 1, above=False)
    # entry marker (v2.1: адаптивная позиция лейбла — не обрезается у правого края)
    ax.plot(ei, epx, "o", markersize=11, color=ENT, markeredgecolor=CARD,
            markeredgewidth=2.5, zorder=10)
    entry_ha = "left" if ei < n * 0.7 else "right"
    entry_dx = -8 if entry_ha == "left" else 8
    ax.annotate(f" ВХОД {_fp(epx)} ", xy=(ei, epx), xytext=(entry_dx, -26),
                textcoords="offset points", fontsize=10, fontweight="bold", color="#FFFFFF",
                va="top", ha=entry_ha,
                bbox=dict(boxstyle="round,pad=0.25", fc=ENT, ec="none"), zorder=12)
    # текущая/выходная точка (v2.1: лейбл привязан к последней свече, не висит справа)
    if is_open:
        mc = GRN if last_price >= epx else RED
        ax.plot(n - 1, last_price, "o", markersize=10, color=mc,
                markeredgecolor=CARD, markeredgewidth=2.5, zorder=10)
        ax.annotate(f" ТЕКУЩАЯ {_fp(last_price)} ", xy=(n - 1, last_price),
                    xytext=(-6, 14), textcoords="offset points", fontsize=10,
                    fontweight="bold", color="#FFFFFF", va="bottom", ha="right",
                    bbox=dict(boxstyle="round,pad=0.25", fc=mc, ec="none"),
                    arrowprops=dict(arrowstyle="-", color=mc, lw=0.8), zorder=12)
    else:
        if ext is not None and ext > 0:
            mc = RED if reason == "Стоп-лосс" else (GRN if reason == "Тейк-профит" else SEC)
            ax.plot(n - 1, ext, "o", markersize=11, color=mc,
                    markeredgecolor=CARD, markeredgewidth=2.5, zorder=10)
            # v3: лейбл не выходит за правую границу (ha=right, внутри графика)
            ax.annotate(f" ВЫХОД {_fp(ext)} ", xy=(n - 1, ext), xytext=(-6, 14),
                        textcoords="offset points", fontsize=10, fontweight="bold",
                        color="#FFFFFF", va="bottom", ha="right",
                        bbox=dict(boxstyle="round,pad=0.25", fc=mc, ec="none"),
                        arrowprops=dict(arrowstyle="-", color=mc, lw=0.8), zorder=12)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=BG, edgecolor="none",
                pad_inches=0, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _df_from_kls(kls, interval):
    df = pd.DataFrame(kls)
    ts = df["timestamp"].values.astype(float)
    if ts[0] > 1e12:
        ts /= 1000
    idx = pd.to_datetime(ts, unit="s", utc=True)
    return df.set_index(idx)[["open", "high", "low", "close"]].rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})


async def generate_trade_chart(
    exchange, symbol, entry_price, entry_time=None,
    exit_price=None, exit_time=None, side="BUY",
    sl=0., tp=0., interval="15", lookback=48,
    leverage=3, pnl=None, **kw,
):
    """v2: реальные Bybit свечи + минимальная разметка сделки (см. ТЗ)."""
    if entry_time is None:
        return None

    is_long = side.upper() in ("BUY", "LONG")
    is_open = exit_time is None or exit_price is None
    mfe_r = kw.get("mfe_r") or 0.0
    mae_r = kw.get("mae_r") or 0.0

    # Динамическое окно: график должен покрыть от входа ДО закрытия
    # (или до текущего момента для открытой позиции). Раньше post_bars=200
    # (≈62.5ч на 15m) — если позиция висела дольше, закрытие НЕ попадало
    # в картинку. Теперь считаем по факту: (exit_or_now − entry) + запас.
    tf_min = _interval_minutes(interval)
    try:
        if isinstance(entry_time, (int, float)):
            entry_ts = float(entry_time) if entry_time < 1e12 else float(entry_time) / 1000
        else:
            entry_ts = pd.Timestamp(entry_time).timestamp()
        end_ts = None
        if exit_time is not None:
            if isinstance(exit_time, (int, float)):
                end_ts = float(exit_time) if exit_time < 1e12 else float(exit_time) / 1000
            else:
                end_ts = pd.Timestamp(exit_time).timestamp()
        else:
            end_ts = time.time()
        span_hours = (end_ts - entry_ts) / 3600
        # Долгая позиция (>4.5 дней на 15m = 432 бара): укрупняем таймфрейм,
        # чтобы весь период влез в картинку и свечи остались читаемыми.
        if span_hours > 4.5 * 24 and interval == "15":
            interval = "60"
            tf_min = 60
        span_bars = int((end_ts - entry_ts) / (tf_min * 60)) + 10  # +10 бар запаса
        post_bars = min(max(span_bars, 60), 1000)
        # Bybit отдаёт максимум 1000 баров за запрос; берём с запасом
        post_bars = min(post_bars, 950)
    except Exception:
        post_bars = 200

    kls, entry_idx = None, 0
    real = await _fetch_real_klines(symbol, interval, entry_time,
                                    pre_bars=50, post_bars=post_bars)
    if real is not None:
        kls, entry_idx = real
    else:
        # фолбэк: синтетика на MFE/MAE
        if exit_time is None:
            exit_time = entry_time + timedelta(hours=6)
        if exit_price is None:
            exit_price = entry_price * (1.005 if is_long else 0.995)
        kls = _generate_synthetic_klines(
            entry_price, exit_price, entry_time, exit_time,
            sl, tp, is_long, mfe_r, mae_r)
    if not kls or len(kls) < 8:
        return None

    df = _df_from_kls(kls, interval)
    try:
        png = _render(
            symbol, df, entry_idx, side, leverage, entry_price, sl, tp,
            exit_price, entry_time, exit_time, is_open,
            kw.get("reason", ""), mfe_r, mae_r,
            kw.get("qty", 0.0), kw.get("current_price", 0.0), pnl,
            kw.get("prob"), kw.get("score"), kw.get("adx"),
        )
        return png
    except Exception as e:
        log.error(f"Chart v2 error: {e}", exc_info=True)
        return None


async def generate_guardian_chart(
    symbol, entry_price, side="BUY",
    old_sl=0.0, new_sl=0.0, current_price=0.0,
    peak_r=0.0, event_type="BE",
    leverage=1, entry_time=None,
) -> Optional[bytes]:
    """Guardian-событие: реальные свечи + старый/новый SL + текущая цена."""
    if entry_time is None:
        return None
    is_long = side.upper() in ("BUY", "LONG")
    if current_price <= 0:
        current_price = entry_price

    kls, entry_idx = None, 0
    real = await _fetch_real_klines(symbol, "15", entry_time, pre_bars=40, post_bars=120)
    if real is not None:
        kls, entry_idx = real
    else:
        exit_t = entry_time + timedelta(hours=6)
        kls = _generate_synthetic_klines(
            entry_price, current_price, entry_time, exit_t,
            old_sl, 0, is_long, peak_r if peak_r > 0 else 0.5, 0.0)
    if not kls or len(kls) < 8:
        return None
    df = _df_from_kls(kls, "15")

    n = len(df)
    o, h, l, c = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    sym = symbol.upper()
    for sf in ["-USDT", "/USDT", "USDT"]:
        if sym.endswith(sf):
            sym = sym[:-len(sf)]
            break
    ei = max(0, min(entry_idx, n - 1))
    lows, highs = float(np.min(l)), float(np.max(h))
    y_lo, y_hi = lows, highs
    for p_ in (entry_price, old_sl, new_sl, current_price):
        if p_ and p_ > 0:
            y_lo, y_hi = min(y_lo, p_), max(y_hi, p_)
    rng = y_hi - y_lo or entry_price * 0.02 or 1.0
    m10 = rng * 0.10
    y_lo -= m10
    y_hi += m10

    fig = plt.figure(figsize=(16, 9), facecolor=BG, dpi=100)
    sc = GRN if is_long else RED
    fig.text(0.06, 0.955, f"{sym}/USDT", fontsize=24, fontweight="bold", color=TXT)
    fig.text(0.06, 0.918, f"{'▲' if is_long else '▼'} {'LONG' if is_long else 'SHORT'} ×{max(1, leverage)}",
             fontsize=13, fontweight="bold", color=sc)
    names = {"BE": "Безубыток", "PARTIAL": "Частичная фиксация", "TIGHT": "Жёсткая защита"}
    fig.text(0.94, 0.955, names.get(event_type, event_type), fontsize=18,
             color="#2563EB", ha="right", weight="bold")
    fig.text(0.94, 0.918, f"Пик {peak_r:+.2f}R", fontsize=12, color=SEC, ha="right")

    ax = fig.add_axes([0.05, 0.08, 0.90, 0.78])
    ax.set_facecolor(CARD)
    ax.set_ylim(y_lo, y_hi)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color=GRID, linewidth=0.5)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(axis="y", labelsize=9, colors=SEC, length=0, pad=4)
    ax.set_xticks([])
    _draw_candles(ax, o, h, l, c, 0.65 if n <= 80 else 0.5)

    ax.axvline(x=ei, color=ENT, linewidth=1.0, linestyle=(0, (2, 3)), alpha=0.35, zorder=4)
    ax.plot(ei, entry_price, "o", markersize=11, color=ENT, markeredgecolor=CARD,
            markeredgewidth=2.5, zorder=10)
    # v2.1: адаптивная позиция лейбла — не обрезается у правого края
    entry_ha = "left" if ei < n * 0.7 else "right"
    entry_dx = -8 if entry_ha == "left" else 8
    ax.annotate(f" ВХОД {_fp(entry_price)} ", xy=(ei, entry_price),
                xytext=(entry_dx, -26),
                textcoords="offset points", fontsize=10, fontweight="bold", color="#FFFFFF",
                va="top", ha=entry_ha,
                bbox=dict(boxstyle="round,pad=0.25", fc=ENT, ec="none"), zorder=12)

    if old_sl > 0:
        _level_line(ax, old_sl, RED, "СТАРЫЙ SL", 0, n - 1, above=False)
    if new_sl > 0 and abs(new_sl - old_sl) > 1e-9:
        _level_line(ax, new_sl, GRN, "НОВЫЙ SL", 0, n - 1, above=True)
    lc = current_price or float(c[-1])
    mc = GRN if lc >= entry_price else RED
    ax.plot(n - 1, lc, "o", markersize=10, color=mc, markeredgecolor=CARD,
            markeredgewidth=2.5, zorder=10)
    ax.annotate(f" ТЕКУЩАЯ {_fp(lc)} ", xy=(n - 1, lc), xytext=(-6, 14),
                textcoords="offset points", fontsize=10, fontweight="bold",
                color="#FFFFFF", va="bottom", ha="right",
                bbox=dict(boxstyle="round,pad=0.25", fc=mc, ec="none"),
                arrowprops=dict(arrowstyle="-", color=mc, lw=0.8), zorder=12)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=BG, edgecolor="none",
                pad_inches=0, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
