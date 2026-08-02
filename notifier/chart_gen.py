"""
TradingOS Trade Chart v18 — Synthetic price data.
Генерирует реалистичный график сделки: вход → максимум → выход.
Временная шкала = реальная длительность сделки.
Адаптивный таймфрейм.
"""
from __future__ import annotations
import io, logging, asyncio, threading, random, math
from typing import Optional
from datetime import timedelta
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

log = logging.getLogger("TradingOS.ChartGen")
_LOCK = threading.Lock()

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Inter", "SF Pro", "DejaVu Sans", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

# ─── LIGHT THEME ────────────────────────────────────────────
BG    = "#F8FAFC"
CARD  = "#FFFFFF"
TXT   = "#0F172A"
SEC   = "#64748B"
GRN   = "#059669"
RED   = "#DC2626"
ENT   = "#3B82F6"
GRID  = (0,0,0,0.04)


def _fp(p: float) -> str:
    if p is None: return "—"
    if p >= 1000: return f"{p:,.2f}"
    if p >= 1: return f"{p:.2f}"
    return f"{p:.4f}"


def _choose_tf(duration_minutes: float) -> int:
    """Выбор таймфрейма по длительности сделки."""
    if duration_minutes < 60: return 5
    elif duration_minutes < 240: return 15
    elif duration_minutes < 720: return 30
    elif duration_minutes < 1440: return 60
    else: return 240


def _generate_synthetic_klines(entry_price, exit_price, entry_time, exit_time,
                                sl, tp, is_long, mfe_r=0.0, mae_r=0.0):
    """Генерирует синтетические свечи, показывающие историю сделки.
    
    Путь цены: вход → максимум (MFE) → выход (SL/TP).
    Временная шкала = реальная длительность сделки.
    """
    if entry_time is None or exit_time is None:
        return None
    
    # Длительность в минутах
    duration_sec = (exit_time - entry_time).total_seconds()
    duration_min = duration_sec / 60
    tf_min = _choose_tf(duration_min)
    
    # Количество свечей
    num_candles = max(10, int(duration_min / tf_min) + 1)
    
    # R = расстояние от входа до стопа
    r_value = abs(entry_price - sl) if sl and sl > 0 else abs(entry_price) * 0.01

    # Максимум и минимум
    if is_long:
        max_price = entry_price + mfe_r * r_value if mfe_r else max(entry_price, exit_price) * 1.005
        min_price = entry_price + mae_r * r_value if mae_r else min(entry_price, exit_price) * 0.995
    else:
        max_price = entry_price - mae_r * r_value if mae_r else max(entry_price, exit_price) * 1.005
        min_price = entry_price + mfe_r * r_value if mfe_r else min(entry_price, exit_price) * 0.995
    
    # Гарантируем, что max > entry > min и exit в диапазоне
    if is_long:
        # max_price must be at least exit_price (candles must show full move)
        if mfe_r and mfe_r > 0:
            max_price = max(entry_price + mfe_r * r_value, exit_price)
        else:
            max_price = max(max_price, entry_price, exit_price)
        min_price = min(min_price, entry_price, exit_price)
        # CRITICAL: For LONG, price cannot go below SL (would have triggered)
        if sl and sl > 0:
            min_price = max(min_price, sl)
    else:
        if mfe_r and mfe_r > 0:
            max_price = max(entry_price - mfe_r * r_value, exit_price)
        else:
            max_price = max(max_price, entry_price, exit_price)
        min_price = min(min_price, entry_price, exit_price)
        # CRITICAL: For SHORT, price cannot go above SL
        if sl and sl > 0:
            max_price = min(max_price, sl)
    
    # Генерируем временные метки
    times = [entry_time + timedelta(minutes=tf_min * i) for i in range(num_candles)]
    
    # Ценовой путь: 40% времени рост до максимума, 60% падение до выхода
    mid = int(num_candles * 0.4)
    prices = []
    for i in range(num_candles):
        if i <= mid:
            fraction = i / mid if mid > 0 else 0
            price = entry_price + (max_price - entry_price) * fraction
        else:
            fraction = (i - mid) / (num_candles - mid - 1) if (num_candles - mid - 1) > 0 else 1
            price = max_price - (max_price - exit_price) * fraction
        
        # Шум (зависит от диапазона цен, не от фиксированного 1.0)
        price_range = max_price - min_price
        noise_std = max(price_range * 0.02, entry_price * 0.0001)  # 2% of range
        noise = random.gauss(0, noise_std)
        price += noise
        # CLAMP: цена не может выйти за max_price/min_price
        price = max(min_price, min(max_price, price))
        prices.append(price)
    
    # Гарантируем первую и последнюю точки
    prices[0] = entry_price
    prices[-1] = exit_price
    
    # Генерируем OHLCV из close
    klines = []
    for i in range(num_candles):
        close = prices[i]
        noise = random.gauss(0, noise_std * 0.5)
        open_price = prices[i-1] if i > 0 else close
        # CLAMP high/low to [min_price, max_price]
        high = min(max_price, max(open_price, close) + abs(noise) * 0.5)
        low = max(min_price, min(open_price, close) - abs(noise) * 0.5)
        volume = random.uniform(100, 1000)
        ts = int(times[i].timestamp())
        klines.append({
            "timestamp": ts,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "Volume": volume,
        })
    
    return klines


