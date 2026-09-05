"""
regime_bandit.py — Contextual Thompson Sampling bandit for regime-based risk allocation.

Per regime r ∈ {0,1,2,3}, maintains Beta(alpha, beta) of win probability.
On each new signal:
1. Load regime from features
2. Sample from Beta_r for each regime
3. Choose action: bet size = base_risk * P(win | regime) / P(win | baseline)
4. After fill + exit: update alpha[r] if WIN, beta[r] if LOSS

Usage:
    from regimes_v1.regime_bandit import RegimeBandit
    bandit = RegimeBandit()
    regime = bandit.predict_regime(features_row)
    bet_size = bandit.get_bet_size(base_risk=500, regime=regime)
    # after exit:
    bandit.update(regime=regime, win=True/False)
"""
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path("/root/tradingos/regimes_v1")


def _load_model():
    with open(OUT/"gmm_model.pkl", "rb") as f:
        return pickle.load(f)


class RegimeBandit:
    """Thompson Sampling bandit for per-regime bet sizing."""

    def __init__(self, alpha_init: float = 1.0, beta_init: float = 1.0, prior_n: float = 20.0):
        # Initialize Beta posteriors from prior WR estimates (n0 pseudo-observations).
        # This makes Thompson sampling aware of the regime WR prior before live data.
        self.prior_wr = {0: 0.615, 1: 0.578, 2: 0.598, 3: 0.750}
        self.alpha = {r: max(0.5, self.prior_wr[r] * prior_n) for r in range(4)}
        self.beta = {r: max(0.5, (1 - self.prior_wr[r]) * prior_n) for r in range(4)}
        # Map regime → semantic name (based on GMM means)
        self.regime_names = {0: "VOL_SPIKE", 1: "TRENDING_UP", 2: "QUIET", 3: "BEAR_DROP"}
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def predict_regime(self, features: dict) -> int:
        """Predict regime (0..3) from feature dict {log_ret, vol_z, abs_funding, oi_chg}."""
        import numpy as np
        X = np.array([[features.get(c, 0) for c in self.model["feat_cols"]]], dtype=np.float64)
        X_s = self.model["scaler"].transform(X)
        return int(self.model["gmm"].predict(X_s)[0])

    def get_bet_size(self, base_risk: float, regime: int) -> float:
        """Thompson sample: sample P(win) from each regime, choose the best.
        Returns scaled bet_size in USD."""
        # Sample P(win) from Beta for each regime
        samples = {}
        for r in range(4):
            samples[r] = np.random.beta(self.alpha[r], self.beta[r])
        best_regime = max(samples, key=samples.get)
        # If user's regime isn't best → skip
        if regime != best_regime:
            return 0.0  # don't take the trade this cycle
        # Scale bet by sample confidence
        sample_wr = samples[best_regime]
        # Base bet × (sample_wr / prior_wr)
        # If sample_wr > prior_wr → bigger bet; if lower → smaller
        prior = self.prior_wr.get(regime, 0.5)
        multiplier = sample_wr / prior if prior > 0 else 1.0
        return base_risk * max(0.3, min(2.0, multiplier))

    def update(self, regime: int, win: bool):
        """Update Beta posterior for regime after trade outcome."""
        if win:
            self.alpha[regime] += 1
        else:
            self.beta[regime] += 1

    def get_stats(self) -> dict:
        """Get current Beta statistics."""
        return {
            "regimes": {
                r: {
                    "name": self.regime_names.get(r, f"R{r}"),
                    "alpha": self.alpha[r],
                    "beta": self.beta[r],
                    "mean_wr": self.alpha[r] / (self.alpha[r] + self.beta[r]),
                    "samples": self.alpha[r] + self.beta[r] - 2,  # excluding priors
                }
                for r in range(4)
            }
        }

    def save(self, path: Path | None = None):
        path = path or (OUT/"bandit_state.json")
        with open(path, "w") as f:
            json.dump({"alpha": self.alpha, "beta": self.beta}, f)

    def load(self, path: Path | None = None):
        path = path or (OUT/"bandit_state.json")
        if not Path(path).exists():
            return False
        with open(path) as f:
            state = json.load(f)
        self.alpha = {int(k): v for k, v in state["alpha"].items()}
        self.beta = {int(k): v for k, v in state["beta"].items()}
        return True


if __name__ == "__main__":
    bandit = RegimeBandit()
    # Test predict from features
    features = {"log_ret": -0.001, "vol_z": -0.5, "abs_funding": 0.0001, "oi_chg": 0.0}
    regime = bandit.predict_regime(features)
    print(f"Predicted regime: {regime} ({bandit.regime_names[regime]})")
    bet_size = bandit.get_bet_size(base_risk=500, regime=regime)
    print(f"Recommended bet: ${bet_size:.2f}")
    print(f"Stats: {bandit.get_stats()}")
