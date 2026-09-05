//+------------------------------------------------------------------+
//| TradingOS_Guardian.mq5                                           |
//| v0.2 — Phase 2.1: Risk Monitor + Protection State               |
//+------------------------------------------------------------------+
#property strict
#property description "TradingOS Guardian — MT5 Runtime Agent + Risk Monitor"
#property description "Heartbeat, Account, Positions, Protection State, JSON"

input string  TradingOS_ID = "TOS_MT5_01";
input int     TimerSeconds = 10;
input double  MaxRiskPct   = 2.0;   // max risk % of balance per position

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("TradingOS Guardian v0.2 started");
   Print("ID: ", TradingOS_ID, " Timer: ", TimerSeconds, "s");
   Print("Terminal: ", TerminalInfoString(TERMINAL_NAME));
   Print("Account: ", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)),
         " Server: ", AccountInfoString(ACCOUNT_SERVER));
   EventSetTimer(TimerSeconds);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("TradingOS Guardian stopped (reason: ", reason, ")");
}

//+------------------------------------------------------------------+
//| OnTimer — main loop                                              |
//+------------------------------------------------------------------+
void OnTimer()
{
   SendHeartbeat();
   CollectAccount();
   CollectPositions();
   ValidateProtection();
   CalculateRisk();
   ExportState();
}

//+------------------------------------------------------------------+
//| Heartbeat                                                        |
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   Print("TradingOS HEARTBEAT:");
   Print("  terminal=", TerminalInfoString(TERMINAL_NAME),
         " connected=", TerminalInfoInteger(TERMINAL_CONNECTED));
   Print("  account=", AccountInfoInteger(ACCOUNT_LOGIN),
         " server=", AccountInfoString(ACCOUNT_SERVER));
   Print("  time=", TimeCurrent());
}

//+------------------------------------------------------------------+
//| Account Snapshot                                                 |
//+------------------------------------------------------------------+
void CollectAccount()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN);
   double free    = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double dd      = (balance > 0) ? (balance - equity) / balance * 100 : 0;

   Print("TradingOS ACCOUNT:");
   Print("  balance=", DoubleToString(balance, 2));
   Print("  equity=", DoubleToString(equity, 2));
   Print("  margin=", DoubleToString(margin, 2));
   Print("  free_margin=", DoubleToString(free, 2));

   if(dd > 5.0)
      Print("  🔴 DRAWDOWN: ", DoubleToString(dd, 2), "% (CRITICAL)");
   else if(dd > 3.0)
      Print("  ⚠️ DRAWDOWN: ", DoubleToString(dd, 2), "% (WARNING)");
}

//+------------------------------------------------------------------+
//| Position Snapshot                                                |
//+------------------------------------------------------------------+
void CollectPositions()
{
   int total = PositionsTotal();
   if(total == 0) { Print("TradingOS POSITIONS: none"); return; }

   Print("TradingOS POSITIONS: ", total);
   for(int i = 0; i < total; i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      Print("  POSITION #", PositionGetInteger(POSITION_TICKET));
      Print("    symbol=",    PositionGetString(POSITION_SYMBOL));
      Print("    side=",      (int)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL");
      Print("    volume=",    DoubleToString(PositionGetDouble(POSITION_VOLUME), 2));
      Print("    entry=",     DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 4));
      Print("    current=",   DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 4));
      Print("    sl=",        DoubleToString(PositionGetDouble(POSITION_SL), 4));
      Print("    tp=",        DoubleToString(PositionGetDouble(POSITION_TP), 4));
      Print("    profit=",    DoubleToString(PositionGetDouble(POSITION_PROFIT), 2));
   }
}

//+------------------------------------------------------------------+
//| Protection Validation v2 — with state classification            |
//+------------------------------------------------------------------+
void ValidateProtection()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      double sl     = PositionGetDouble(POSITION_SL);
      double tp     = PositionGetDouble(POSITION_TP);
      double profit = PositionGetDouble(POSITION_PROFIT);
      long ticket   = PositionGetInteger(POSITION_TICKET);

      // --- CLASSIFY PROTECTION STATE ---
      string state;
      string risk_level;

      if(sl == 0 && tp == 0)
      {
         state = "CRITICAL";
         risk_level = "NO_SL_NO_TP";
         Print("  🔴 PROTECTION STATE on ", symbol, " [", state, "] — ", risk_level);
      }
      else if(sl == 0)
      {
         state = "CRITICAL";
         risk_level = "NO_SL";
         Print("  🔴 PROTECTION STATE on ", symbol, " [", state, "] — ", risk_level);
      }
      else if(tp == 0)
      {
         state = "WARNING";
         risk_level = "NO_TP";
         Print("  🟡 PROTECTION STATE on ", symbol, " [", state, "] — ", risk_level);
      }
      else
      {
         state = "NORMAL";
         risk_level = "PROTECTED";
         Print("  🟢 PROTECTION STATE on ", symbol, " [", state, "] — ", risk_level);
      }
   }

   if(total == 0)
      Print("  ⚪ PROTECTION STATE: NO POSITIONS");
}

