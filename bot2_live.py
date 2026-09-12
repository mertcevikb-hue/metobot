"""
Bot 2: Live Signal Generation Engine (Production-Ready)

Real-time trading bot that:
1. Streams live market data from exchange
2. Validates OHLCV data (DataGuard)
3. Calculates technical features (ATR, EMA, RSI, VWAP, BB)
4. Detects market regime (trend, range, volatility)
5. Analyzes market structure (SMC: BOS, CHoCH, OB, FVG)
6. Generates trading signals with continuous scores
7. Applies risk-based position sizing
8. Manages position state with hysteresis
9. Executes or logs signals
10. Broadcasts to dashboard in real-time

Signal Pipeline:
    Live Exchange
         ↓
    LiveDataFeed (stream candles)
         ↓
    DataGuard (validate OHLCV)
         ↓
    FeatureEngine (calculate indicators)
         ↓
    RegimeEngine (detect market regime)
         ↓
    QuantEngine (orchestrate full analysis)
         ↓
    RiskEngine (position sizing)
         ↓
    PositionStateMachine (state management)
         ↓
    SignalDispatcher (broadcast & execute)
         ↓
    Database (log all signals)
"""

import asyncio
import datetime
from typing import Dict, Any, Optional, List, Tuple, Set
from loguru import logger

from data.live_feed import init_live_feed, get_live_feed
from engine import QuantEngine, DataGuard, FeatureEngine, RegimeEngine, RiskEngine, PositionStateMachine, MarketSchedule
from database.db_manager import DatabaseManager
from execution.signal_dispatcher import SignalDispatcher


