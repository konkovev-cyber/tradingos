# TradingOS Architecture

## 1. Core Philosophy
TradingOS is not a standalone bot, but a **Control Plane** for capital and positions. The system's primary goal is to transform a collection of trading bots into a unified organism where intelligence is centralized and execution is delegated to adapters.

## 2. The Three-Layer Architecture

### Level 1: CORE (The Money Maker)
The central intelligence loop. Decisions here directly impact PnL.
- **Market**: Normalization of ticks, orderbooks, and candles.
- **Research**: Feature engineering and edge validation.
- **Strategy**: Generation of trade candidates with confidence scores.
- **Signal**: Real-time candidate detection.
- **Decision**: Final BUY/SELL/HOLD verdict.
- **Risk**: Safety gates and exposure limits.
- **Capital**: Position sizing and budget allocation.
- **Portfolio**: Global asset correlation and exposure management.
- **Position (PIE)**: Lifecycle management of open trades (Health, MFE/MAE, Trail).
- **Execution**: Order routing and delivery confirmation.

### Level 2: PLATFORM (The Infrastructure)
Support systems that ensure reliability but do not make trading decisions.
- **Event Bus**: Unified event stream.
- **Storage**: Single source of truth for data and state.
- **Configuration**: Centralized runtime parameters.
- **Telemetry**: Health monitoring and alerting.
- **Scheduler**: Task orchestration.
- **Simulation**: "What-if" scenario analysis.

### Level 3: EXTERNAL (The Interfaces)
I/O layers that communicate with the outside world.
- **Exchange Adapters**: Pure translation layer (TradingOS $\leftrightarrow$ API).
- **Telegram**: UI/Notifications.
- **Dashboard**: Visual monitoring.
- **API/CLI**: Programmatic and manual control.

## 3. The Facade Law
Inter-domain communication is strictly forbidden except through public interfaces (Facades). No domain may access the internal state or private methods of another.

## 4. Execution Logic
`Decision` $\to$ `Risk` $\to$ `Capital` $\to$ `Execution` $\to$ `Adapter` $\to$ `Exchange`.
