# Phase 6.5 — Real Account Shadow Test

**Date:** 2026-07-21
**Status:** PLANNED

## Goal

Connect real BingX account as read-only data source.
Shadow decisions run on real account state.
No order execution.

## Mode

```
Mode: SHADOW_REAL_ACCOUNT

read:        YES (positions, balance, fees)
analyze:     YES (full Control Plane pipeline)
recommend:   YES (Decision Review)
execute:     NO (BLOCKED)
```

## Architecture

```
BingX API (read-only)
    │
    ├─ GET /positions (HMAC signed)
    ├─ GET /balance (HMAC signed)
    └─ GET /trades (HMAC signed)
    │
    ↓
BingX Read Adapter (new, isolated)
    │
    ↓
Unified State → Portfolio → Decision → Review
```

## Safety Rules

- API key must have **read-only** permissions (no trade permission)
- Adapter only uses GET endpoints
- No POST/PUT/DELETE anywhere in the code
- Adapter is isolated from LIVE bot code
- All results logged as SHADOW

## 7-Day Test Plan

### Day 0: Connection Check
- Verify HMAC signing works
- Read positions
- Read balance
- Read recent trades

### Days 1-7: Shadow Collection
- Each cycle: read real state → run pipeline → log results
- Compare: what would TradingOS recommend vs what bot actually does
- Collect: reject reasons, paper outcomes, reality outcomes

## Acceptance Criteria

- [ ] BingX read adapter works with HMAC
- [ ] Real positions appear in Unified State
- [ ] Portfolio Intelligence uses real data
- [ ] KPI Collector grows samples from real account
- [ ] Decision Review shows real account status
- [ ] No order execution ever
