# DESIGN.md — TradingOS Two-Contour Execution Classifier

## 1. Objective

Every signal the scanner produces must carry an explicit execution contour: MARKET (immediate), LIMIT (wait for pullback), or NO TRADE (invalid). The classifier replaces the current implicit priority chain (tp_unreachable > gates > WAIT_LIMIT > ALLOW) with a transparent, auditable decision that maps setup anatomy to execution style. Quality bar: a reviewer can read the classifier output and explain *why* this signal is market vs limit without reading code.

## 2. Product Context

- **What the product does:** A manual trading console (Telegram + Bybit) that scans crypto/stock perpetuals, scores setups 0–100, and presents the owner with executable cards. The owner decides position size and leverage; the system decides *how* to enter.
- **Who it's for:** One discretionary trader (the owner) who wants structure around entry discipline — no more "market order at top of range" or "limit order that fills instantly as market."
- **Adjacent brands (feel like these):** 
  1. **Linear** — precise, developer-facing, decisions surfaced not hidden
  2. **Ramp** — financial product, opinionated defaults, audit trail
  3. **Bloomberg Terminal** — dense but structured, every number has a lineage
- **Distant brand (do not feel like this):** **Robinhood** — gamified, hides mechanics, encourages impulse over process
- **Cultural register:** technical, sober, process-first. "Boring is correct."

## 3. Visual Foundations

*This is a backend classifier — no visual layer. The "visual" equivalent is the signal card taxonomy presented to the owner.*

### 3a. Color (Signal Card Semantics)

- **Neutral scale:** `--n-50: #F8FAFC, --n-100: #F1F5F9, --n-200: #E2E8F0, --n-400: #94A3B8, --n-600: #475569, --n-800: #1E293B, --n-950: #0F172A`
- **Accent(s):** `--accent-market: #059669` (emerald), `--accent-limit: #D97706` (amber), `--accent-skip: #DC2626` (red)
- **Semantic:** `--success: #059669, --warning: #D97706, --error: #DC2626`
- **Usage rules:** Each card type gets exactly one accent color on its header badge and primary button. No gradients. No second accent on the same card.

### 3b. Typography

- **Display face:** `JetBrains Mono, 600, tracking -0.02em` (signal IDs, prices)
- **Body face:** `IBM Plex Sans, 400/500/600` (explanatory text)
- **Fallback stack:** `system-ui, -apple-system, sans-serif`
- **Type scale:** `11 / 12 / 13 / 14 / 16 / 20 / 28 / 40`
- **Weight discipline:** 400 for body, 500 for labels, 600 for values, 700 only for header badge. No 300, no 800.

### 3c. Spacing & rhythm

- **Base unit:** `4 px`
- **Spacing scale:** `4, 8, 12, 16, 24, 32, 48, 64`
- **What "generous" whitespace means:** card sections separated by 16px; intra-section lines 8px; header-to-body 12px.

### 3d. Component seeds

- **Signal Card (3 variants):** MARKET (green badge), LIMIT (amber badge), SKIP (red badge, no action button). Each variant has identical layout, only badge color and button set differ.
- **Decision Trace Panel:** collapsible section on every card showing the classifier's reasoning chain (contour → features → thresholds).
- **No icons** as decoration. Only functional markers (⚡ market, ⏳ limit, 🚫 skip).

## 4. Accessibility

- **Text contrast:** all text ≥ 4.5:1 on card backgrounds (tested in Telegram light/dark)
- **Motion:** none (static cards)
- **Focus indicators:** N/A (Telegram native)
- **Alt text policy:** N/A

## 5. Voice & Tone

- **Register:** technical, concise, evidentiary
- **Sentence rhythm:** short, declarative. One fact per line.
- **Words this brand uses:** "contour", "edge", "fill risk", "structural", "audit"
- **Words this brand refuses:** "seamless", "optimize", "smart", "AI-powered", "next-gen", "unlock"
- **Address:** "you" (the owner), imperative for actions ("Place limit", "Skip")

## 6. Implementation Practices

- **Token format:** Python constants in `tradingos/signals/contour_classifier.py` — no external config for thresholds (versioned with code)
- **Component library convention:** N/A (backend)
- **Grid system:** N/A
- **Motion rules:** N/A
- **Testing:** property-based tests on synthetic setups + regression suite on historical signals (see §9)

## 7. Anti-Patterns

- **No hidden priority chains.** The current `trade_decision` logic (tp_unreachable > gates > WAIT_LIMIT > ALLOW) is an implicit priority chain. Every decision must be an explicit contour with named features.
- **No "market by default".** MARKET is a specific contour for high-urgency/breakout setups only. If the setup doesn't meet breakout criteria, it's not MARKET.
- **No limit orders that fill instantly.** LIMIT contour requires structural pullback evidence (C1 maturity + min distance). PostOnly is enforced at exchange.
- **No contour without audit fields.** Every signal must carry `contour`, `contour_confidence`, `contour_features` (dict of raw feature values), `contour_thresholds` (dict of thresholds used).
- **No magic numbers in classifier.** All thresholds are named constants with docstrings explaining origin (empirical, theoretical, owner directive).

## 8. Decision-Making

1. **Structural honesty over fill rate.** A contour that accurately describes the setup (even if it means NO TRADE) beats one that forces a fill.
2. **Explicit over implicit.** If a new feature affects contour, add it to the feature vector and thresholds — don't bury it in a gate.
3. **Owner decides size/leverage; system decides contour.** The contour is not a suggestion — it's the execution contract.
4. **Backward compatibility via versioning.** Classifier v1, v2, etc. Old signals retain their contour; new signals use current version.
5. **Test-driven thresholds.** Thresholds change only with test evidence (historical backtest or paper forward-test), not intuition.

## 9. Workflow

1. **Define contour taxonomy** (MARKET, LIMIT, NO_TRADE) with feature vectors for each
2. **Implement `ContourClassifier` class** in `tradingos/signals/contour_classifier.py`
3. **Wire into `score_symbol`** — replace `trade_decision` logic with `classifier.classify(features)`
4. **Add contour fields to signal output** (`contour`, `contour_confidence`, `contour_features`, `contour_thresholds`)
5. **Update `_send_signal_card` / `_send_wait_limit_card`** to render contour badge + decision trace panel
6. **Write property tests** for each contour on synthetic setups
7. **Run regression on last 30 days of signals** — verify contour distribution matches owner intent
8. **Deploy behind feature flag** (`contour_classifier_enabled` in manual_session.json), observe 1 week, then promote