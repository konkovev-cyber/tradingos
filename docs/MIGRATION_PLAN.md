# Migration Plan (Strangler Pattern)

## General Rule
No LIVE code is touched until a TARGET replacement is verified. 
Verification = Identical output/action on live data.

## Phase Sequence (By Responsibility)

### Phase 1: Data & Infrastructure (Low Risk)
- [ ] **Data Contracts**: Define unified schemas for positions and trades.
- [ ] **Event System**: Implement basic internal eventing.
- [ ] **Market Data**: Centralize the data stream.

### Phase 2: The Control Plane (High Value - The "Money Saver")
- [ ] **Execution Adapter**: Create a clean wrapper for BingX (OPEN/CLOSE/MODIFY).
- [ ] **Position Manager (PIE)**: Move position analysis and Health Score to TradingOS.
- [ ] **Risk Action Layer**: Implement `MOVE_SL` and `CLOSE` based on PIE/Risk rules.

### Phase 3: Capital & Risk (The Guardrails)
- [ ] **Capital Engine**: Move position sizing and budget logic.
- [ ] **Risk Engine**: Centralize exposure limits and safety gates.

### Phase 4: The Brain (The Entry)
- [ ] **Decision Engine**: Implement the final verdict logic.
- [ ] **Signal/Strategy**: Migrate entry logic from legacy bots.

### Phase 5: Final Cut-over
- [ ] Decommission legacy logic in `ubot_bingx`.
- [ ] `ubot_bingx` becomes a pure `BingX Adapter`.
