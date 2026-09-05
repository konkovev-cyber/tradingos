# Strategy Ranking

## Scoring

| Criteria | Weight | Description |
|----------|--------|-------------|
| Simplicity | 0-5 | Can be tested tomorrow? |
| Data Availability | 0-5 | All data already in Data Lake? |
| Independence | 0-5 | Doesn't depend on other strategies? |
| Potential | 0-5 | Historical evidence of edge? |
| Risk | 0-5 (inverted) | Low risk = high score |

**Score = Simple + Data + Independent + Potential - Risk**

## Ranking Table

| Rank | ID | Class | Simple | Data | Indep | Potent | Risk | **Score** |
|------|----|-------|--------|------|-------|--------|------|-----------|
| 1 | FILTER-001 | Risk | 5 | 5 | 5 | 5 | 1 | **19** |
| 2 | MR-001 | MeanRev | 4 | 4 | 4 | 4 | 2 | **14** |
| 3 | VOL-001 | Breakout | 4 | 4 | 4 | 4 | 2 | **14** |
| 4 | SESSION-001 | Session | 3 | 3 | 4 | 4 | 2 | **12** |
| 5 | TF-001 | Trend | 4 | 4 | 4 | 3 | 3 | **12** |
| 6 | LS-001 | Sweep | 3 | 4 | 4 | 3 | 3 | **11** |
| 7 | SESSION-002 | Session | 3 | 3 | 4 | 3 | 2 | **11** |
| 8 | MR-002 | MeanRev | 4 | 4 | 3 | 3 | 3 | **11** |
| 9 | VOL-002 | Breakout | 4 | 4 | 3 | 3 | 3 | **11** |
| 10 | FILTER-002 | Risk | 4 | 4 | 4 | 3 | 4 | **11** |
| 11 | TF-002 | Trend | 3 | 4 | 4 | 3 | 3 | **11** |
| 12 | GRID-001 | Grid | 2 | 3 | 3 | 3 | 3 | **8** |
| 13 | MR-003 | MeanRev | 3 | 3 | 3 | 2 | 3 | **8** |

## Top 5 Candidates

| Rank | ID | Why |
|------|----|-----|
| 1 | FILTER-001 | Zero implementation cost, protects all strategies |
| 2 | MR-001 | Strongest academic backing, simple logic |
| 3 | VOL-001 | Complements MR-001 (different regimes) |
| 4 | SESSION-001 | Gold-specific, high potential |
| 5 | TF-001 | Covers trend regime (MR-001 blind spot) |
