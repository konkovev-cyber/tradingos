"""
meta_v1_retrain.py — weekly retrain of the meta_v1 LightGBM model.

What it does:
1. Load latest signal_log_clean.parquet + derivatives + microstructure
2. Forward-join to build target (fwd_4h_ret) from klines
3. Include BOTH passed and rejected signals
4. Train LightGBM with walk-forward CV (5 folds)
5. Save model + emit summary

Run: python3 /root/tradingos/ml/meta_v1/meta_v1_retrain.py
Cron: systemd timer (see meta_v1_retrain.service/timer below)
"""
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

OUT = Path("/root/tradingos/ml/meta_v1")
ROOT = Path("/root/tradingos")
LAB_DIR = Path("/root/tradingos_lab/edge_factory/data")
DERIV_PATH = LAB_DIR / "derivatives" / "derivatives.jsonl"
MICRO_DIR = LAB_DIR / "microstructure"

FEAT_COLS = ["score","rsi","adx","atr","ema20","final_probability","confidence","rejected"]
LABEL_HORIZON_BARS = 4   # 1h on M15 (best AUC vs 4h/24h)
TARGET_COL = "fwd_1h_ret"


def load_signals() -> pd.DataFrame:
    """Load BOTH passed (signal_log) and rejected (rejected_candidates) signals."""
    # Passed
    passed = []
    for line in (ROOT/"memory/signal_log.jsonl").open():
        try:
            d = json.loads(line)
        except Exception:
            continue
        sym = d.get("symbol", "")
        if sym.startswith("TEST"): continue
        if d.get("reject_reason"): continue
        passed.append({"ts_str": d["timestamp"], "symbol": sym,
                       "direction": d.get("direction",""), "score": d.get("score", 0),
                       "rsi": d.get("rsi", 0), "adx": d.get("adx", 0),
                       "atr": d.get("atr", 0), "ema20": d.get("ema20", 0),
                       "final_probability": d.get("final_probability", 0),
                       "confidence": d.get("confidence", 0.5),
                       "rejected": 0})
    df_p = pd.DataFrame(passed)
    if not df_p.empty:
        df_p["ts"] = pd.to_datetime(df_p["ts_str"], utc=True, errors="coerce")
        df_p["ts_epoch"] = df_p["ts"].astype("int64") // 10**6  # us → s

    # Rejected
    rejected = []
    for line in (ROOT/"guardian/rejected_candidates.jsonl").open():
        try:
            d = json.loads(line)
        except Exception:
            continue
        sym = d.get("symbol", "")
        if sym.startswith("TEST"): continue
        rejected.append({"ts_str": d["record_time"], "symbol": sym,
                         "direction": d.get("direction",""), "score": d.get("score", 0),
                         "rsi": 0, "adx": d.get("adx", 0),
                         "atr": d.get("atr", 0), "ema20": 0,
                         "final_probability": d.get("probability", 0),
                         "confidence": 0.5,
                         "rejected": 1})
    df_r = pd.DataFrame(rejected)
    if not df_r.empty:
        df_r["ts"] = pd.to_datetime(df_r["ts_str"], utc=True, errors="coerce")
        df_r["ts_epoch"] = df_r["ts"].astype("int64") // 10**6

    df = pd.concat([df_p, df_r], ignore_index=True)
    df = df.dropna(subset=["ts_epoch", "symbol"])
    df = df.drop_duplicates(subset=["ts_epoch","symbol","direction"], keep="first")
    return df


def load_klines() -> pd.DataFrame:
    rows = []
    for f in (ROOT/"replay_cache").glob("*_M15.parquet"):
        sym = f.stem.rsplit("_", 1)[0]
        df = pd.read_parquet(f)
        if "time" in df.columns: df = df.rename(columns={"time":"ts"})
        if df["ts"].iloc[0] > 1e12: df["ts"] = df["ts"] // 1000
        rows.append(df[["ts","c"]].assign(symbol=sym))
    return pd.concat(rows, ignore_index=True).sort_values(["symbol","ts"])


def join_targets(df: pd.DataFrame, klines: pd.DataFrame) -> pd.DataFrame:
    bar_seconds = 15 * 60
    horizon_s = LABEL_HORIZON_BARS * bar_seconds

    grouped = {sym: g.set_index("ts")["c"] for sym, g in klines.groupby("symbol")}

    rets = np.full(len(df), np.nan)
    for i, row in enumerate(df.itertuples()):
        sym = row.symbol; t = row.ts_epoch
        if sym not in grouped: continue
        kl = grouped[sym]
        try:
            entry_idx = kl.index[kl.index <= t]
            if len(entry_idx) == 0: continue
            entry_c = kl[entry_idx[-1]]
            future_idx = kl.index[kl.index <= t + horizon_s]
            if len(future_idx) < 5: continue
            future_c = kl[future_idx[-1]]
            if entry_c <= 0: continue
            ret = (future_c - entry_c) / entry_c
            if row.direction == "SELL": ret = -ret
            rets[i] = ret
        except Exception:
            continue
    df[TARGET_COL] = rets
    return df


