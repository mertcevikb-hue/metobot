"""Market Hours Scheduler - Control Bot 1 by market hours"""

from loguru import logger
import asyncio

class MarketScheduler:
    def __init__(self, watchlist_manager):
        self.watchlist = watchlist_manager
        self.is_trading = False
    
    def should_trade_now(self):
        for exchange in ["NASDAQ", "NYSE", "BIST"]:
            if self.watchlist.is_market_open(exchange):
                return True
        return False
    
    def get_open_exchanges(self):
        return [e for e in ["NASDAQ", "NYSE", "BIST"] if self.watchlist.is_market_open(e)]
    
    def get_market_status(self):
        return {e: {"open": self.watchlist.is_market_open(e), "symbols": len(self.watchlist.get_symbols(e))} 
                for e in ["NASDAQ", "NYSE", "BIST"]}
    
    async def run_market_hours_check(self, bot1_callback):
        """Run Bot 1 only during market hours"""
        logger.info("📅 Market Scheduler Started")
        while True:
            try:
                if self.should_trade_now():
                    if not self.is_trading:
                        logger.info(f"📈 Markets OPEN: {self.get_open_exchanges()}")
                        self.is_trading = True
                    await bot1_callback()
                else:
                    if self.is_trading:
                        logger.info("⏸️  Markets CLOSED")
                        self.is_trading = False
                    await asyncio.sleep(300)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)