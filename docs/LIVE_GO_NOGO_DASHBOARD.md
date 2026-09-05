# Live GO/NO-GO Dashboard

## Формат: 10 секунд на решение

```text
╔══════════════════════════════════════════════════════════════╗
║                  TRADINGOS LIVE GO/NO-GO                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  PHASE 0 VALIDATION:                                        ║
║    R1  Replay determinism      [  ]                         ║
║    R2  No state leakage        [  ]                         ║
║    R3  Feature stability       [  ]                         ║
║    R4  Signal consistency      [  ]                         ║
║    R5  No signal explosion     [  ]                         ║
║    R6  Policy determinism      [  ]                         ║
║    R7  No oscillation          [  ]                         ║
║    R8  Execution consistency   [  ]                         ║
║    R9  SL/TP correctness       [  ]                         ║
║    R10 Governor stability      [  ]                         ║
║    R11 No false RED            [  ]                         ║
║    R12 Diversification intact  [  ]                         ║
║    R13 Allocation consistency  [  ]                         ║
║    R14 No weight explosion     [  ]                         ║
║    R15 Learning stability      [  ]                         ║
║    R16 Freeze mechanism works  [  ]                         ║
║    R17 Recovery correctness    [  ]                         ║
║    R18 Circuit breaker valid   [  ]                         ║
║                                                              ║
║  ──────────────────────────────────────────────────────────  ║
║                                                              ║
║  RESULT:                                                     ║
║    [ ] GO      — Phase 0 PASS, proceed to MICRO LIVE        ║
║    [ ] NO-GO   — Phase 0 FAIL, stop and fix                 ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

## Как использовать

1. Прогон Phase 0 (replay + all checks)
2. Заполнить все 18 чекбоксов
3. Если все PASS → GO
4. Если хотя бы один FAIL → NO-GO

## Правило

> **18/18 PASS = GO. <18 = NO-GO. Без исключений.**