def join_features(df: pd.DataFrame) -> pd.DataFrame:
    """Join derivatives + microstructure features."""
    # Derivatives
    if DERIV_PATH.exists():
        rows = []
        for line in DERIV_PATH.open():
            try: d = json.loads(line)
            except: continue
            rows.append({"ts": d["ts"]//1000 if d["ts"] > 1e12 else d["ts"],
                         "symbol": d["symbol"],
                         "deriv_funding": d.get("funding_rate", 0),
                         "deriv_oi": d.get("open_interest", 0),
                         "deriv_buy_ratio": d.get("buy_ratio", 0.5),
                         "deriv_sell_ratio": d.get("sell_ratio", 0.5)})
        df_deriv = pd.DataFrame(rows).sort_values("ts")
        df = pd.merge_asof(df.sort_values("ts_epoch"), df_deriv, left_on="ts_epoch",
                          right_on="ts", by="symbol", direction="backward", tolerance=900)
        df = df.drop(columns=["ts"], errors="ignore")

    # Microstructure
    micro_files = list(MICRO_DIR.glob("*.jsonl"))
    if micro_files:
        rows = []
        for f in micro_files:
            for line in f.open():
                try: d = json.loads(line)
                except: continue
                rows.append({"ts": int(d["ts"])//1000 if d["ts"] > 1e12 else int(d["ts"]),
                             "symbol": d["symbol"],
                             "spread_bps": d.get("spread_bps", 0),
                             "imb_10": d.get("imb_10", 0),
                             "imb_20": d.get("imb_20", 0),
                             "taker_imb": d.get("taker_imb", 0),
                             "n_trades": d.get("n_trades", 0)})
        df_micro = pd.DataFrame(rows).sort_values("ts")
        df = pd.merge_asof(df.sort_values("ts_epoch"), df_micro, left_on="ts_epoch",
                          right_on="ts", by="symbol", direction="backward", tolerance=300)
        df = df.drop(columns=["ts"], errors="ignore")
    return df


def train_and_save(df: pd.DataFrame) -> dict:
    df = df[df[TARGET_COL].notna()].copy()
    df["label"] = (df[TARGET_COL] > 0).astype(int)
    print(f"Training set: {len(df)} rows, WR={100*df['label'].mean():.1f}%")

    # Build feature list: start with FEAT_COLS, add deriv/micro only if sufficiently dense.
    # Sparse features (mostly -1 fill) hurt the model — model treats -1 as a class signal.
    feat_cols = list(FEAT_COLS)
    candidate_cols = [c for c in df.columns if c.startswith(("deriv_", "spread_", "imb_", "taker_", "n_trades"))]
    for c in candidate_cols:
        if c in df.columns and df[c].notna().mean() >= 0.30:  # ≥30% non-null
            feat_cols.append(c)
    print(f"Features: {feat_cols}")

    X = df[feat_cols].fillna(-1); y = df["label"].values

    # Time-series CV
    tscv = TimeSeriesSplit(n_splits=5)
    fold_aucs = []
    feature_imp = {c: 0 for c in feat_cols}

    for fold, (tr, vl) in enumerate(tscv.split(X)):
        m = lgb.LGBMClassifier(n_estimators=500, learning_rate=0.05, max_depth=6, num_leaves=31,
                              subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.1,
                              random_state=42, verbose=-1)
        m.fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[vl])[:, 1]
        auc = roc_auc_score(y[vl], p)
        fold_aucs.append(auc)
        for c, imp in zip(feat_cols, m.feature_importances_):
            feature_imp[c] += imp
        print(f"  fold {fold}: AUC={auc:.3f}  n_vl={len(vl)}")

    mean_auc = float(np.mean(fold_aucs))
    print(f"\nMean OOF AUC: {mean_auc:.3f} ± {np.std(fold_aucs):.3f}")

    # Train final on all data
    final = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, max_depth=6, num_leaves=31,
                              subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.1,
                              random_state=42, verbose=-1)
    final.fit(X, y)
    final.booster_.save_model(str(OUT/"meta_v1_full_model.txt"))
    print(f"Saved model to {OUT/'meta_v1_full_model.txt'}")

    # Save feature importance
    imp_sorted = sorted(feature_imp.items(), key=lambda x: -x[1])
    return {
        "mean_auc": mean_auc,
        "std_auc": float(np.std(fold_aucs)),
        "n_train": int(len(df)),
        "wr_pct": float(100*df["label"].mean()),
        "feature_importance": [{"f": f, "imp": int(i)} for f, i in imp_sorted],
        "n_folds": len(fold_aucs),
    }


def main():
    t0 = time.time()
    print("Loading signals...")
    df = load_signals()
    print(f"  total signals: {len(df)}, span {df['ts'].min()} → {df['ts'].max()}")

    print("\nLoading klines...")
    klines = load_klines()
    print(f"  klines: {len(klines)} rows")

    print("\nJoining forward returns...")
    df = join_targets(df, klines)
    print(f"  with {TARGET_COL}: {df[TARGET_COL].notna().sum()}")

    print("\nJoining derivatives + microstructure...")
    df = join_features(df)

    print("\nTraining...")
    summary = train_and_save(df)
    summary["generated_at"] = datetime.utcnow().isoformat()
    summary["elapsed_sec"] = round(time.time() - t0, 1)
    (OUT/"last_retrain_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone in {summary['elapsed_sec']}s")


if __name__ == "__main__":
    main()
