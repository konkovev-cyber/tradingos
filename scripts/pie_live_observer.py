#!/usr/bin/env python3
"""
PIE Live Observer — подключает PIE к реальным позициям BingX.

Не управляет позициями. Только наблюдает и записывает события.

Запуск:
  python3 scripts/pie_live_observer.py
  python3 scripts/pie_live_observer.py --interval 60
  python3 scripts/pie_live_observer.py --once
"""

import os
import sys
import asyncio
import logging
import argparse
import time
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Add paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path("/root/mt5_trading_bot")))

from adapters.bingx_client import BingXClient

# PIE
import importlib.util
_pie_spec = importlib.util.spec_from_file_location(
    "position_intelligence", str(ROOT / "core" / "intelligence" / "position_intelligence.py")
)
_pie_mod = importlib.util.module_from_spec(_pie_spec)
_pie_spec.loader.exec_module(_pie_mod)
PIEPositionIntelligence = _pie_mod.PIEPositionIntelligence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PIELiveObserver")


class PIELiveObserver:
    """
    Live Observer — PIE наблюдает за реальными позициями BingX.
    
    Не управляет. Не закрывает. Не открывает.
    Только записывает события и рекомендации.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        db_path: Optional[Path] = None,
        interval: int = 60,
        pie_mode: str = "observe",
    ):
        self._bingx = BingXClient(api_key, api_secret)
        self._pie = PIEPositionIntelligence(
            mode=pie_mode,
            db_path=db_path or ROOT / "tradingos_data.db",
        )
        self._interval = interval
        self._cycle = 0
        self._started_at = time.time()
        
        # Track known positions to detect new/closed
        self._known_positions: Dict[str, Dict] = {}
        
        # BTC price for trend
        self._btc_price: float = 0.0
        self._btc_trend: str = "UNKNOWN"

    async def initialize(self) -> bool:
        """Проверить подключение к BingX."""
        try:
            positions = await self._bingx.get_positions()
            logger.info(f"BingX connected. Found {len(positions)} open positions")
            logger.info(f"PIE mode: {self._pie._mode}")
            
            # Get BTC price for trend
            btc_ticker = await self._bingx.get_ticker("BTCUSDT")
            if btc_ticker:
                self._btc_price = float(btc_ticker.get("lastPrice", 0))
                logger.info(f"BTC price: ${self._btc_price:,.2f}")
            
            return True
        except Exception as e:
            logger.error(f"Failed to connect to BingX: {e}")
            return False

    async def run_once(self) -> Dict[str, Any]:
        """Один цикл наблюдения."""
        self._cycle += 1
        now = time.time()
        
        # Get current positions from BingX
        positions = await self._bingx.get_positions()
        
        # Update BTC trend
        btc_ticker = await self._bingx.get_ticker("BTCUSDT")
        if btc_ticker:
            new_btc_price = float(btc_ticker.get("lastPrice", 0))
            if self._btc_price > 0:
                btc_change = (new_btc_price - self._btc_price) / self._btc_price
                if btc_change > 0.005:
                    self._btc_trend = "BULLISH"
                elif btc_change < -0.005:
                    self._btc_trend = "BEARISH"
                else:
                    self._btc_trend = "NEUTRAL"
            self._btc_price = new_btc_price
        
        # Track current position symbols
        current_symbols = set()
        
        # Process each position
        for pos in positions:
            symbol = pos.get("symbol", "")
            position_amt = float(pos.get("positionAmt", 0))
            position_side = pos.get("positionSide", "LONG")  # LONG or SHORT
            
            if position_amt == 0:
                continue
            
            current_symbols.add(symbol)
            
            # BingX perpetual: positionSide dictates direction, amt is always positive
            side = "BUY" if position_side == "LONG" else "SELL"
            entry_price = float(pos.get("avgPrice", 0))
            mark_price = float(pos.get("markPrice", entry_price))
            unrealized_pnl = float(pos.get("unrealizedProfit", 0))
            leverage = int(pos.get("leverage", 1))
            created_at = pos.get("createTime", "")
            
            # Calculate PnL
            if side == "BUY":
                pnl_pct = (mark_price - entry_price) / entry_price if entry_price > 0 else 0
            else:
                pnl_pct = (entry_price - mark_price) / entry_price if entry_price > 0 else 0
            
            # Get candle data for high/low
            candles = await self._bingx.get_klines(symbol, "1m", limit=5)
            high = max(c["high"] for c in candles) if candles else mark_price
            low = min(c["low"] for c in candles) if candles else mark_price
            
            # Simple trend detection for this symbol
            if candles and len(candles) >= 2:
                price_change = (candles[-1]["close"] - candles[-2]["close"]) / candles[-2]["close"]
                if price_change > 0.001:
                    symbol_trend = "BULLISH"
                elif price_change < -0.001:
                    symbol_trend = "BEARISH"
                else:
                    symbol_trend = "NEUTRAL"
            else:
                symbol_trend = "UNKNOWN"
            
            # Create position ID from symbol + entry
            pos_id = f"LIVE_{symbol}_{int(entry_price)}"
            
            # Check if new position
            if pos_id not in self._known_positions:
                logger.info(f"[PIE] New position detected: {symbol} {side} @ {entry_price}")
                self._pie.on_position_open(
                    position_id=pos_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry_price,
                    metadata={
                        "leverage": leverage,
                        "created_at": created_at,
                        "source": "bingx_live",
                    },
                )
            
            # Update PIE with current state
            snapshot = self._pie.on_bar_update(
                position_id=pos_id,
                current_price=mark_price,
                high=high,
                low=low,
                bars_held=self._known_positions.get(pos_id, {}).get("bars", 0) + 1,
                trend=symbol_trend,
                btc_trend=self._btc_trend,
            )
            
            # Update known position
            self._known_positions[pos_id] = {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "pnl_pct": pnl_pct,
                "bars": self._known_positions.get(pos_id, {}).get("bars", 0) + 1,
                "last_update": now,
            }
            
            # Print status
            if snapshot:
                print(
                    f"[{self._cycle:4d}] {symbol:10s} {side:4s} "
                    f"entry={entry_price:.2f} now={mark_price:.2f} "
                    f"pnl={pnl_pct:+.2%} MFE={snapshot.max_profit_seen:+.2%} "
                    f"MAE={snapshot.max_loss_seen:+.2%} "
                    f"health={snapshot.health_score:.0f} "
                    f"rec={snapshot.recommendation.value}"
                )
        
        # Check for closed positions
        closed_ids = [pid for pid in self._known_positions if pid.split("_")[1] not in current_symbols]
        for pos_id in closed_ids:
            pos_info = self._known_positions[pos_id]
            logger.info(f"[PIE] Position closed: {pos_info['symbol']}")
            
            # Get final price
            ticker = await self._bingx.get_ticker(pos_info["symbol"])
            exit_price = float(ticker.get("lastPrice", pos_info["mark_price"]))
            
            self._pie.on_position_close(
                position_id=pos_id,
                exit_price=exit_price,
                exit_reason="EXCHANGE_CLOSED",
            )
            
            del self._known_positions[pos_id]
        
        # Return summary
        elapsed = time.time() - self._started_at
        return {
            "cycle": self._cycle,
            "elapsed_hours": round(elapsed / 3600, 2),
            "positions_tracked": len(self._known_positions),
            "btc_price": self._btc_price,
            "btc_trend": self._btc_trend,
        }

    async def run_daemon(self):
        """Запустить постоянное наблюдение."""
        logger.info(f"Starting PIE Live Observer (interval={self._interval}s)")
        logger.info(f"Database: {self._pie._db_path}")
        logger.info("Mode: OBSERVE ONLY — no trades will be executed")
        logger.info("-" * 60)
        
        try:
            while True:
                result = await self.run_once()
                await asyncio.sleep(self._interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        finally:
            await self._bingx.close()
            self._print_final_summary()

    def _print_final_summary(self):
        """Итоговый отчёт."""
        elapsed = time.time() - self._started_at
        
        print()
        print("=" * 60)
        print("  PIE LIVE OBSERVER — SESSION SUMMARY")
        print("=" * 60)
        print(f"  Duration: {elapsed/3600:.1f} hours")
        print(f"  Cycles: {self._cycle}")
        print(f"  Positions tracked: {len(self._known_positions)}")
        print(f"  BTC: ${self._btc_price:,.2f} ({self._btc_trend})")
        print()
        
        # PIE closed analytics
        closed = self._pie.get_closed_analytics(limit=100)
        if closed:
            print(f"  Closed positions: {len(closed)}")
            for c in closed:
                print(f"    {c['symbol']} {c['side']} MFE={c['max_profit_seen']:+.2%} "
                      f"MAE={c['max_loss_seen']:+.2%} retrace={c['profit_retracement']:.0%}")
        else:
            print("  No positions closed during observation")
        
        print("=" * 60)
        print()


async def main():
    parser = argparse.ArgumentParser(description="PIE Live Observer for BingX")
    parser.add_argument("--interval", type=int, default=60, help="Observation interval (seconds)")
    parser.add_argument("--once", action="store_true", help="Single observation cycle")
    parser.add_argument("--db", default=str(ROOT / "tradingos_data.db"), help="Database path")
    parser.add_argument("--mode", default="observe", choices=["observe", "live_assist"],
                       help="PIE mode: observe (default) or live_assist")
    
    args = parser.parse_args()
    
    # Load API keys from .env
    from dotenv import load_dotenv
    env_path = Path("/opt/ubot_bingx/.env")
    load_dotenv(env_path)
    
    api_key = os.getenv("BINGX_API_KEY")
    api_secret = os.getenv("BINGX_API_SECRET")
    
    if not api_key or not api_secret:
        print("ERROR: BINGX_API_KEY and BINGX_API_SECRET must be set in .env")
        sys.exit(1)
    
    observer = PIELiveObserver(
        api_key=api_key,
        api_secret=api_secret,
        db_path=Path(args.db),
        interval=args.interval,
        pie_mode=args.mode,
    )
    
    if not await observer.initialize():
        print("ERROR: Failed to connect to BingX")
        sys.exit(1)
    
    if args.once:
        result = await observer.run_once()
        print(f"Result: {result}")
    else:
        await observer.run_daemon()
    
    await observer._bingx.close()


if __name__ == "__main__":
    asyncio.run(main())