def _build(symbol, epx, etm, ext, xtm, side, sl, tp, interval, lev, pnl, kls, **kw):
    """v18: рисует график по синтетическим данным.
    
    Временная шкала = реальная длительность сделки.
    Entry слева, exit справа.
    """
    fig = None
    try:
        if not kls or len(kls) < 5:
            return None
        
        is_long = side.upper() in ("BUY", "LONG")
        sym = symbol.upper()
        for sf in ["-USDT", "/USDT", "USDT"]:
            if sym.endswith(sf): sym = sym[:-len(sf)]; break
        
        # Парсим свечи
        df = pd.DataFrame(kls)
        ts_col, cm = None, {}
        for c in df.columns:
            cl = c.lower()
            if cl == "timestamp": ts_col = c
            elif cl in ("open", "high", "low", "close"): cm[c] = cl.title()
        if ts_col is None:
            for c in df.columns:
                if "time" in c.lower(): ts_col = c; break
        if ts_col: cm[ts_col] = "timestamp"
        df = df.rename(columns=cm)
        cols = ["Open", "High", "Low", "Close"]
        if "timestamp" in df.columns: cols.append("timestamp")
        df = df[cols].dropna()
        if len(df) < 5: return None
        if "timestamp" in df.columns:
            ts = df["timestamp"].values.astype(float)
            if ts[0] > 1e12: ts /= 1000
            idx = pd.to_datetime(ts, unit="s", utc=True)
        else:
            idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=len(df), freq=_norm_int(interval))
        df = df.set_index(idx)[["Open", "High", "Low", "Close"]].dropna()
        df = df.sort_index()
        if len(df) < 5: return None
        
        n = len(df)
        
        # Определяем reason (do NOT overwrite ext to avoid contradiction with text)
        reason = "Ручное"
        if ext is not None:
            if sl > 0:
                if (is_long and ext <= sl * 1.005) or (not is_long and ext >= sl * 0.995):
                    reason = "Стоп-лосс"
            if reason == "Ручное" and tp > 0:
                if (is_long and ext >= tp * 0.995) or (not is_long and ext <= tp * 1.005):
                    reason = "Тейк-профит"
        
        # FIX: PnL% should be on INVESTED CAPITAL (margin), not price
        # qty might not be available here, so use leverage=lev
        # margin = epx * qty / lev. If qty is not given, use pnl / epx * lev approx
        pnl_pct = 0.0
        if pnl is not None and abs(pnl) > 1e-9 and epx > 0:
            # Use leverage for sensible %: pnl% ≈ price move% × leverage
            price_move_pct = ((ext - epx) / epx * 100) if is_long else ((epx - ext) / epx * 100) if ext else 0
            pnl_pct = price_move_pct * max(1, lev)
            # Sanity clamp: pnl_pct should match PnL sign
            if (pnl > 0 and pnl_pct < 0) or (pnl < 0 and pnl_pct > 0):
                pnl_pct = -pnl_pct
        elif ext and epx > 0:
            price_move_pct = ((ext - epx) / epx * 100) if is_long else ((epx - ext) / epx * 100)
            pnl_pct = price_move_pct * max(1, lev)
        is_profit = pnl is not None and pnl > 0 if abs(pnl or 0) > 1e-9 else pnl_pct > 0
        is_be = not (abs(pnl or 0) > 1e-9 or abs(pnl_pct) > 0.01)
        pa = abs(pnl) if pnl and abs(pnl) > 1e-9 else (abs(pnl_pct) / 100 * epx * max(1, lev) if ext else 0)
        
        # Y range
        y_lo, y_hi = df["Low"].min(), df["High"].max()
        rng = y_hi - y_lo
        if rng == 0: rng = max(abs(y_hi), 1.0) * 0.01
        pad = rng * 0.20
        y_lo -= pad; y_hi += pad
        
        # ─── FIGURE 800x800 ─────────────────────────────────
        fig = plt.figure(figsize=(8, 8), facecolor=BG, dpi=100)
        
        # ─── HEADER ─────────────────────────────────────────
        fig.text(0.06, 0.915, f"{sym}/USDT", fontsize=18, fontweight="bold", color=TXT)
        sc = GRN if is_long else RED
        fig.text(0.06, 0.885, f"{'▲' if is_long else '▼'} {'LONG' if is_long else 'SHORT'} ×{max(1, lev)}",
                 fontsize=10, fontweight="bold", color=sc)
        
        if ext is not None:
            rc = RED if reason == "Стоп-лосс" else GRN
            fig.text(0.50, 0.915, "ЗАКРЫТА", fontsize=7, color=SEC, ha="center", weight="bold")
            fig.text(0.50, 0.890, reason, fontsize=11, color=rc, ha="center", weight="bold")
            if xtm is not None:
                try:
                    fig.text(0.50, 0.870, pd.to_datetime(xtm, utc=True).strftime("%Y-%m-%d %H:%M"),
                             fontsize=7, color=SEC, ha="center")
                except Exception:
                    pass
        
        if ext is not None:
            pc = GRN if is_profit else RED
            sn = "+" if is_profit else "-"
            fig.text(0.94, 0.915, f"{sn}${pa:.2f}" if pa > 0 else "$0.00",
                     fontsize=20, color=pc, ha="right", weight="bold", transform=fig.transFigure)
            fig.text(0.94, 0.888, f"{pnl_pct:+.2f}%", fontsize=10, color=pc,
                     ha="right", transform=fig.transFigure)
            badge = "ПРИБЫЛЬ" if is_profit else "УБЫТОК"
            bc = GRN if is_profit else RED
            if is_be: badge = "0"; bc = SEC
            fig.text(0.94, 0.866, badge, fontsize=7, color=bc, ha="right", weight="bold",
                     transform=fig.transFigure,
                     bbox=dict(boxstyle="round,pad=0.12", fc=bc + "18", ec=bc + "50", lw=0.4))
        
        # ─── CHART ───────────────────────────────────────────
        ax = fig.add_axes([0.10, 0.20, 0.80, 0.55])
        ax.set_facecolor(CARD)
        
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        c = df["Close"].values
        
        bw = min(0.70, 8 / n)
        
        for i in range(n):
            is_up = c[i] >= o[i]
            clr = GRN if is_up else RED
            bh = max(abs(c[i] - o[i]), rng * 0.0006)
            body_low = min(o[i], c[i])
            ax.plot([i, i], [l[i], h[i]], color=clr, linewidth=1.5,
                    solid_capstyle="butt", zorder=2)
            ax.add_patch(Rectangle(
                (i - bw / 2, body_low), bw, bh,
                facecolor=clr, edgecolor=clr, linewidth=1.0, alpha=1.0, zorder=3,
            ))
        
        # Grid
        ax.set_axisbelow(True)
        ax.grid(True, axis="y", color=GRID, linewidth=0.5, alpha=0.7)
        ax.grid(True, axis="x", color=GRID, linewidth=0.3, alpha=0.4, linestyle=":")
        ax.set_ylim(y_lo, y_hi)
        ax.set_xlim(-0.5, n - 0.5)
        
        # ─── TIME AXIS (реальные таймстампы, без наложения) ─
        step = max(1, n // 5)
        ticks = list(range(0, n, step))
        if ticks[-1] != n - 1:
            ticks.append(n - 1)
        tls = [df.index[i].strftime("%H:%M") for i in ticks if i < n]
        ax.set_xticks(ticks[:len(tls)])
        ax.set_xticklabels(tls, fontsize=8, color=SEC, weight="bold", rotation=30, ha="right")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(axis="y", labelsize=8, colors=SEC, length=0, pad=4)
        
        # ─── ENTRY MARKER (первая свеча, слева) ─────────────
        ey = c[0]
        ax.plot(0, ey, "o", markersize=12, color=ENT,
                markeredgecolor=CARD, markeredgewidth=2.5, zorder=10)
        ax.axhline(y=epx, color=ENT, linewidth=1.2, linestyle=(0, (4, 3)),
                   alpha=0.6, zorder=1)
        ax.annotate(
            f" ВХОД {epx:.4f} ",
            xy=(0, ey), xytext=(-10, -22), textcoords="offset points",
            fontsize=9, fontweight="bold", color="#FFFFFF",
            va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", fc=ENT, ec="none"),
            arrowprops=dict(arrowstyle="-", color=ENT, lw=0.8, alpha=0.6),
            zorder=12,
        )
        
        # ─── EXIT MARKER (последняя свеча, справа) ──────────
        if ext is not None:
            xi = n - 1
            if reason == "Стоп-лосс":
                marker_y = l[xi]; mc = RED
            elif reason == "Тейк-профит":
                marker_y = h[xi]; mc = GRN
            else:
                marker_y = c[xi]; mc = SEC
            lbl = "SL" if reason == "Стоп-лосс" else "TP" if reason == "Тейк-профит" else "ВЫХОД"
            ax.plot(xi, marker_y, "o", markersize=12, color=mc,
                    markeredgecolor=CARD, markeredgewidth=2.5, zorder=10)
            ax.axhline(y=ext, color=mc, linewidth=1.2, linestyle=(0, (4, 3)),
                       alpha=0.6, zorder=1)
            ax.annotate(
                f" {lbl} {ext:.4f} ",
                xy=(xi, marker_y), xytext=(10, 22), textcoords="offset points",
                fontsize=9, fontweight="bold", color="#FFFFFF",
                va="bottom", ha="right",
                bbox=dict(boxstyle="round,pad=0.3", fc=mc, ec="none"),
                arrowprops=dict(arrowstyle="-", color=mc, lw=0.8, alpha=0.6),
                zorder=12,
            )
        
        # ─── TRADE PATH LINE ────────────────────────────────
        path_color = GRN if is_profit else RED
        ax.plot(range(n), c, color=path_color, linewidth=2.0,
                alpha=0.5, zorder=4, solid_capstyle="round")
        
        # ─── MAX/MIN LABELS (calculated from ACTUAL candle data) ────
        # CRITICAL: calculate from real candle high/low so labels match chart
        r_dist = abs(epx - sl) if (sl and sl > 0) else 0

        # Find actual max/min from candle data
        max_idx = int(np.argmax(h))
        min_idx = int(np.argmin(l))
        actual_max_price = h[max_idx]
        actual_min_price = l[min_idx]

        # Convert to R-multiple for display
        if r_dist > 0:
            if is_long:
                max_r = (actual_max_price - epx) / r_dist
                min_r = (actual_min_price - epx) / r_dist
            else:
                max_r = (epx - actual_max_price) / r_dist
                min_r = (epx - actual_min_price) / r_dist
        else:
            max_r = min_r = 0

        # Draw MAX label at actual max candle
        if max_idx > 0 and max_idx < n - 1:
            ax.annotate(
                f"MAX {max_r:+.2f}R",
                xy=(max_idx, actual_max_price), xytext=(0, 18), textcoords="offset points",
                fontsize=8, fontweight="bold", color=GRN,
                ha="center", va="bottom",
                arrowprops=dict(arrowstyle="->", color=GRN, lw=1.2),
                zorder=12,
            )

        # Draw MIN label at actual min candle (allow edge candles)
        if min_idx != max_idx:
            ax.annotate(
                f"MIN {min_r:+.2f}R",
                xy=(min_idx, actual_min_price), xytext=(0, -18), textcoords="offset points",
                fontsize=8, fontweight="bold", color=RED,
                ha="center", va="top",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.2),
                zorder=12,
            )

        # Y-axis label — show PRICES (USDT) since candles are in price units
        ax.set_ylabel(f"Цена (USDT)", fontsize=8, color=SEC, weight="bold")
        
        # ─── FOOTER ──────────────────────────────────────
        dur = "--"
        # Use holding_hours from kw if available (matches text message)
        holding_hours = kw.get("holding_hours", 0)
        if holding_hours and holding_hours > 0:
            h = int(holding_hours)
            m = int((holding_hours - h) * 60)
            dur = f"{h}ч {m}м" if h > 0 else f"{m}м"
        elif ext is not None and etm is not None and xtm is not None:
            try:
                s_ = int((pd.to_datetime(xtm, utc=True) - pd.to_datetime(etm, utc=True)).total_seconds())
                h_, m_ = divmod(max(0, s_), 3600); dur = f"{h_}ч {m_ // 60}м"
            except Exception:
                pass
        rr = "--"
        if sl > 0 and ext is not None and abs(epx - sl) > 0:
            r_mult = (ext - epx) / abs(epx - sl) if is_long else (epx - ext) / abs(epx - sl)
            rr = f"{r_mult:+.2f}R"
        fc = GRN if is_profit else RED
        sn = "+" if is_profit else "-"
        
        ft = [
            ("ВХОД",   _fp(epx),                            TXT),
            ("ВЫХОД",  _fp(ext) if ext is not None else "--", TXT),
            ("P&L",    f"{sn}${pa:.2f}" if not is_be else "0", fc),
            ("R:R",    rr,                                   TXT),
            ("ВРЕМЯ",  dur,                                  SEC),
        ]
        n_ft = len(ft)
        fw = 0.88 / n_ft
        for i, (lb, vl, cl) in enumerate(ft):
            x = 0.06 + i * fw
            fig.text(x, 0.115, lb, fontsize=7, color=SEC, va="center", weight="bold")
            fig.text(x, 0.075, vl, fontsize=12, color=cl, va="center", weight="bold")
        
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=BG, edgecolor="none",
                    pad_inches=0, bbox_inches=None)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        log.error(f"Chart error: {e}", exc_info=True)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


