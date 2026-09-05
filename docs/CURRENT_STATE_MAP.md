# Current State Map

| Component | Current Location | Role | Status | Target Domain | Migration Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ubot_bingx | `/opt/ubot_bingx` | Entry + Risk + Exec | **LIVE LEGACY** | Adapter | Move logic to Core, keep only API calls |
| PIE Observer | `/root/tradingos/scripts/pie_live_observer.py` | Position Analysis | **TARGET** | Position (PIE) | Transition from observer to active manager |
| Auto Safe | `/root/tradingos/control/auto_safe.py` | Risk Mitigation | **LIVE** | Risk / Execution | Formalize into Risk Action Layer |
| Telegram Bot | `/root/tradingos/telegram_control/` | Command UI | **LIVE** | External (UI) | Strip business logic, leave only API calls |
| SQLite DBs | `/root/tradingos/*.db` | Persistence | **PLATFORM** | Storage | Consolidate fragmented DBs into one Data Layer |
| Market Data | `/root/tradingos/observe_crypto.py` | Data Collection | **LIVE** | Market | Standardize data contracts |
| Research Tools| `/root/tradingos/research/` | Edge Discovery | **TARGET** | Research | Formalize Feature Engineering pipeline |