class Bot2LiveEngine:
    """Production-ready live trading engine following Watchlist Terminal."""

    def __init__(self, symbols: Optional[List[str]] = None, account_balance: float = 10000.0, db: Optional[DatabaseManager] = None):
        self.initial_symbols = [s.strip().upper() for s in symbols] if symbols else []
        self.symbols = list(self.initial_symbols)
        self.account_balance = account_balance
        self.is_running = False

        # Dynamic symbol task management
        self.symbol_tasks: Dict[str, asyncio.Task] = {}
        self.tracked_symbols: Set[str] = set()

        # Components
        self.db = db
        self.live_feed = None
        self.dispatcher = SignalDispatcher(db=self.db) if self.db else SignalDispatcher()

        # State tracking
        self.positions = {}  # symbol -> {state, entry_price, etc.}
        self.signal_count = 0
        self.trade_count = 0
        self.stats = {
            "signals_generated": 0,
            "trades_executed": 0,
            "errors": 0,
            "started_at": None
        }

    async def sync_watchlist_symbols(self) -> Tuple[Set[str], Set[str]]:
        """
        Dynamically synchronize running streams with database Watchlist Terminal.
        Spawns streams for newly added symbols and terminates streams for removed symbols.
        Returns (to_add, to_remove).
        """
        try:
            if not self.db:
                return set(), set()

            watchlist_items = await self.db.get_watchlist()
            db_symbols = set(item.symbol.strip().upper() for item in watchlist_items if item.symbol)

            # If DB watchlist is empty, fall back to initial symbols or defaults
            if not db_symbols:
                db_symbols = set(self.initial_symbols) if self.initial_symbols else {"SPY", "QQQ", "AAPL"}

            self.symbols = sorted(list(db_symbols))

            # Symbols to start and remove
            to_add = db_symbols - self.tracked_symbols
            to_remove = self.tracked_symbols - db_symbols
            self.tracked_symbols = set(db_symbols)

            for sym in to_add:
                if sym not in self.positions:
                    open_trade = await self.db.get_open_trade_for_symbol(sym) if self.db else None
                    if open_trade:
                        self.positions[sym] = {
                            "state": "HOLDING",
                            "entry_price": open_trade.get("entry_price"),
                            "entry_time": open_trade.get("created_at"),
                            "entry_score": open_trade.get("score"),
                            "take_profit": open_trade.get("take_profit"),
                            "stop_loss": open_trade.get("stop_loss"),
                            "side": open_trade.get("side", "LONG"),
                            "open_trade": open_trade
                        }
                    else:
                        self.positions[sym] = {
                            "state": "WATCH",
                            "entry_price": None,
                            "entry_time": None,
                            "entry_score": None,
                            "take_profit": None,
                            "stop_loss": None,
                            "side": None,
                            "open_trade": None
                        }
                if self.is_running and self.live_feed and self.live_feed.is_connected:
                    logger.info(f"➕ Watchlist Terminal sync: Starting live stream for {sym}")
                    self.symbol_tasks[sym] = asyncio.create_task(self.run_symbol_stream(sym))

            # Symbols to remove
            for sym in to_remove:
                logger.info(f"➖ Watchlist Terminal sync: Stopping live stream for {sym}")
                task = self.symbol_tasks.pop(sym, None)
                if task and not task.done():
                    task.cancel()
                self.positions.pop(sym, None)

            return to_add, to_remove

        except Exception as e:
            logger.error(f"Error synchronizing watchlist symbols: {e}")
            return set(), set()

    async def initialize(self):
        """Initialize all components."""
        logger.info("🚀 Initializing Bot 2 (Live Engine)...")

        try:
            # Connect to database
            if self.db is None:
                self.db = DatabaseManager()
                await self.db.connect()
                logger.info("✓ Database connected")
            else:
                try:
                    await self.db.connect()
                except Exception:
                    pass

            # Initialize dispatcher with database connection
            self.dispatcher = SignalDispatcher(db=self.db)

            # Synchronize symbols from database watchlist
            await self.sync_watchlist_symbols()

            # Startup reconciliation: force-close any stale positions outside session hours
            reconciled = await self.db.reconcile_stale_positions()
            if reconciled > 0:
                logger.warning(f"🛡️ Bot 2 Startup: Reconciled {reconciled} stale positions outside session hours.")

            # Hydrate active open positions from database
            if self.db:
                open_trades = await self.db.get_open_trades()
                for ot in open_trades:
                    sym = (ot.get("underlying") or ot.get("symbol", "")).strip().upper()
                    if sym:
                        self.positions[sym] = {
                            "state": "HOLDING",
                            "entry_price": ot.get("entry_price"),
                            "entry_time": ot.get("created_at"),
                            "entry_score": ot.get("score"),
                            "take_profit": ot.get("take_profit"),
                            "stop_loss": ot.get("stop_loss"),
                            "side": ot.get("side", "LONG"),
                            "open_trade": ot
                        }
                        logger.info(f"📂 Hydrated active open position for {sym} (Trade {ot.get('trade_id')}, Entry: ${ot.get('entry_price')}, TP: {ot.get('take_profit')}, SL: {ot.get('stop_loss')})")

            # Initialize live feed
            self.live_feed = await init_live_feed(self.symbols)
            logger.info(f"✓ Live feed connected ({len(self.symbols)} symbols from Watchlist: {', '.join(self.symbols)})")

            self.stats["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            logger.info("✓ Bot 2 initialized successfully")

        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            raise

    async def process_candle(self, symbol: str, candle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process a candle and generate signal if criteria met.

        Returns:
            Signal dict or None
        """
        try:
            current_price = candle.get("close", 0)
            candle_high = candle.get("high", current_price)
            candle_low = candle.get("low", current_price)
            self.positions[symbol]["current_price"] = current_price

            # Synchronize open trade state with database / cache
            open_trade = None
            if self.db:
                open_trade = await self.db.get_open_trade_for_symbol(symbol)
            if open_trade:
                self.positions[symbol]["open_trade"] = open_trade
                self.positions[symbol]["take_profit"] = open_trade.get("take_profit") or self.positions[symbol].get("take_profit")
                self.positions[symbol]["stop_loss"] = open_trade.get("stop_loss") or self.positions[symbol].get("stop_loss")
                self.positions[symbol]["side"] = open_trade.get("side") or self.positions[symbol].get("side", "LONG")
                if self.positions[symbol].get("state") == "WATCH":
                    self.positions[symbol]["state"] = "HOLDING"
            elif self.positions[symbol].get("state") in ("HOLDING", "ENTERED") and not open_trade:
                # Position was closed elsewhere (e.g. manual exit or 23:00 watchdog)
                self.positions[symbol]["state"] = "WATCH"
                self.positions[symbol]["open_trade"] = None

            # 1. Get historical candles for analysis
            candles = await self.live_feed.get_candles(symbol, timeframe="1m", limit=100)
            if not candles or len(candles) < 35:
                logger.debug(f"Insufficient data for {symbol}")
                return None

            # Extract OHLCV arrays
            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            volumes = [c["volume"] for c in candles]

            # 2. Validate data integrity
            valid, error = DataGuard.validate_ohlcv(closes, highs, lows, volumes)
            if not valid:
                logger.warning(f"⚠️  {symbol}: Data validation failed: {error}")
                self.positions[symbol]["state"] = "INVALIDATED"
                return None

            # 3. Run full quantitative analysis using centralized engine
            current_pos_state = self.positions[symbol].get("state", "WATCH")
            analysis = QuantEngine.analyze_ticker(
                symbol=symbol,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
                current_position_state=current_pos_state
            )

            # 4. Update position state
            new_state = analysis.get("position_state", "WATCH")
            old_state = self.positions[symbol]["state"]
            if new_state != old_state:
                logger.info(f"📍 {symbol}: State transition {old_state} → {new_state} (score: {analysis.get('setup_score'):.1f})")
                self.positions[symbol]["state"] = new_state

            # Market Hours Verification (Regular hours: 09:30 - 16:00 ET for US Equities & Options)
            is_market_open, session, market_reason = MarketSchedule.is_market_open(symbol)
            market_info = MarketSchedule.get_market_status(symbol)

            entry_valid = analysis.get("entry_valid", False)
            decision = analysis.get("decision", "NO TRADE")
            reasons = list(analysis.get("reasons", []))
            warnings = list(analysis.get("warnings", []))

            # Strict Execution Gating: If market is closed, block position entries
            if not is_market_open:
                entry_valid = False
                if decision in ["LONG", "SHORT", "BUY", "SELL"]:
                    decision = "NO TRADE"
                    warnings.append(f"GATED: {market_reason}")

            # 5. Create signal object for logging/broadcasting (includes risk_metrics)
            signal = {
                "symbol": symbol,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "price": current_price,
                "regime": analysis.get("regime", "UNKNOWN"),
                "strategy": analysis.get("strategy", "NONE"),
                "score": analysis.get("setup_score", 0.0),
                "position_state": new_state,
                "entry_valid": entry_valid,
                "decision": decision,
                "is_vetoed": analysis.get("is_vetoed", False),
                "veto_reasons": list(analysis.get("veto_reasons", [])),
                "market_session": session,
                "market_is_open": is_market_open,
                "market_info": market_info,
                "reasons": reasons,
                "warnings": warnings,
                "features": analysis.get("features", {}),
                "risk_metrics": analysis.get("risk_metrics", {}),
                "risk_level": analysis.get("risk_level", "UNKNOWN")
            }

            # 6. Enrich signal with nearest options data (Strike, Expiration, Spread, Premium)
            await self.dispatcher._enrich_with_options_data(signal)

            # Re-sync veto status if option enrichment applied a veto
            if signal.get("veto_reason"):
                signal["is_vetoed"] = True
                if signal["veto_reason"] not in signal["veto_reasons"]:
                    signal["veto_reasons"].append(signal["veto_reason"])
                signal["decision"] = "NO TRADE"
                signal["entry_valid"] = False

            # 7. Save signal to database (rate limit identical NO TRADE spam)
            last_saved = self.positions[symbol].get("last_saved_time")
            now_ts = datetime.datetime.now(datetime.timezone.utc)
            time_elapsed = (now_ts - last_saved).total_seconds() if last_saved else 999999
            
            is_significant = (
                signal["decision"] != "NO TRADE" or
                new_state != old_state or
                signal["score"] >= 50.0 or
                time_elapsed > 300  # Periodic status log every 5 minutes
            )

            if is_significant:
                await self.db.save_live_signal(signal)
                self.positions[symbol]["last_saved_time"] = now_ts
                self.stats["signals_generated"] += 1

            # 8. Broadcast signal to web dashboard
            try:
                from web.routes.ws import broadcast_signal
                await broadcast_signal(signal)
            except Exception:
                pass

            # 8b. Dispatch Telegram signal notification
            last_tg = self.positions[symbol].get("last_telegram_time")
            tg_elapsed = (now_ts - last_tg).total_seconds() if last_tg else 999999
            should_tg = (
                (signal.get("entry_valid") and signal.get("decision") in ["LONG", "SHORT", "BUY", "SELL"]) or
                (new_state == "ENTRY_READY" and tg_elapsed > 180) or
                (signal.get("score", 0.0) >= 75.0 and tg_elapsed > 180) or
                (tg_elapsed > 600)  # 10 minute periodic pulse report for watchlist symbols
            )
            if should_tg and self.dispatcher and getattr(self.dispatcher, "notify_telegram", True):
                try:
                    await self.dispatcher._notify_telegram(signal)
                    self.positions[symbol]["last_telegram_time"] = now_ts
                except Exception as tg_err:
                    logger.warning(f"Telegram notification error for {symbol}: {tg_err}")

            # 9. Log signal & trigger high conviction 85+ card (or Veto Shield card)
            opt = signal.get("option_data", {})
            if signal.get("is_vetoed") and signal["score"] >= 75.0:
                logger.warning(
                    f"\n{'!'*65}\n"
                    f"🛡️ [VETO SHIELD: CHASE / EXHAUSTION PREVENTED] {symbol} @ ${current_price:.2f} (Score: {signal['score']:.1f}/100)\n"
                    f"⛔ REASONS: {'; '.join(signal.get('veto_reasons', []))}\n"
                    f"💡 RESULT: Late-entry total-loss risk successfully averted.\n"
                    f"{'!'*65}"
                )
            elif signal["score"] >= 85.0 and signal.get("entry_valid"):
                logger.warning(
                    f"\n{'='*65}\n"
                    f"🎯 [85+ HIGH CONVICTION TRADE SIGNAL] {symbol} @ ${current_price:.2f} (Score: {signal['score']:.1f}/100)\n"
                    f"📌 DIRECTION: {signal['decision']} | CONTRACT: {opt.get('contract_type', 'CALL')} STRIKE: {opt.get('strike_price', 'ATM')}\n"
                    f"📅 EXPIRATION: {opt.get('expiration_date', 'N/A')} | SPREAD: {opt.get('bid_ask_spread', 'N/A')} | PREMIUM: {opt.get('premium', 'N/A')}\n"
                    f"📊 REASONS: {', '.join(signal.get('reasons', []))}\n"
                    f"⏰ SESSION: {session} ({'OPEN' if is_market_open else 'CLOSED'})\n"
                    f"{'='*65}"
                )
            elif signal["score"] >= 50.0:
                logger.info(f"📈 {symbol}: Score {signal['score']:.1f} | {signal['decision']} | {session}")

            # 10. Position Execution Lifecycle via PositionManager & DynamicExitEngine
            from engine.position_manager import global_position_manager
            active_managed_pos = global_position_manager.get_position(symbol)
            has_open_pos = (open_trade is not None) or (active_managed_pos is not None) or (self.positions[symbol].get("state") in ("ENTERED", "HOLDING", "PROFIT_PROTECTION", "RUNNER", "EXIT_READY"))

            # --- A. CHECK ACTIVE POSITION EXITS (3-Layer Dynamic Exit: TP1, TP2, Trailing SL, Profit Protection, Rotation) ---
            if has_open_pos:
                # If managed position not in memory but in DB, hydrate into PositionManager
                if not active_managed_pos and open_trade:
                    active_managed_pos = global_position_manager.open_position(open_trade)

                exit_eval = global_position_manager.evaluate_candle(
                    underlying=symbol,
                    candle=candle,
                    features=analysis.get("features", {})
                )

                should_exit = exit_eval.get("should_exit", False)
                exit_price = exit_eval.get("exit_price", current_price)
                exit_reason = exit_eval.get("exit_reason", "Dynamic exit condition triggered")
                result_code = exit_eval.get("result_code", "CLOSED")

                # Fallback: Hard Engine Exit (only if EXIT_READY, never EXIT_WARNING)
                if not should_exit and new_state == "EXIT_READY" and signal.get("decision") == "EXIT":
                    should_exit = True
                    exit_price = current_price
                    exit_reason = f"State Machine exit signal triggered (Score: {signal['score']:.1f})"
                    result_code = "EXIT_SIGNAL"

                if should_exit:
                    logger.warning(f"🛑 {symbol}: {exit_reason}. Executing position exit for {symbol}...")
                    closed = await self.dispatcher.execute_position_exit(
                        symbol=symbol,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        result=result_code
                    )
                    actual_exit = closed.get("exit_price", exit_price) if closed else exit_price
                    self.positions[symbol]["state"] = "WATCH"
                    self.positions[symbol]["exit_price"] = actual_exit
                    self.positions[symbol]["exit_time"] = signal["timestamp"]
                    self.positions[symbol]["open_trade"] = None
                    self.positions[symbol]["take_profit"] = None
                    self.positions[symbol]["stop_loss"] = None
                    logger.info(f"✅ {symbol}: Position closed successfully at ${actual_exit:.2f} ({result_code})")
                    if self.dispatcher and getattr(self.dispatcher, "notify_telegram", True):
                        try:
                            await self.dispatcher.notify_position_exit(
                                symbol=symbol,
                                exit_price=actual_exit,
                                exit_reason=exit_reason,
                                pnl=closed.get("pnl") if closed else None
                            )
                        except Exception as ex_err:
                            logger.warning(f"Failed to send exit telegram alert: {ex_err}")

            # --- B. ENTRY EXECUTION (When no active open position) ---
            elif not has_open_pos:
                trade_eval = analysis.get("trade_evaluation", {})
                if not is_market_open:
                    if new_state == "ENTRY_READY" and analysis.get("entry_valid"):
                        logger.info(f"⏸️ {symbol}: Entry setup confirmed, but market is CLOSED ({session}: {market_reason}). Position entry blocked.")
                elif new_state == "ENTRY_READY" and signal.get("entry_valid") and signal.get("decision") in ["LONG", "SHORT", "BUY", "SELL"]:
                    if trade_eval.get("approved", True):
                        logger.info(f"🚀 {symbol}: Entry approved by Trade Engine. Executing paper trade order...")
                        dispatch_res = await self.dispatcher.dispatch(signal)
                        if dispatch_res.get("order", {}).get("status") in ["filled", "open"]:
                            self.positions[symbol]["state"] = "ENTERED"
                            self.positions[symbol]["entry_price"] = current_price
                            self.positions[symbol]["entry_time"] = signal["timestamp"]
                            self.positions[symbol]["entry_score"] = signal["score"]
                            self.positions[symbol]["take_profit"] = signal.get("risk_metrics", {}).get("take_profit")
                            self.positions[symbol]["stop_loss"] = signal.get("risk_metrics", {}).get("stop_loss")
                            self.positions[symbol]["side"] = signal.get("decision", "LONG")
                            self.positions[symbol]["open_trade"] = dispatch_res.get("trade")
                            self.positions[symbol]["last_telegram_time"] = now_ts
                            self.stats["trades_executed"] += 1
                        else:
                            logger.warning(f"Order not filled: {dispatch_res.get('order', {}).get('reason')}")
                    else:
                        logger.info(f"⛔ {symbol}: Entry blocked by Trade Engine VETO ({trade_eval.get('primary_reason')}).")

            return signal

        except Exception as e:
            logger.error(f"❌ Error processing {symbol}: {e}")
            self.stats["errors"] += 1
            return None

    async def run_symbol_stream(self, symbol: str):
        """Stream candles for a single symbol."""
        try:
            logger.info(f"📊 Starting stream for {symbol}...")

            async for candle in self.live_feed.stream_candles(symbol):
                if not self.is_running:
                    break

                # Process the candle
                signal = await self.process_candle(symbol, candle)

                # Optional: Execute trade if signal valid
                # if signal and signal["entry_valid"]:
                #     await self.execute_signal(signal)

        except Exception as e:
            logger.error(f"Stream error for {symbol}: {e}")

    async def run_live_loop(self):
        """Main loop - process all symbols concurrently and dynamically sync with Watchlist Terminal."""
        logger.info("🎬 Starting live trading loop following Watchlist Terminal...")
        self.is_running = True

        try:
            # Launch streams for current watchlist symbols
            for symbol in list(self.symbols):
                if symbol not in self.symbol_tasks:
                    self.symbol_tasks[symbol] = asyncio.create_task(self.run_symbol_stream(symbol))

            # Dynamic Watchlist Watcher & Session Close Watchdog (2-second tick)
            loop_counter = 0
            while self.is_running:
                await asyncio.sleep(2)
                loop_counter += 1

                # Session End Enforcement: If session is closed, ensure open positions are reconciled
                if MarketSchedule.is_market_closed() and self.db:
                    await self.db.reconcile_stale_positions()
                    for sym, pos in self.positions.items():
                        if pos.get("state") == "ENTERED":
                            pos["state"] = "WATCH"
                            pos["entry_price"] = None

                # Every 10s (5 ticks): sync watchlist
                if loop_counter % 5 == 0:
                    await self.sync_watchlist_symbols()

        except asyncio.CancelledError:
            logger.info("Live loop cancelled")
        except Exception as e:
            logger.error(f"Fatal error in live loop: {e}")
        finally:
            for sym, task in list(self.symbol_tasks.items()):
                if task and not task.done():
                    task.cancel()
            self.symbol_tasks.clear()
            self.is_running = False

    async def report_status(self):
        """Periodically report bot status."""
        while self.is_running:
            try:
                await asyncio.sleep(300)  # Every 5 minutes

                # Feed health
                health = await self.live_feed.health_check()
                logger.info(f"📊 Feed Health: {len(health['last_prices'])} prices, "
                           f"{len(health.get('stale_symbols', []))} stale")

                # Position summary
                states = {}
                for sym, pos in self.positions.items():
                    state = pos.get("state", "UNKNOWN")
                    states[state] = states.get(state, 0) + 1

                logger.info(f"📍 Positions: {states}")
                logger.info(f"📈 Stats: {self.stats['signals_generated']} signals, "
                           f"{self.stats['errors']} errors")

            except Exception as e:
                logger.error(f"Status report error: {e}")

    async def stop(self):
        """Gracefully stop the bot."""
        logger.info("🛑 Stopping Bot 2...")
        self.is_running = False

        for sym, task in list(self.symbol_tasks.items()):
            if task and not task.done():
                task.cancel()
        self.symbol_tasks.clear()

        if self.live_feed:
            await self.live_feed.disconnect()

        if self.db:
            await self.db.close()

        # Log final stats
        logger.info(f"✓ Bot stopped. Final stats: {self.stats}")

    async def start(self):
        """Start the bot."""
        try:
            await self.initialize()

            # Run live loop and status reporter concurrently
            await asyncio.gather(
                self.run_live_loop(),
                self.report_status()
            )

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self.stop()


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))

    # Start bot - automatically dynamically follows database Watchlist Terminal
    bot = Bot2LiveEngine(symbols=None, account_balance=ACCOUNT_BALANCE)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Bot interrupted")

