# E-017 v2 — CASCADE MICRO-RESPONSE (Phase 3 v2 interim)

_Дата: 2026-08-24 ~19:00 UTC. Shadow-only. Production frozen. Никаких реальных сделок._

## Цель v2 (3 вопроса)

1. Сохраняется ли эффект на 1-й минуте (15/30/60/90/120s holdings)?
2. Какая величина каскада создаёт edge?
3. Можно ли повысить NET без роста costs?

## Инструменты

- `whale_e017_v2.py` — accumulator + bucket-анализ. Идемпотентен (dedup по signature symbol|start|dir).
- Ledger: `logs/whale/e017_v2_ledger.jsonl` (append, 45 эпизодов сейчас).
- **Авто-накопление**: systemd timer `whale-e017-accum.timer` — каждый час (включён).

## Данные

- 2,120 liquidation events (сейчас, collector LIVE после фикса)
- 45 cascade эпизодов (26 LONG, 15 SHORT, 4 NC) — BTC/ETH/SOL/XRP/DOGE
- price source: BTC через tick-tape (субминутно); ETH/SOL и др. через microstructure 30s

## Результаты n=45 (signed mean, %; 13/19.5/26 bps costs)

| Holding | mean signed | CI | NET base | NET +50% | NET +100% |
|---|---|---|---|---|---|
| 15s | +0.059 | [+0.01,+0.11] | −0.071 | −0.136 | −0.201 |
| 30s | +0.117 | [+0.05,+0.19] | −0.013 | −0.078 | −0.143 |
| **60s** | **+0.154** | [+0.07,+0.24] | **+0.024** | −0.041 | −0.106 |
| 90s | +0.149 | [+0.06,+0.24] | +0.019 | −0.046 | −0.111 |
| 120s | +0.144 | [+0.05,+0.24] | +0.014 | −0.051 | −0.116 |

Единственный NET base положительный на 60s (+0.024%, CUM +1.07%). Но при +50% cost маржа исчезает.

## Bucket-анализ (КЛЮЧЕВОЙ результат)

**LONG_CASCADE small/mid** (небольшие лонг-ликвидации) → положительный NET base на 60-120s:

| Bucket | n | 60s signed | NET base |
|---|---|---|---|
| LONG small | 12 | **+0.273%** | **+0.143%** |
| LONG mid | 10 | **+0.281%** | **+0.151%** |
| LONG large | 8 | +0.086% | −0.044% |
| SHORT small | 3 | +0.128% | −0.002% |
| SHORT mid | 5 | −0.055% | −0.185% |
| SHORT large | 7 | +0.006% | −0.124% |

**Выводы:**
- **Эффект живёт на LONG-cascade (small/mid неволи), 60-120s, NET base +0.14-0.15%** — единственный bucket без tail-артефакта.
- large-каскады НЕ дают edge (не "чем больше, тем лучше").
- SHORT-каскады слабее/отрицательны.
- 15s/30s — проскальзывание убивает (латентность входа).

## Tail (top-25% notional)

15s NET: −0.13% base — **крупные каскады НЕ дают мгновенный edge** (вопрос 2: edge в small/mid, не в tail).

## Overlap

1 перекрывающаяся пара; CLEAN (non-overlap, n=44): 60s NET base +0.027% — почти то же, что полный набор (эффект не от клонирования).

## VERDICT (interim)

- **Не PASS**: n=45 слишком мал; margin base только ×2 против +50% cost.
- **Единственный живой кандидат**: LONG_CASCADE small/mid, hold 60-120s, NET base +0.14-0.15% (n=22). 
- Следующий gate: **≥200 эпизодов** (≈3-5 дней накопления при текущем темпе) → пересчёт: подтвердить **LONG small/mid 60-120s** на чистых non-overlap эпизодах с NET +50% stability.
- E-011: REJECT (закрыт, не возобновлять).

## Production

```
mode=MANUAL, kill_switch=true, immutable
auditd(0 rogue), watchdog, liquidation collector LIVE
timer whale-e017-accum: каждые 60 мин
```