async def generate_trade_chart(
    exchange, symbol, entry_price, entry_time=None,
    exit_price=None, exit_time=None, side="BUY",
    sl=0., tp=0., interval="15", lookback=48,
    leverage=3, pnl=None, **kw,
) -> Optional[bytes]:
    """v21: SINGLE-SOURCE validation. Mfe/mae R values normalized ONCE at entry,
    then propagated to both synthetic and label display.
    """
    if entry_time is None:
        return None

    # ─── SINGLE VALIDATION POINT ───────────────────────────
    # Normalize mfe_r/mae_r ONCE here, both will be applied uniformly downstream
    mfe_r_raw = kw.get("mfe_r", None)
    mae_r_raw = kw.get("mae_r", None)

    # Reject None, reject outliers (abs > 10R is unrealistic for normal trades)
    mfe_r = float(mfe_r_raw) if (mfe_r_raw is not None and -10 < mfe_r_raw < 10) else 0.0
    mae_r = float(mae_r_raw) if (mae_r_raw is not None and -10 < mae_r_raw < 10) else 0.0

    if mfe_r_raw is not None and abs(mfe_r_raw) > 10:
        log.warning(f"RAW mfe_r={mfe_r_raw} REJECTED — replaced with 0 (synthetic fallback)")
    if mae_r_raw is not None and abs(mae_r_raw) > 10:
        log.warning(f"RAW mae_r={mae_r_raw} REJECTED — replaced with 0 (synthetic fallback)")

    # Log normalized values going INTO chart
    log.info(
        f"CHART_INPUT symbol={symbol} side={side} "
        f"mfe_r_norm={mfe_r:.3f} (raw={mfe_r_raw}) "
        f"mae_r_norm={mae_r:.3f} (raw={mae_r_raw}) "
        f"sl={sl} entry={entry_price} exit={exit_price}"
    )

    is_long = side.upper() in ("BUY", "LONG")

    # For OPEN card (no exit), use entry_time + 6h as window
    if exit_time is None:
        exit_time = entry_time + timedelta(hours=6)
        if exit_price is None:
            exit_price = entry_price * (1.005 if is_long else 0.995)

    kls = _generate_synthetic_klines(
        entry_price, exit_price, entry_time, exit_time,
        sl, tp, is_long, mfe_r, mae_r,
    )
    if not kls or len(kls) < 5:
        return None

    # Pass through the NORMALIZED values so chart and text agree
    with _LOCK:
        return _build(symbol, entry_price, entry_time, exit_price, exit_time,
                      side, sl, tp, interval, leverage, pnl, kls,
                      holding_hours=kw.get("holding_hours", 0),
                      mfe_r=mfe_r, mae_r=mae_r)
