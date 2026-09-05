//+------------------------------------------------------------------+
//|                                          TradingOS v1.0.0         |
//|                  Market Truth Alignment Layer (MTAL)              |
//+------------------------------------------------------------------+
#property copyright "TradingOS"
#property version   "1.0.0"
#property strict

//--- Core
#include "Core/Types.mqh"
#include "Core/EventBus.mqh"
#include "Core/StateStore.mqh"
#include "Core/IdempotencyGuard.mqh"

//--- Persistence
#include "Persistence/FileEventStore.mqh"

//--- Engine
#include "Engine/DecisionEngine.mqh"
#include "Engine/RiskSupervisor.mqh"

//--- Execution
#include "Execution/ExecutionEngine.mqh"
#include "Execution/BrokerSync.mqh"

//--- Recovery
#include "Recovery/RecoveryEngine.mqh"

//--- Calibration (v0.5)
#include "Calibration/DriftEstimator.mqh"
#include "Calibration/ModelErrorAnalyzer.mqh"
#include "Calibration/ExecutionCalibrationEngine.mqh"
#include "Calibration/BrokerFingerprintEngine.mqh"

//--- State Machines
#include "StateMachines/PositionFSM.mqh"

// ================= GLOBALS =================

//--- Core components
FileEventStore   EventLog("events.bin");
StateStore       State;
Event            EventsBuffer[];

//--- Engines
DecisionEngine   Decision;
RiskSupervisor   Risk;
ExecutionEngine  Execution;
BrokerSync       Sync;
RecoveryEngine   Recovery;

//--- Calibration (v0.5)
DriftEstimator         Drift;
ModelErrorAnalyzer     ErrorAnalyzer;
ExecutionCalibrationEngine CalibrationEngine;

//--- Shadow Mode (v1.0)
bool   ShadowMode = true;  // DEFAULT: SHADOW MODE ON
string ShadowLog  = "shadow_ledger.csv";

//--- Integrity Layer
string LastHash = "0000000000000000000000000000000000000000000000000000000000000000";

//--- Timing
datetime last_bar = 0;
int      tick_count = 0;
int      intent_count = 0;

// ================= INIT =================

int OnInit()
{
   Print("========================================");
   Print("  TradingOS v1.0.0 STARTING");
   Print("  Mode: ", ShadowMode ? "SHADOW" : "LIVE");
   Print("========================================");

   EventLog.Open();

   //--- CRITICAL: FULL RECOVERY FLOW
   Recovery.Recover(EventLog, State);

   //--- CRITICAL: ALIGN WITH BROKER
   Sync.Sync(State);

   //--- Validate state
   if(State.last_event_id > 0)
   {
      Print("[INIT] State restored from event #", State.last_event_id);
      Print("[INIT] Equity: $", DoubleToString(State.portfolio.equity, 2));
      Print("[INIT] Positions: ", State.execution.open_positions);
   }
   else
   {
      Print("[INIT] Fresh start");
      State.portfolio.equity = AccountInfoDouble(ACCOUNT_EQUITY);
      State.portfolio.peak_equity = State.portfolio.equity;
      State.portfolio.balance = AccountInfoDouble(ACCOUNT_BALANCE);
   }

   //--- Initialize shadow log
   if(ShadowMode)
   {
      InitializeShadowLog();
   }

   Print("========================================");
   Print("  TradingOS v1.0.0 READY");
   Print("  Shadow Mode: ", ShadowMode ? "ACTIVE" : "INACTIVE");
   Print("========================================");

   return INIT_SUCCEEDED;
}

// ================= MAIN LOOP =================

