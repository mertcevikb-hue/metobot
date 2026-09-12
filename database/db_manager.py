import os
import uuid
import re
from datetime import datetime, timezone
from typing import List, Any, Optional, Dict
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, desc, func, text, event
from sqlalchemy.engine import Engine
from loguru import logger
from database.schema import Base, WatchlistSymbol, ModelWeight, LiveSignal, AttributionLog, TradeLog, PortfolioState, RejectedSignal

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./database/metobot.db")

TICKER_VALIDATOR = re.compile(r"^[A-Za-z0-9.\-_/]{1,15}$")


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()
    except Exception:
        pass


class DatabaseManager:
    def __init__(self, db_url: str = DATABASE_URL):
        connect_args = {}
        if "sqlite" in db_url:
            connect_args["timeout"] = 30
        self.engine = create_async_engine(db_url, echo=False, future=True, connect_args=connect_args)
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    @staticmethod
    def _migrate_columns(connection):
        """Ensures newly added columns exist in SQLite trade_logs table."""
        try:
            res = connection.exec_driver_sql("PRAGMA table_info(trade_logs);").fetchall()
            existing_cols = [r[1] for r in res]
            new_columns = [
                ("tp1", "FLOAT"),
                ("tp2", "FLOAT"),
                ("trailing_stop", "FLOAT"),
                ("direction_score", "FLOAT"),
                ("direction_edge", "FLOAT"),
                ("confidence", "FLOAT"),
                ("signal_type", "VARCHAR(64)"),
                ("entry_thesis", "TEXT"),
                ("reject_reason", "VARCHAR(128)"),
                ("instrument", "VARCHAR(32) DEFAULT 'STOCK'"),
                ("is_0dte", "INTEGER DEFAULT 0"),
                ("mae", "FLOAT"),
                ("mfe", "FLOAT"),
            ]
            for col_name, col_type in new_columns:
                if col_name not in existing_cols:
                    connection.exec_driver_sql(f"ALTER TABLE trade_logs ADD COLUMN {col_name} {col_type};")
                    logger.info(f"🛡️ Migrated schema: Added column '{col_name}' to trade_logs.")
        except Exception as e:
            logger.warning(f"Schema column migration notice: {e}")

    async def connect(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(self._migrate_columns)
        logger.info("Database connection and tables successfully verified.")

    async def close(self):
        """Safely close async database connection pool."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database connection closed.")

    async def get_watchlist(self) -> List[Any]:
        async with self.async_session() as session:
            result = await session.execute(select(WatchlistSymbol))
            return result.scalars().all()

    async def add_symbol(self, symbol: str) -> bool:
        clean = (symbol or "").strip().upper().replace("$", "")
        if not clean or not TICKER_VALIDATOR.match(clean):
            return False
        async with self.async_session() as session:
            async with session.begin():
                existing = await session.execute(
                    select(WatchlistSymbol).where(WatchlistSymbol.symbol == clean)
                )
                if existing.scalars().first():
                    return False
                session.add(WatchlistSymbol(symbol=clean))
                return True

    async def remove_symbol(self, symbol: str) -> bool:
        clean = (symbol or "").strip().upper().replace("$", "")
        if not clean or not TICKER_VALIDATOR.match(clean):
            return False
        async with self.async_session() as session:
            async with session.begin():
                existing = await session.execute(
                    select(WatchlistSymbol).where(WatchlistSymbol.symbol == clean)
                )
                obj = existing.scalars().first()
                if obj:
                    await session.delete(obj)
                    return True
                return False

    async def save_attribution_logs(self, logs: List[Dict[str, Any]]) -> bool:
        """Bot 1'in ürettiği Post-Mortem (otopsi) raporlarını veritabanına kaydeder."""
        if not logs:
            return False
        try:
            async with self.async_session() as session:
                async with session.begin():
                    for log in logs:
                        entry = AttributionLog(
                            trade_id=str(log.get("trade_id") or f"tr_{uuid.uuid4().hex[:8]}"),
                            symbol=str(log.get("symbol") or "SPY"),
                            result=str(log.get("result") or "UNKNOWN"),
                            primary_reason=str(log.get("primary_reason") or "MARKET_NOISE"),
                            tags=log.get("tags") or [],
                            weight_adjustment_hint=log.get("weight_adjustment_hint") or {},
                            created_at=datetime.now(timezone.utc)
                        )
                        session.add(entry)
            logger.info(f"💾 {len(logs)} adet eğitim/atıf logu veritabanına başarıyla kaydedildi.")
            return True
        except Exception as e:
            logger.error(f"Atıf logları kaydedilirken hata oluştu: {e}")
            return False

    async def update_model_weights(self, weights: Dict[str, Any]) -> bool:
        """Optimize edilen yeni katsayı matrisini yeni versiyon olarak veritabanına işler."""
        if not weights:
            return False
        try:
            async with self.async_session() as session:
                async with session.begin():
                    # Mevcut en yüksek versiyon numarasını bul
                    stmt = select(ModelWeight.version).order_by(desc(ModelWeight.version)).limit(1)
                    res = await session.execute(stmt)
                    current_version = res.scalar_one_or_none() or 0
                    new_version = current_version + 1

                    entry = ModelWeight(
                        version=new_version,
                        weights=weights,
                        updated_at=datetime.now(timezone.utc)
                    )
                    session.add(entry)
            logger.info(f"🧠 Yeni model ağırlıkları (v{new_version}) veritabanına işlendi: {weights}")
            return True
        except Exception as e:
            logger.error(f"Model ağırlığı kaydedilirken hata: {e}")
            return False

    async def get_latest_model_weights(self) -> Optional[Dict[str, Any]]:
        """Bot 2 ve Dashboard için en son eğitilmiş katsayıları getirir."""
        try:
            async with self.async_session() as session:
                stmt = select(ModelWeight).order_by(desc(ModelWeight.version)).limit(1)
                res = await session.execute(stmt)
                record = res.scalars().first()
                if record:
                    return record.weights
        except Exception as e:
            logger.error(f"Model ağırlıkları okunurken hata: {e}")
        return None

    async def save_live_signal(self, signal: Dict[str, Any]) -> bool:
        """Bot 2'nin ürettiği canlı sinyalleri veritabanına kaydeder."""
        if not signal:
            return False
        try:
            async with self.async_session() as session:
                async with session.begin():
                    entry = LiveSignal(
                        symbol=signal.get("symbol", "UNKNOWN"),
                        score=float(signal.get("score", 0.0)),
                        regime=str(signal.get("regime", "UNKNOWN")),
                        features=signal,
                        timestamp=datetime.now(timezone.utc)
                    )
                    session.add(entry)
            return True
        except Exception as e:
            logger.error(f"Live signal save error: {e}")
            return False

    async def get_recent_live_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent live signals for dashboard stream."""
        try:
            async with self.async_session() as session:
                stmt = select(LiveSignal).order_by(desc(LiveSignal.id)).limit(limit)
                res = await session.execute(stmt)
                records = res.scalars().all()
                return [
                    {
                        "id": r.id,
                        "symbol": r.symbol,
                        "score": r.score,
                        "regime": r.regime,
                        "features": r.features,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"Signals retrieval error: {e}")
            return []

    async def create_trade(self, trade_data: Dict[str, Any]) -> Dict[str, Any]:
        """Record an executed/opened trade position into database."""
        if not trade_data:
            return {}
        try:
            trade_id = trade_data.get("trade_id") or f"tr_{uuid.uuid4().hex[:8]}"
            symbol = str(trade_data.get("symbol", "UNKNOWN")).strip().upper()
            raw_underlying = trade_data.get("underlying") or (symbol.split("2")[0] if "2" in symbol and len(symbol) > 10 else symbol)
            underlying = str(raw_underlying).strip().upper() if raw_underlying else symbol
            side = str(trade_data.get("side", "LONG")).strip().upper()
            entry_price = float(trade_data.get("entry_price", 0.0))
            
            entry = TradeLog(
                trade_id=trade_id,
                symbol=symbol,
                underlying=underlying,
                option_type=trade_data.get("option_type"),
                strike_price=float(trade_data["strike_price"]) if trade_data.get("strike_price") is not None else None,
                expiration=trade_data.get("expiration"),
                contract_symbol=trade_data.get("contract_symbol"),
                side=side,
                entry_price=entry_price,
                exit_price=None,
                stop_loss=float(trade_data["stop_loss"]) if trade_data.get("stop_loss") is not None else None,
                take_profit=float(trade_data["take_profit"]) if trade_data.get("take_profit") is not None else None,
                tp1=float(trade_data["tp1"]) if trade_data.get("tp1") is not None else float(trade_data["take_profit"]) if trade_data.get("take_profit") is not None else None,
                tp2=float(trade_data["tp2"]) if trade_data.get("tp2") is not None else None,
                trailing_stop=float(trade_data["trailing_stop"]) if trade_data.get("trailing_stop") is not None else float(trade_data["stop_loss"]) if trade_data.get("stop_loss") is not None else None,
                quantity=float(trade_data.get("quantity", 1.0)),
                score=float(trade_data["score"]) if trade_data.get("score") is not None else None,
                direction_score=float(trade_data["direction_score"]) if trade_data.get("direction_score") is not None else None,
                direction_edge=float(trade_data["direction_edge"]) if trade_data.get("direction_edge") is not None else None,
                confidence=float(trade_data["confidence"]) if trade_data.get("confidence") is not None else None,
                strategy=trade_data.get("strategy", "QUANT_MOMENTUM"),
                signal_type=trade_data.get("signal_type", "SETUP_ENTRY"),
                entry_thesis=trade_data.get("entry_thesis"),
                status="OPEN",
                result=None,
                exit_reason=None,
                reject_reason=trade_data.get("reject_reason"),
                instrument=trade_data.get("instrument", "OPTION" if trade_data.get("option_type") in ["CALL", "PUT"] else "STOCK"),
                is_0dte=1 if trade_data.get("is_0dte") else 0,
                pnl=0.0,
                pnl_pct=0.0,
                created_at=datetime.now(timezone.utc),
                closed_at=None
            )
            async with self.async_session() as session:
                async with session.begin():
                    session.add(entry)
            logger.info(f"💾 Trade position opened and recorded: {trade_id} ({symbol} {side} @ ${entry_price:.2f})")
            return {
                "trade_id": trade_id,
                "symbol": symbol,
                "underlying": underlying,
                "option_type": entry.option_type,
                "strike_price": entry.strike_price,
                "expiration": entry.expiration,
                "contract_symbol": entry.contract_symbol,
                "side": side,
                "entry_price": entry_price,
                "exit_price": None,
                "stop_loss": entry.stop_loss,
                "take_profit": entry.take_profit,
                "tp1": entry.tp1,
                "tp2": entry.tp2,
                "trailing_stop": entry.trailing_stop,
                "quantity": entry.quantity,
                "score": entry.score,
                "direction_score": entry.direction_score,
                "direction_edge": entry.direction_edge,
                "confidence": entry.confidence,
                "strategy": entry.strategy,
                "entry_thesis": entry.entry_thesis,
                "instrument": entry.instrument,
                "is_0dte": bool(entry.is_0dte),
                "status": "OPEN",
                "result": None,
                "exit_reason": None,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "created_at": entry.created_at.isoformat(),
                "closed_at": None
            }
        except Exception as e:
            logger.error(f"Error saving trade position: {e}")
            return {}

    async def get_trades(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Retrieve real executed trades from trade_logs table."""
        try:
            async with self.async_session() as session:
                stmt = select(TradeLog).order_by(desc(TradeLog.id))
                if symbol:
                    clean_sym = symbol.strip().upper()
                    stmt = stmt.where(
                        (func.upper(TradeLog.symbol) == clean_sym) |
                        (func.upper(TradeLog.underlying) == clean_sym) |
                        (func.upper(TradeLog.contract_symbol) == clean_sym)
                    )
                if status:
                    clean_status = status.strip().upper()
                    stmt = stmt.where(TradeLog.status == clean_status)
                stmt = stmt.limit(limit)
                res = await session.execute(stmt)
                records = res.scalars().all()
                return [
                    {
                        "id": r.id,
                        "trade_id": r.trade_id,
                        "symbol": r.symbol,
                        "underlying": r.underlying or r.symbol,
                        "option_type": r.option_type,
                        "strike_price": r.strike_price,
                        "expiration": r.expiration,
                        "contract_symbol": r.contract_symbol,
                        "side": r.side,
                        "entry_price": r.entry_price,
                        "exit_price": r.exit_price,
                        "stop_loss": r.stop_loss,
                        "take_profit": r.take_profit,
                        "tp1": r.tp1,
                        "tp2": r.tp2,
                        "trailing_stop": r.trailing_stop,
                        "quantity": r.quantity,
                        "score": r.score,
                        "direction_score": r.direction_score,
                        "direction_edge": r.direction_edge,
                        "confidence": r.confidence,
                        "strategy": r.strategy,
                        "signal_type": r.signal_type,
                        "entry_thesis": r.entry_thesis,
                        "instrument": r.instrument or ("OPTION" if r.option_type in ["CALL", "PUT"] else "STOCK"),
                        "is_0dte": bool(r.is_0dte),
                        "status": r.status,
                        "result": r.result,
                        "exit_reason": r.exit_reason,
                        "pnl": r.pnl,
                        "pnl_pct": r.pnl_pct,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "closed_at": r.closed_at.isoformat() if r.closed_at else None
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"Error fetching trade history: {e}")
            return []

    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Retrieve all currently open positions."""
        return await self.get_trades(status="OPEN")

    async def get_open_trade_for_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get currently active open position for a given symbol or underlying."""
        try:
            clean = symbol.strip().upper()
            async with self.async_session() as session:
                stmt = select(TradeLog).where(
                    TradeLog.status == "OPEN",
                    ((func.upper(TradeLog.symbol) == clean) | 
                     (func.upper(TradeLog.underlying) == clean) |
                     (func.upper(TradeLog.contract_symbol) == clean))
                ).order_by(desc(TradeLog.id)).limit(1)
                res = await session.execute(stmt)
                r = res.scalars().first()
                if not r:
                    return None
                return {
                    "id": r.id,
                    "trade_id": r.trade_id,
                    "symbol": r.symbol,
                    "underlying": r.underlying,
                    "option_type": r.option_type,
                    "strike_price": r.strike_price,
                    "expiration": r.expiration,
                    "contract_symbol": r.contract_symbol,
                    "side": r.side,
                    "entry_price": r.entry_price,
                    "exit_price": r.exit_price,
                    "stop_loss": r.stop_loss,
                    "take_profit": r.take_profit,
                    "tp1": r.tp1 or r.take_profit,
                    "tp2": r.tp2,
                    "trailing_stop": r.trailing_stop or r.stop_loss,
                    "quantity": r.quantity,
                    "score": r.score,
                    "direction_score": r.direction_score,
                    "direction_edge": r.direction_edge,
                    "confidence": r.confidence,
                    "strategy": r.strategy,
                    "is_0dte": bool(r.is_0dte),
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
        except Exception as e:
            logger.error(f"Error fetching open trade for {symbol}: {e}")
            return None

    async def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "Exit signal confirmed",
        result: str = "CLOSED"
    ) -> Optional[Dict[str, Any]]:
        """Close an open trade position, record exit price, exit time, and calculate P&L."""
        try:
            async with self.async_session() as session:
                async with session.begin():
                    stmt = select(TradeLog).where(TradeLog.trade_id == trade_id).limit(1)
                    res = await session.execute(stmt)
                    trade = res.scalars().first()
                    if not trade:
                        logger.warning(f"Trade not found to close: {trade_id}")
                        return None
                    
                    trade.status = "CLOSED"
                    trade.exit_price = float(exit_price)
                    trade.closed_at = datetime.now(timezone.utc)
                    trade.exit_reason = exit_reason
                    trade.result = result

                    # Calculate P&L
                    qty = trade.quantity if trade.quantity else 1.0
                    multiplier = 100.0 if trade.option_type in ["CALL", "PUT"] else 1.0
                    if trade.side in ["LONG", "BUY"]:
                        raw_pnl = (trade.exit_price - trade.entry_price) * qty * multiplier
                        pnl_pct = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100.0 if trade.entry_price > 0 else 0.0
                    else:  # SHORT
                        raw_pnl = (trade.entry_price - trade.exit_price) * qty * multiplier
                        pnl_pct = ((trade.entry_price - trade.exit_price) / trade.entry_price) * 100.0 if trade.entry_price > 0 else 0.0

                    trade.pnl = round(raw_pnl, 2)
                    trade.pnl_pct = round(pnl_pct, 2)

                    logger.info(f"✅ Trade closed: {trade_id} ({trade.symbol}) | P&L: ${trade.pnl} ({trade.pnl_pct}%)")
                    return {
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "underlying": trade.underlying,
                        "option_type": trade.option_type,
                        "strike_price": trade.strike_price,
                        "expiration": trade.expiration,
                        "contract_symbol": trade.contract_symbol,
                        "side": trade.side,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "quantity": trade.quantity,
                        "status": "CLOSED",
                        "result": trade.result,
                        "exit_reason": trade.exit_reason,
                        "pnl": trade.pnl,
                        "pnl_pct": trade.pnl_pct,
                        "created_at": trade.created_at.isoformat() if trade.created_at else None,
                        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None
                    }
        except Exception as e:
            logger.error(f"Error closing trade {trade_id}: {e}")
            return None

    async def _resolve_market_exit_price(self, trade_obj: TradeLog) -> float:
        """Resolve current live market quote for an open position upon session close."""
        entry_p = float(trade_obj.entry_price or 0.0)
        opt_type = trade_obj.option_type
        contract_sym = trade_obj.contract_symbol or trade_obj.symbol
        underlying = trade_obj.underlying or trade_obj.symbol

        # 1. Option trade resolution
        if opt_type in ["CALL", "PUT"] or (contract_sym and ("C0" in str(contract_sym) or "P0" in str(contract_sym))):
            try:
                from data.polygon_client import PolygonOptionsClient
                poc = PolygonOptionsClient()
                if contract_sym and contract_sym != underlying:
                    quote = await asyncio.wait_for(poc.get_option_realtime_quote(contract_sym), timeout=2.0)
                    if quote:
                        bid = float(quote.get("bid_price", 0.0) or 0.0)
                        ask = float(quote.get("ask_price", 0.0) or 0.0)
                        last = float(quote.get("last_trade_price", 0.0) or 0.0)
                        mark = last if last > 0 else (bid if bid > 0 else ((bid + ask) / 2.0 if (bid + ask) > 0 else 0.0))
                        if mark > 0:
                            return round(mark, 2)
            except Exception as opt_err:
                logger.debug(f"Option exit quote fetch error for {contract_sym}: {opt_err}")

            # Option fallback: Intrinsic value from underlying spot
            try:
                if underlying:
                    import yfinance as yf
                    def _get_opt_spot():
                        t = yf.Ticker(underlying)
                        fi = getattr(t, 'fast_info', None)
                        return float(fi.last_price) if (fi and hasattr(fi, 'last_price') and fi.last_price) else None
                    spot = await asyncio.wait_for(asyncio.to_thread(_get_opt_spot), timeout=1.5)
                    if spot and trade_obj.strike_price:
                        strike = float(trade_obj.strike_price)
                        intrinsic = max(0.05, spot - strike) if opt_type == "CALL" else max(0.05, strike - spot)
                        return round(intrinsic, 2)
            except Exception:
                pass

        # 2. Equity trade resolution
        if underlying:
            try:
                import yfinance as yf
                def _get_spot():
                    t = yf.Ticker(underlying)
                    fi = getattr(t, 'fast_info', None)
                    if fi and hasattr(fi, 'last_price') and fi.last_price:
                        return round(float(fi.last_price), 2)
                    return None
                spot = await asyncio.wait_for(asyncio.to_thread(_get_spot), timeout=1.5)
                if spot:
                    return spot
            except Exception:
                pass

        return entry_p

    async def close_all_open_positions(
        self,
        exit_reason: str = "23:00 End-of-Session Forced Close",
        exit_dt: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Close ALL currently OPEN trade positions immediately.
        Enforces: At 23:00:00 GMT+3 (or normal session end), EVERY open position must be closed.
        Guarantees: OPEN POSITIONS = 0.
        Resolves real-time market quote to compute true realized P&L instead of 0% PnL.
        """
        closed_trades = []
        now_dt = exit_dt or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        try:
            # 1. First fetch open trades to resolve prices without holding open database lock
            resolved_prices = {}
            async with self.async_session() as session:
                stmt = select(TradeLog).where(TradeLog.status == "OPEN")
                res = await session.execute(stmt)
                open_trades_preview = res.scalars().all()
                if not open_trades_preview:
                    return []

                logger.warning(
                    f"🚨 Force Closing {len(open_trades_preview)} OPEN positions: {exit_reason}"
                )

                # Resolve live market price for each open position
                for ot in open_trades_preview:
                    if ot.exit_price is not None and ot.exit_price > 0:
                        resolved_prices[ot.trade_id] = ot.exit_price
                    else:
                        resolved_prices[ot.trade_id] = await self._resolve_market_exit_price(ot)

            # 2. Commit closures with resolved exit prices and calculate true P&L
            async with self.async_session() as session:
                async with session.begin():
                    stmt = select(TradeLog).where(TradeLog.status == "OPEN")
                    res = await session.execute(stmt)
                    open_trades = res.scalars().all()

                    for trade in open_trades:
                        trade.status = "CLOSED"
                        real_exit_p = resolved_prices.get(trade.trade_id)
                        if real_exit_p is None or real_exit_p <= 0:
                            real_exit_p = float(trade.entry_price or 0.0)

                        trade.exit_price = round(real_exit_p, 2)
                        trade.closed_at = now_dt
                        trade.exit_reason = exit_reason
                        trade.result = "CLOSED"

                        # Calculate realized P&L
                        qty = trade.quantity if trade.quantity else 1.0
                        multiplier = 100.0 if trade.option_type in ["CALL", "PUT"] else 1.0
                        if trade.side in ["LONG", "BUY"]:
                            raw_pnl = (trade.exit_price - trade.entry_price) * qty * multiplier
                            pnl_pct = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100.0 if trade.entry_price and trade.entry_price > 0 else 0.0
                        else:  # SHORT
                            raw_pnl = (trade.entry_price - trade.exit_price) * qty * multiplier
                            pnl_pct = ((trade.entry_price - trade.exit_price) / trade.entry_price) * 100.0 if trade.entry_price and trade.entry_price > 0 else 0.0

                        trade.pnl = round(raw_pnl, 2)
                        trade.pnl_pct = round(pnl_pct, 2)

                        closed_trades.append({
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "underlying": trade.underlying,
                            "option_type": trade.option_type,
                            "strike_price": trade.strike_price,
                            "expiration": trade.expiration,
                            "contract_symbol": trade.contract_symbol,
                            "side": trade.side,
                            "entry_price": trade.entry_price,
                            "exit_price": trade.exit_price,
                            "quantity": trade.quantity,
                            "status": "CLOSED",
                            "result": trade.result,
                            "exit_reason": trade.exit_reason,
                            "pnl": trade.pnl,
                            "pnl_pct": trade.pnl_pct,
                            "created_at": trade.created_at.isoformat() if trade.created_at else None,
                            "closed_at": trade.closed_at.isoformat() if trade.closed_at else None
                        })
                        logger.info(
                            f"🛑 Forced Closed: {trade.trade_id} ({trade.symbol} {trade.option_type or ''}) at ${trade.exit_price} | P&L: ${trade.pnl} ({trade.pnl_pct}%)"
                        )

            return closed_trades
        except Exception as e:
            logger.error(f"Error in close_all_open_positions: {e}")
            return []

    async def reconcile_stale_positions(self, dt: Optional[datetime] = None) -> int:
        """
        Reconcile and close any open positions that exist when trading session is closed.
        Called on server startup, watchdog loops, and background reconcilers.
        Guarantees: IF current_time >= 23:00 GMT+3 (or outside normal session), OPEN POSITIONS = 0.
        """
        try:
            from engine.market_hours import MarketSchedule
            is_open, session, reason = MarketSchedule.is_market_open(dt=dt)
            if not is_open:
                closed = await self.close_all_open_positions(
                    exit_reason="End-of-Session Auto-Reconcile",
                    exit_dt=dt
                )
                if closed:
                    logger.warning(
                        f"🛡️ Reconciled {len(closed)} stale open positions outside session ({session})."
                    )
                return len(closed)
            return 0
        except Exception as e:
            logger.error(f"Error reconciling stale positions: {e}")
            return 0

    # =========================================================================
    # PORTFOLIO STATE PERSISTENCE & ANALYTICS
    # =========================================================================
    async def get_portfolio_state(self) -> Dict[str, Any]:
        """Fetch or initialize the single persistent paper portfolio state."""
        try:
            async with self.async_session() as session:
                async with session.begin():
                    stmt = select(PortfolioState).order_by(desc(PortfolioState.id)).limit(1)
                    res = await session.execute(stmt)
                    state = res.scalars().first()
                    if not state:
                        state = PortfolioState(
                            starting_balance=100000.0,
                            available_cash=100000.0,
                            realized_pnl=0.0,
                            daily_pnl=0.0,
                            last_reset_date=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc)
                        )
                        session.add(state)
                        await session.flush()
                return {
                    "id": state.id,
                    "starting_balance": state.starting_balance,
                    "available_cash": state.available_cash,
                    "realized_pnl": state.realized_pnl,
                    "daily_pnl": state.daily_pnl,
                    "last_reset_date": state.last_reset_date.isoformat() if state.last_reset_date else None,
                    "updated_at": state.updated_at.isoformat() if state.updated_at else None
                }
        except Exception as e:
            logger.error(f"Error getting portfolio state: {e}")
            return {
                "starting_balance": 100000.0,
                "available_cash": 100000.0,
                "realized_pnl": 0.0,
                "daily_pnl": 0.0
            }

    async def update_portfolio_state(
        self,
        available_cash: float,
        realized_pnl: float,
        daily_pnl: float,
        starting_balance: Optional[float] = None
    ) -> bool:
        """Update persistent portfolio balance after trades open or close."""
        try:
            async with self.async_session() as session:
                async with session.begin():
                    stmt = select(PortfolioState).order_by(desc(PortfolioState.id)).limit(1)
                    res = await session.execute(stmt)
                    state = res.scalars().first()
                    if state:
                        state.available_cash = round(float(available_cash), 2)
                        state.realized_pnl = round(float(realized_pnl), 2)
                        state.daily_pnl = round(float(daily_pnl), 2)
                        if starting_balance is not None:
                            state.starting_balance = round(float(starting_balance), 2)
                        state.updated_at = datetime.now(timezone.utc)
                    else:
                        init_bal = round(float(starting_balance if starting_balance is not None else 100000.0), 2)
                        state = PortfolioState(
                            starting_balance=init_bal,
                            available_cash=round(float(available_cash), 2),
                            realized_pnl=round(float(realized_pnl), 2),
                            daily_pnl=round(float(daily_pnl), 2),
                            last_reset_date=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc)
                        )
                        session.add(state)
            return True
        except Exception as e:
            logger.error(f"Error updating portfolio state: {e}")
            return False

    async def update_starting_balance(self, new_balance: float, adjust_cash: bool = True) -> Dict[str, Any]:
        """Update starting balance and adjust cash accordingly in persistent state."""
        try:
            new_bal = round(float(new_balance), 2)
            async with self.async_session() as session:
                async with session.begin():
                    stmt = select(PortfolioState).order_by(desc(PortfolioState.id)).limit(1)
                    res = await session.execute(stmt)
                    state = res.scalars().first()
                    if state:
                        if adjust_cash:
                            diff = new_bal - state.starting_balance
                            state.available_cash = max(0.0, round(state.available_cash + diff, 2))
                        state.starting_balance = new_bal
                        state.updated_at = datetime.now(timezone.utc)
                        current_cash = state.available_cash
                        current_rpnl = state.realized_pnl
                        current_dpnl = state.daily_pnl
                    else:
                        state = PortfolioState(
                            starting_balance=new_bal,
                            available_cash=new_bal,
                            realized_pnl=0.0,
                            daily_pnl=0.0,
                            last_reset_date=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc)
                        )
                        session.add(state)
                        current_cash = new_bal
                        current_rpnl = 0.0
                        current_dpnl = 0.0
            logger.info(f"Updated persistent starting balance to ${new_bal:.2f} (cash: ${current_cash:.2f})")
            return {
                "starting_balance": new_bal,
                "available_cash": current_cash,
                "realized_pnl": current_rpnl,
                "daily_pnl": current_dpnl
            }
        except Exception as e:
            logger.error(f"Error updating starting balance: {e}")
            return {}

    async def reset_portfolio_state(self, starting_balance: float = 100000.0) -> Dict[str, Any]:
        """Resets the artificial paper balance and P&L history to starting amount."""
        try:
            async with self.async_session() as session:
                async with session.begin():
                    state = PortfolioState(
                        starting_balance=float(starting_balance),
                        available_cash=float(starting_balance),
                        realized_pnl=0.0,
                        daily_pnl=0.0,
                        last_reset_date=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    session.add(state)
            logger.info(f"🔄 Portfolio reset to ${starting_balance:.2f}")
            return {
                "starting_balance": starting_balance,
                "available_cash": starting_balance,
                "realized_pnl": 0.0,
                "daily_pnl": 0.0
            }
        except Exception as e:
            logger.error(f"Error resetting portfolio state: {e}")
            return {}

    async def save_rejected_signal(self, reject_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save a trade signal vetoed/rejected by Trade Engine for auditability."""
        try:
            symbol = str(reject_data.get("symbol", "UNKNOWN")).strip().upper()
            underlying = str(reject_data.get("underlying") or symbol).strip().upper()
            direction = str(reject_data.get("direction", "NEUTRAL")).strip().upper()
            entry = RejectedSignal(
                symbol=symbol,
                underlying=underlying,
                direction=direction,
                strategy=reject_data.get("strategy"),
                score=float(reject_data["score"]) if reject_data.get("score") is not None else None,
                confidence=float(reject_data["confidence"]) if reject_data.get("confidence") is not None else None,
                market_regime=reject_data.get("market_regime"),
                reason_code=reject_data.get("reason_code", "UNKNOWN_VETO"),
                reason_detail=reject_data.get("reason_detail") or str(reject_data.get("rejection_reasons", "")),
                timestamp=datetime.now(timezone.utc)
            )
            async with self.async_session() as session:
                async with session.begin():
                    session.add(entry)
            return {"success": True, "symbol": symbol, "reason_code": entry.reason_code}
        except Exception as e:
            logger.error(f"Error saving rejected signal: {e}")
            return {"success": False, "error": str(e)}

    async def get_recent_rejected_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent rejected/vetoed trade signals."""
        try:
            async with self.async_session() as session:
                stmt = select(RejectedSignal).order_by(desc(RejectedSignal.id)).limit(limit)
                res = await session.execute(stmt)
                records = res.scalars().all()
                return [
                    {
                        "id": r.id,
                        "symbol": r.symbol,
                        "underlying": r.underlying or r.symbol,
                        "direction": r.direction,
                        "strategy": r.strategy,
                        "score": r.score,
                        "confidence": r.confidence,
                        "market_regime": r.market_regime,
                        "reason_code": r.reason_code,
                        "reason_detail": r.reason_detail,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"Error fetching rejected signals: {e}")
            return []

    async def get_performance_analytics(self) -> Dict[str, Any]:
        """
        Calculate professional performance analytics across simulated trade history:
        Win Rate, Average Win, Average Loss, Profit Factor, Expectancy, Max Drawdown,
        and multi-dimensional breakdowns (Stock vs Option, CALL vs PUT, Strategy, 0DTE).
        """
        try:
            trades = await self.get_trades(limit=500)
            closed_trades = [t for t in trades if t.get("status") == "CLOSED"]
            total_closed = len(closed_trades)

            if total_closed == 0:
                return {
                    "total_trades": 0,
                    "closed_trades": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "expectancy": 0.0,
                    "total_realized_pnl": 0.0,
                    "average_win": 0.0,
                    "average_loss": 0.0,
                    "average_holding_time_str": "0m",
                    "max_drawdown": 0.0,
                    "breakdown": {
                        "by_instrument": {},
                        "by_direction": {},
                        "by_strategy": {},
                        "by_0dte": {}
                    }
                }

            wins = [t for t in closed_trades if (t.get("pnl") or 0.0) > 0]
            losses = [t for t in closed_trades if (t.get("pnl") or 0.0) < 0]
            breakevens = [t for t in closed_trades if (t.get("pnl") or 0.0) == 0]

            win_count = len(wins)
            loss_count = len(losses)
            win_rate = round((win_count / total_closed) * 100.0, 1)

            total_win_amt = sum(float(t.get("pnl") or 0.0) for t in wins)
            total_loss_amt = sum(abs(float(t.get("pnl") or 0.0)) for t in losses)
            net_pnl = round(total_win_amt - total_loss_amt, 2)

            avg_win = round(total_win_amt / win_count, 2) if win_count > 0 else 0.0
            avg_loss = round(total_loss_amt / loss_count, 2) if loss_count > 0 else 0.0

            profit_factor = round(total_win_amt / max(total_loss_amt, 1.0), 2)
            win_prob = win_count / total_closed
            loss_prob = loss_count / total_closed
            expectancy = round((win_prob * avg_win) - (loss_prob * avg_loss), 2)

            # Holding time calculation
            holding_seconds_list = []
            for t in closed_trades:
                if t.get("created_at") and t.get("closed_at"):
                    try:
                        c_dt = datetime.fromisoformat(t["created_at"])
                        x_dt = datetime.fromisoformat(t["closed_at"])
                        holding_seconds_list.append(abs((x_dt - c_dt).total_seconds()))
                    except Exception:
                        pass

            avg_holding_secs = int(sum(holding_seconds_list) / len(holding_seconds_list)) if holding_seconds_list else 0
            holding_hrs = avg_holding_secs // 3600
            holding_mins = (avg_holding_secs % 3600) // 60
            holding_str = f"{holding_hrs}h {holding_mins}m" if holding_hrs > 0 else f"{holding_mins}m"

            # Multi-dimensional breakdowns
            def _sub_stats(trade_list):
                if not trade_list:
                    return {"trades": 0, "win_rate": 0.0, "pnl": 0.0}
                w = [t for t in trade_list if (t.get("pnl") or 0.0) > 0]
                p = sum(float(t.get("pnl") or 0.0) for t in trade_list)
                return {
                    "trades": len(trade_list),
                    "win_rate": round((len(w) / len(trade_list)) * 100.0, 1),
                    "pnl": round(p, 2)
                }

            # By Instrument
            by_instrument = {
                "STOCK": _sub_stats([t for t in closed_trades if t.get("instrument") == "STOCK" or t.get("option_type") not in ["CALL", "PUT"]]),
                "OPTION": _sub_stats([t for t in closed_trades if t.get("instrument") == "OPTION" or t.get("option_type") in ["CALL", "PUT"]])
            }

            # By Direction
            by_direction = {
                "CALL": _sub_stats([t for t in closed_trades if t.get("option_type") == "CALL" or t.get("side") in ["LONG", "BUY"]]),
                "PUT": _sub_stats([t for t in closed_trades if t.get("option_type") == "PUT" or t.get("side") in ["SHORT", "SELL"]])
            }

            # By Strategy
            strategies = set(t.get("strategy") or "UNKNOWN" for t in closed_trades)
            by_strategy = {
                s: _sub_stats([t for t in closed_trades if (t.get("strategy") or "UNKNOWN") == s])
                for s in strategies
            }

            # By 0DTE
            by_0dte = {
                "0DTE": _sub_stats([t for t in closed_trades if t.get("is_0dte")]),
                "STANDARD": _sub_stats([t for t in closed_trades if not t.get("is_0dte")])
            }

            return {
                "total_trades": len(trades),
                "closed_trades": total_closed,
                "open_trades": len(trades) - total_closed,
                "win_count": win_count,
                "loss_count": loss_count,
                "breakeven_count": len(breakevens),
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "expectancy": expectancy,
                "total_realized_pnl": net_pnl,
                "average_win": avg_win,
                "average_loss": avg_loss,
                "average_holding_time_str": holding_str,
                "breakdown": {
                    "by_instrument": by_instrument,
                    "by_direction": by_direction,
                    "by_strategy": by_strategy,
                    "by_0dte": by_0dte
                }
            }
        except Exception as e:
            logger.error(f"Error calculating performance analytics: {e}")
            return {"error": str(e)}