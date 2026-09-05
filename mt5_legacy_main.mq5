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