void OnTick()
{
   tick_count++;

   //--- 1. CREATE MARKET EVENT
   Event e;

   e.id        = GetTickCount();
   e.sequence  = tick_count;
   e.symbol    = _Symbol;
   e.type      = EVT_TICK;
   e.timestamp = TimeCurrent();
   e.payload   = DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_BID));

   //--- 2. PERSIST EVENT (SOURCE OF TRUTH)
   EventLog.Append(e);

   //--- 3. APPLY STATE
   State.Apply(e);

   //--- 4. UPDATE MARKET DATA
   State.market.price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   State.market.bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   State.market.ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   State.market.spread = (State.market.ask - State.market.bid) / _Point;

   //--- 5. UPDATE PORTFOLIO FROM BROKER
   State.portfolio.equity = AccountInfoDouble(ACCOUNT_EQUITY);
   State.portfolio.balance = AccountInfoDouble(ACCOUNT_BALANCE);
   State.portfolio.margin = AccountInfoDouble(ACCOUNT_MARGIN);
   State.UpdateDrawdown();

   //--- 6. NEW BAR = DECISION TIME
   datetime current_bar = iTime(_Symbol, PERIOD_M5, 0);
   if(current_bar != last_bar)
   {
      last_bar = current_bar;

      //--- 6a. DECISION (INTENT ONLY)
      Intent intent = Decision.Create(_Symbol, State.market);

      //--- 6b. RISK CHECK
      if(!Risk.Allow(intent, State.portfolio))
      {
         Print("[RISK] Intent blocked");
         return;
      }

      //--- 6c. EXECUTION
      if(ShadowMode)
      {
         //--- SHADOW MODE: Simulate only, no real orders
         ExecuteShadow(intent);
      }
      else
      {
         //--- LIVE MODE: Execute with quality gate
         Execution.Execute(intent, State, EventLog, EventLog);
      }
   }

   //--- 7. BROKER RECONCILIATION (EVERY 10 TICKS)
   if(tick_count % 10 == 0)
      Sync.Sync(State);

   //--- 8. INTEGRITY CHECK (EVERY 100 TICKS)
   if(tick_count % 100 == 0)
      VerifyIntegrity();
}

// ================= SHADOW EXECUTION =================

void ExecuteShadow(Intent &intent)
{
   intent_count++;

   //--- Calculate simulated execution metrics
   double spread = State.market.spread;
   double volatility = State.market.atr;
   double slippage = CalculateSlippage(spread, volatility, intent.risk_budget);
   double impact = CalculateImpact(intent.risk_budget, 100.0, 1.0);
   double fill_prob = CalculateFillProbability(spread, volatility, 1.0);
   double eqs = CalculateEQS(slippage, fill_prob, impact);

   //--- Determine if shadow fill would occur
   bool filled = (eqs >= 40.0 && fill_prob > 0.5);

   //--- Log to shadow ledger
   LogShadowRecord(intent, slippage, impact, fill_prob, eqs, filled);

   //--- Print summary
   Print("[SHADOW] Intent #", intent_count,
         " | ", intent.direction,
         " | EQS=", DoubleToString(eqs, 2),
         " | Fill=", DoubleToString(fill_prob * 100, 1), "%",
         " | ", filled ? "SIMULATED" : "REJECTED");
}

// ================= SHADOW HELPERS =================

double CalculateSlippage(double spread, double volume, double risk)
{
   double base_slip = spread * 0.5;
   double vol_factor = volatility * 0.3;
   double size_factor = MathLog(1 + volume);
   return base_slip + vol_factor + size_factor;
}

double CalculateImpact(double volume, double avg_volume, double liquidity)
{
   if(liquidity <= 0) return 1.0;
   double participation = volume / avg_volume;
   return MathPow(participation, 2.0) * 0.1;
}

double CalculateFillProbability(double spread, double volatility, double liquidity)
{
   double p = 1.0;
   p -= spread * 0.1;
   p -= volatility * 0.05;
   if(liquidity < 0.5) p -= 0.3;
   return MathMax(0.0, MathMin(1.0, p));
}

double CalculateEQS(double slippage, double fill_prob, double impact)
{
   double s = (1.0 - slippage) * 0.4 + fill_prob * 0.4 + (1.0 - impact) * 0.2;
   return s * 100.0;
}

