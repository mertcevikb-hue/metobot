import asyncio
from datetime import datetime, timezone
from loguru import logger

from config.watchlist import WatchlistManager
from training.market_scheduler import MarketScheduler
from data.historical import HistoricalDataLoader
from training.paper_trader import PaperTrader
from attribution.post_mortem import PostMortemEngine
from training.weight_optimizer import WeightOptimizer
from database.db_manager import DatabaseManager

class Bot1Trainer:
    def __init__(self):
        self.db = DatabaseManager()
        self.watchlist_manager = WatchlistManager()
        self.scheduler = MarketScheduler(self.watchlist_manager)
        self.data_loader = HistoricalDataLoader()
        self.trader = PaperTrader()
        self.post_mortem = PostMortemEngine()
        self.optimizer = WeightOptimizer()
        
        self.training_interval = 3600
        self.is_running = False
        self.session_stats = {
            "total_trades": 0,
            "symbols_learned": 0,
            "start_time": None
        }

    async def initialize(self):
        logger.info("🚀 Bot 1 Self-Teaching Engine Starting...")
        await self.db.connect()
        symbols = self.watchlist_manager.get_symbols()
        logger.info(f"✓ Watchlist loaded: {len(symbols)} symbols")
        logger.info(f"✓ Symbols: {symbols}")
        self.session_stats["start_time"] = datetime.now(timezone.utc).isoformat()

    async def learn_from_symbol(self, symbol):
        """Learn from a single symbol"""
        try:
            logger.info(f"📚 Learning from {symbol}...")
            
            # Get data and simulate trades for this specific symbol
            market_data = await self.data_loader.fetch_recent_data(symbol=symbol, days=30)
            closed_trades = await self.trader.simulate(market_data, symbol=symbol)
            
            # Analyze results with SMC Post-Mortem
            attribution_logs = []
            for trade in closed_trades:
                ctx = trade.get("market_context") or {}
                reason = self.post_mortem.evaluate(trade, ctx)
                attribution_logs.append(reason)
            
            # Save and optimize
            if attribution_logs:
                await self.db.save_attribution_logs(attribution_logs)
                new_weights = self.optimizer.calculate_new_weights(attribution_logs)
                await self.db.update_model_weights(new_weights)
            
            self.session_stats["total_trades"] += len(closed_trades)
            self.session_stats["symbols_learned"] += 1
            logger.info(f"✅ {symbol}: {len(closed_trades)} trades, weights optimized")
            
        except Exception as e:
            logger.error(f"Error learning from {symbol}: {e}")

    async def run_training_loop(self):
        """Train on all watchlist symbols"""
        logger.info("🧠 Training loop started...")
        self.is_running = True
        
        try:
            open_exchanges = self.scheduler.get_open_exchanges()
            for exchange in open_exchanges:
                symbols = self.watchlist_manager.get_symbols(exchange)
                logger.info(f"📊 {exchange}: {len(symbols)} symbols")
                
                for symbol in symbols:
                    if not self.is_running:
                        break
                    await self.learn_from_symbol(symbol)
                    await asyncio.sleep(5)
            
            logger.success(f"✅ Completed: {self.session_stats}")
            
        except Exception as e:
            logger.error(f"Training loop error: {e}")
        finally:
            self.is_running = False

    async def start(self):
        """Main entry"""
        await self.initialize()
        await self.scheduler.run_market_hours_check(self.run_training_loop)

if __name__ == "__main__":
    trainer = Bot1Trainer()
    asyncio.run(trainer.start())