//+------------------------------------------------------------------+
//| Risk Calculation — SL distance, risk %, exposure                |
//+------------------------------------------------------------------+
void CalculateRisk()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   int total = PositionsTotal();

   for(int i = 0; i < total; i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
      double current= PositionGetDouble(POSITION_PRICE_CURRENT);
      double sl     = PositionGetDouble(POSITION_SL);
      double tp     = PositionGetDouble(POSITION_TP);
      double volume = PositionGetDouble(POSITION_VOLUME);
      double profit = PositionGetDouble(POSITION_PROFIT);
      int type      = (int)PositionGetInteger(POSITION_TYPE);
      long ticket   = PositionGetInteger(POSITION_TICKET);

      // SL distance in price and %
      double sl_dist_price = (type == POSITION_TYPE_BUY && sl > 0) ? entry - sl :
                             (type == POSITION_TYPE_SELL && sl > 0) ? sl - entry : 0;
      double sl_dist_pct = (entry > 0) ? sl_dist_price / entry * 100 : 0;

      // TP distance
      double tp_dist_price = (type == POSITION_TYPE_BUY && tp > 0) ? tp - entry :
                             (type == POSITION_TYPE_SELL && tp > 0) ? entry - tp : 0;
      double tp_dist_pct = (entry > 0) ? tp_dist_price / entry * 100 : 0;

      // Risk as % of balance (approximate)
      double risk_value = sl_dist_price * volume;
      double risk_pct = (balance > 0) ? risk_value / balance * 100 : 0;

      // Current PnL as % of balance
      double pnl_pct = (balance > 0) ? profit / balance * 100 : 0;

      Print("  RISK #", ticket, " ", symbol, ":");
      Print("    sl_dist=", DoubleToString(sl_dist_pct, 2), "%  tp_dist=", DoubleToString(tp_dist_pct, 2), "%");
      Print("    risk=", DoubleToString(risk_pct, 2), "%  pnl=", DoubleToString(pnl_pct, 2), "%");

      // Risk alerts
      if(sl > 0 && sl_dist_pct < 0.1)
         Print("  ⚠️ SL TOO TIGHT on ", symbol, ": ", DoubleToString(sl_dist_pct, 2), "%");
      if(risk_pct > MaxRiskPct)
         Print("  🔴 RISK EXCEEDS LIMIT on ", symbol, ": ", DoubleToString(risk_pct, 2), "% > ", DoubleToString(MaxRiskPct, 1), "%");
   }
}

//+------------------------------------------------------------------+
//| Export JSON snapshot to disk                                     |
//+------------------------------------------------------------------+
void ExportState()
{
   int total = PositionsTotal();
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN);

   // Build JSON manually (no external libs)
   string json = "{";
   json += "\"time\":\"" + TimeToString(TimeCurrent()) + "\",";
   json += "\"account\":" + DoubleToString(balance, 2) + ",";
   json += "\"equity\":" + DoubleToString(equity, 2) + ",";
   json += "\"margin\":" + DoubleToString(margin, 2) + ",";
   json += "\"positions\":[";

   for(int i = 0; i < total; i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;

      if(i > 0) json += ",";
      json += "{";
      json += "\"ticket\":" + IntegerToString(PositionGetInteger(POSITION_TICKET)) + ",";
      json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
      json += "\"type\":\"" + ((int)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
      json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
      json += "\"entry\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 4) + ",";
      json += "\"current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 4) + ",";
      json += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 4) + ",";
      json += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 4) + ",";
      json += "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2);
      json += "}";
   }

   json += "]}";

   // Write to file in Files folder
   int handle = FileOpen("guardian_state.json", FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle, json);
      FileClose(handle);
   }
}
//+------------------------------------------------------------------+