// ================= SHADOW LOGGING =================

void InitializeShadowLog()
{
   int handle = FileOpen(ShadowLog, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle,
         "timestamp", "symbol", "intent_id", "direction", "strength",
         "risk_budget", "spread", "atr", "adx",
         "slippage", "impact", "fill_prob", "eqs", "filled"
      );
      FileClose(handle);
   }
}

void LogShadowRecord(Intent &intent, double slippage, double impact,
                     double fill_prob, double eqs, bool filled)
{
   if(!ShadowMode) return;

   int handle = FileOpen(ShadowLog, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle != INVALID_HANDLE)
   {
      FileSeek(handle, 0, SEEK_END);
      FileWrite(handle,
         TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
         _Symbol,
         intent.id,
         intent.direction,
         DoubleToString(intent.confidence, 4),
         DoubleToString(intent.risk_budget, 4),
         DoubleToString(State.market.spread, 2),
         DoubleToString(State.market.atr, 4),
         DoubleToString(State.market.adx, 2),
         DoubleToString(slippage, 5),
         DoubleToString(impact, 5),
         DoubleToString(fill_prob, 4),
         DoubleToString(eqs, 2),
         filled ? "1" : "0"
      );
      FileClose(handle);
   }
}

// ================= INTEGRITY LAYER =================

void VerifyIntegrity()
{
   //--- Check portfolio consistency
   double calc_equity = State.portfolio.balance + State.portfolio.floating_pnl;
   if(MathAbs(State.portfolio.equity - calc_equity) > 0.0001)
   {
      Print("[CRITICAL] Portfolio equity mismatch!");
      Print("  Expected: ", calc_equity);
      Print("  Actual: ", State.portfolio.equity);
      //--- In production: HALT and snapshot
   }

   //--- Check position integrity
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(PositionSelectByTicket(PositionGetTicket(i)))
      {
         if(PositionGetDouble(POSITION_VOLUME) <= 0)
         {
            Print("[CRITICAL] Invalid position volume detected!");
         }
      }
   }

   //--- Update hash chain
   UpdateHashChain();
}

void UpdateHashChain()
{
   //--- Simple hash: previous hash + current state
   string state_str = DoubleToString(State.portfolio.equity, 2) +
                      DoubleToString(State.portfolio.balance, 2) +
                      IntegerToString(State.execution.total_orders);

   LastHash = CalculateSHA256(LastHash + state_str);
}

string CalculateSHA256(string input)
{
   //--- Placeholder for actual SHA256
   //--- In production, use Windows crypto API or built-in hash
   uchar data[];
   StringToCharArray(input, data);
   //--- Simple hash for demo
   int hash = 0;
   for(int i = 0; i < ArraySize(data); i++)
      hash = hash * 31 + data[i];
   return IntegerToString(MathAbs(hash), 16);
}

// ================= CALIBRATION =================

void UpdateCalibration(double simulated_slippage, double real_slippage)
{
   Drift.RecordSimulated(simulated_slippage);
   Drift.RecordReal(real_slippage);
   ErrorAnalyzer.RecordError(simulated_slippage, real_slippage);
}

// ================= SHUTDOWN =================

void OnDeinit(const int reason)
{
   Print("SHUTDOWN: saving snapshot");
   Print("  Total ticks: ", tick_count);
   Print("  Total intents: ", intent_count);
   Print("  Shadow Mode: ", ShadowMode ? "ACTIVE" : "INACTIVE");
   EventLog.Close();
}

// ================= TIMER =================

void OnTimer()
{
   Print("[TIMER] Periodic reconciliation");
   Sync.Sync(State);
}

// ================= TRADE EVENT =================

void OnTrade()
{
   Print("[TRADE] Trade event detected");
   Sync.Sync(State);
}
//+------------------------------------------------------------------+
