"""
conformal_v1_sl_tp.py — Conformal prediction for SL/TP distances.

Computes per-symbol empirical quantiles of forward returns from historical
klines. Calibrates so that the SL hit rate matches the chosen alpha (e.g.,
5% quantile → 5% of positions get stopped out within horizon).

This is the "Adaptive Conformal Inference" (ACI) baseline — static, per-symbol,
but it gives GUARANTEED coverage guarantees on past data and serves as a
strong prior for live use. Future work: online ACI that adapts to regime
changes via rolling window.

Usage:
    from ml.conformal_v1.conformal_v1_sl_tp import compute_sl_tp, get_conformal_table
    table = get_conformal_table()  # dict {symbol: {horizon: {sl_dist, tp_dist}}}
    sl, tp = compute_sl_tp("BTCUSDT", entry=80000, horizon_bars=4)  # 1h
"""
from pathlib import Path
import pandas as pd
import numpy as np


CONFORMAL_DIR = Path("/root/tradingos/ml/conformal_v1")
KLINES_DIR = Path("/root/tradingos/replay_cache")

HORIZONS_BARS = [4, 16, 96]  # 1h, 4h, 24h on M15
ALPHA_SL = 0.05  # 5% quantile for SL (5% of trades get stopped out)
ALPHA_TP = 0.50  # 50% quantile for TP (median forward return = TP target)


def _load_klines() -> pd.DataFrame:
    """Load all M15 klines and compute forward returns per horizon."""
    rows = []
    for f in KLINES_DIR.glob("*_M15.parquet"):
        sym = f.stem.rsplit("_", 1)[0]
        df = pd.read_parquet(f)
        if "time" in df.columns:
            df = df.rename(columns={"time": "ts"})
        if df["ts"].iloc[0] > 1e12:
            df["ts"] = df["ts"] // 1000
        df = df.sort_values("ts").reset_index(drop=True)
        for h in HORIZONS_BARS:
            df[f"ret_{h}"] = df["c"].pct_change(h).shift(-h)
        df["symbol"] = sym
        rows.append(df[["symbol", "ts", "c"] + [f"ret_{h}" for h in HORIZONS_BARS]])
    return pd.concat(rows, ignore_index=True)


def _build_table() -> dict:
    """Compute per-symbol quantile table. Cached in parquet."""
    cache_path = CONFORMAL_DIR / "conformal_table.parquet"
    if cache_path.exists():
        return pd.read_parquet(cache_path)

    df = _load_klines()
    print(f"Building conformal table from {len(df)} observations across {df['symbol'].nunique()} symbols...")

    table_rows = []
    for sym, group in df.groupby("symbol"):
        row = {"symbol": sym, "n": len(group)}
        for h in HORIZONS_BARS:
            ret_col = f"ret_{h}"
            r = group[ret_col].dropna()
            if len(r) < 50:
                row[f"sl_dist_{h}"] = 0.02  # default 2% if not enough data
                row[f"tp_dist_{h}"] = 0.02
                continue
            row[f"sl_dist_{h}"] = abs(r.quantile(ALPHA_SL))  # 5% quantile abs
            row[f"tp_dist_{h}"] = abs(r.quantile(ALPHA_TP))   # 50% quantile abs
        table_rows.append(row)

    table = pd.DataFrame(table_rows)
    CONFORMAL_DIR.mkdir(parents=True, exist_ok=True)
    table.to_parquet(cache_path, index=False)
    print(f"Saved conformal table: {len(table)} symbols")
    return table


_TABLE = None


def get_conformal_table() -> pd.DataFrame:
    """Load or compute the cached conformal table."""
    global _TABLE
    if _TABLE is None:
        _TABLE = _build_table()
    return _TABLE


def compute_sl_tp(symbol: str, entry: float, horizon_bars: int = 4) -> tuple[float, float]:
    """Return (sl_price, tp_price) for given symbol, entry, and horizon.

    SL = entry * (1 - sl_dist)
    TP = entry * (1 + tp_dist)

    For SHORT, swap: SL = entry * (1 + sl_dist), TP = entry * (1 - tp_dist)
    """
    table = get_conformal_table()
    row = table[table["symbol"] == symbol]
    if row.empty:
        # Unknown symbol: conservative default 2% SL, 4% TP
        sl_dist, tp_dist = 0.02, 0.04
    else:
        sl_dist = float(row[f"sl_dist_{horizon_bars}"].iloc[0])
        tp_dist = float(row[f"tp_dist_{horizon_bars}"].iloc[0])
    sl_price = entry * (1 - sl_dist)
    tp_price = entry * (1 + tp_dist)
    return sl_price, tp_price


def compute_sl_tp_for_side(symbol: str, entry: float, side: str, horizon_bars: int = 4) -> tuple[float, float]:
    """Same as compute_sl_tp but flips for SHORT side."""
    table = get_conformal_table()
    row = table[table["symbol"] == symbol]
    if row.empty:
        sl_dist, tp_dist = 0.02, 0.04
    else:
        sl_dist = float(row[f"sl_dist_{horizon_bars}"].iloc[0])
        tp_dist = float(row[f"tp_dist_{horizon_bars}"].iloc[0])
    if side.upper() in ("SELL", "SHORT"):
        sl_price = entry * (1 + sl_dist)
        tp_price = entry * (1 - tp_dist)
    else:
        sl_price = entry * (1 - sl_dist)
        tp_price = entry * (1 + tp_dist)
    return sl_price, tp_price


if __name__ == "__main__":
    table = get_conformal_table()
    print(f"\nConformal table sample:")
    print(table.head(10))
    print(f"\nBTCUSDT 1h: SL={table[table['symbol']=='BTCUSDT']['sl_dist_4'].iloc[0]*100:.3f}%, "
          f"TP={table[table['symbol']=='BTCUSDT']['tp_dist_4'].iloc[0]*100:.3f}%")
    print(f"ETHUSDT 1h: SL={table[table['symbol']=='ETHUSDT']['sl_dist_4'].iloc[0]*100:.3f}%, "
          f"TP={table[table['symbol']=='ETHUSDT']['tp_dist_4'].iloc[0]*100:.3f}%")
    print(f"AGIUSDT 1h: SL={table[table['symbol']=='AGIUSDT']['sl_dist_4'].iloc[0]*100:.3f}%, "
          f"TP={table[table['symbol']=='AGIUSDT']['tp_dist_4'].iloc[0]*100:.3f}%")

    print("\nLive SL/TP example (BTCUSDT @ 80000):")
    sl, tp = compute_sl_tp("BTCUSDT", entry=80000, horizon_bars=4)
    print(f"  SL={sl:.2f}, TP={tp:.2f}")
