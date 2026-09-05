//+------------------------------------------------------------------+
//| RTS_Manager.mq5                                                  |
//| v0.1 — Adaptive Position Management Engine                       |
//| Basket management, ATR-based scaling, dynamic exit               |
//+------------------------------------------------------------------+
#property strict
#property description "RTS Manager — Adaptive Position Management Engine"
#property description "Basket management, ATR scaling, dynamic exit"

input string  RTS_ID = "RTS_XAU_01";       // Instance ID
input double  BaseLot = 0.01;               // Initial lot size
input int     MaxLevels = 3;                // Max adds (1 initial + 2-3 adds)
input double  MaxRiskPct = 2.0;             // Max total risk % of balance
input double  BasketTargetPct = 0.5;        // Basket TP % above average
input double  PartialClosePct = 0.3;        // % to close at first target
input int     TimerSeconds = 15;             // Heartbeat interval

// --- State ---
double avgEntry = 0;
double totalSize = 0;
int levelCount = 0;
double addDistanceATR = 0.5;  // ATR multiplier for add levels

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("RTS Manager v0.1 started | ", RTS_ID);
   Print("Base lot: ", BaseLot, " MaxLevels: ", MaxLevels);
   EventSetTimer(TimerSeconds);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("RTS Manager stopped");
}

//+------------------------------------------------------------------+
//| OnTimer — main loop                                              |
//+------------------------------------------------------------------+
void OnTimer()
{
   ScanBasket();
   EvaluateAdd();
   EvaluateExit();
   LogState();
}

//+------------------------------------------------------------------+
//| Basket Scan — read all positions, calculate averages            |
//+------------------------------------------------------------------+
void ScanBasket()
{
   int total = PositionsTotal();
   if(total == 0)
   {
      avgEntry = 0;
      totalSize = 0;
      levelCount = 0;
      return;
   }

   double sumValue = 0;
   double sumSize = 0;
   int count = 0;

   for(int i = 0; i < total; i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      if(sym != "XAUUSD") continue;  // Only manage XAUUSD basket

      double price = PositionGetDouble(POSITION_PRICE_OPEN);
      double size  = PositionGetDouble(POSITION_VOLUME);
      sumValue += price * size;
      sumSize += size;
      count++;
   }

   if(sumSize > 0)
   {
      avgEntry = sumValue / sumSize;
      totalSize = sumSize;
      levelCount = count;
   }
}

//+------------------------------------------------------------------+
//| Evaluate Add — check if new add level triggered                 |
//+------------------------------------------------------------------+
void EvaluateAdd()
{
   if(levelCount <= 0) return;
   if(levelCount >= MaxLevels)
   {
      Print("RTS: Max levels reached (", levelCount, "/", MaxLevels, ")");
      return;
   }

   double currentPrice = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetString(POSITION_SYMBOL) == "XAUUSD")
      {
         currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
         break;
      }
   }
   if(currentPrice == 0) return;

   // ATR check (approximate from account volatility)
   double atr = GetATR();
   double addDistance = atr * addDistanceATR;
   double drawdownPct = 0;

   // Check if price moved far enough for next add
   int type = GetBasketDirection();
   if(type == -1) return;  // Unknown direction

   bool priceForAdd = false;
   double accountBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double totalRisk = 0;

   if(type == POSITION_TYPE_BUY)
   {
      double drop = (avgEntry - currentPrice);
      priceForAdd = (drop >= addDistance * levelCount);
      totalRisk = drop * totalSize / accountBalance * 100;
   }
   else
   {
      double rise = (currentPrice - avgEntry);
      priceForAdd = (rise >= addDistance * levelCount);
      totalRisk = rise * totalSize / accountBalance * 100;
   }

   if(!priceForAdd) return;
   if(totalRisk > MaxRiskPct)
   {
      Print("RTS: Max risk reached (", DoubleToString(totalRisk, 1), "%), no add");
      return;
   }

   // Calculate add size (gentle increase, no doubling)
   double addSize = BaseLot * (1.0 + (levelCount - 1) * 0.3);
   if(totalSize + addSize > BaseLot * MaxLevels * 1.5)
   {
      Print("RTS: Max basket size limit");
      return;
   }

   Print("RTS ADD LEVEL ", levelCount + 1, " size=", addSize,
         " avg=", DoubleToString(avgEntry, 1));
   levelCount++;
}

//+------------------------------------------------------------------+
//| Evaluate Exit — check basket TP and partial close conditions    |
//+------------------------------------------------------------------+
void EvaluateExit()
{
   if(levelCount <= 0 || avgEntry == 0) return;

   double currentPrice = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetString(POSITION_SYMBOL) == "XAUUSD")
      {
         currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
         break;
      }
   }
   if(currentPrice == 0) return;

   int direction = GetBasketDirection();
   double basketPnl = 0;

   if(direction == POSITION_TYPE_BUY)
   {
      basketPnl = (currentPrice - avgEntry) / avgEntry * 100;
   }
   else
   {
      basketPnl = (avgEntry - currentPrice) / avgEntry * 100;
   }

   // Basket TP reached?
   if(basketPnl >= BasketTargetPct)
   {
      Print("RTS: BASKET TP HIT at ", DoubleToString(basketPnl, 2), "% ",
            "avg=", DoubleToString(avgEntry, 1), " current=", DoubleToString(currentPrice, 1));

      // Partial close
      double closeSize = totalSize * PartialClosePct;
      if(closeSize > 0)
      {
         Print("RTS: PARTIAL CLOSE ", closeSize, " lot (", PartialClosePct * 100, "%)");
      }
   }

   // Risk limit check
   if(basketPnl < -MaxRiskPct)
   {
      Print("RTS: EMERGENCY — Max drawdown ", DoubleToString(basketPnl, 2), "%");
   }
}

//+------------------------------------------------------------------+
//| Get Basket Direction — POSITION_TYPE_BUY or SELL                |
//+------------------------------------------------------------------+
int GetBasketDirection()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetString(POSITION_SYMBOL) == "XAUUSD")
         return (int)PositionGetInteger(POSITION_TYPE);
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Get ATR — approximate from recent volatility                    |
//+------------------------------------------------------------------+
double GetATR()
{
   // Simplified ATR: average of last N candles
   int handle = iATR("XAUUSD", PERIOD_M5, 14);
   if(handle == INVALID_HANDLE) return 10.0;  // Default fallback

   double atrBuffer[1];
   if(CopyBuffer(handle, 0, 0, 1, atrBuffer) < 1) return 10.0;
   return atrBuffer[0];
}

//+------------------------------------------------------------------+
//| Log State — journal current basket state                        |
//+------------------------------------------------------------------+
void LogState()
{
   if(levelCount == 0) return;

   double currentPrice = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetString(POSITION_SYMBOL) == "XAUUSD")
      {
         currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
         break;
      }
   }

   Print("RTS STATE: levels=", levelCount,
         " avg=", DoubleToString(avgEntry, 1),
         " size=", DoubleToString(totalSize, 2),
         " current=", DoubleToString(currentPrice, 1));
}
//+------------------------------------------------------------------+
