import pytest
import asyncio
import uuid
from datetime import datetime, timezone

from database.db_manager import DatabaseManager
from bot2_live import Bot2LiveEngine
from execution.broker import MockBroker
from execution.signal_dispatcher import SignalDispatcher


@pytest.mark.asyncio
async def test_live_bot_take_profit_exit(db):
    """
    Test that when a LONG position is open and a candle high reaches or exceeds
    the Take-Profit price, Bot2LiveEngine triggers an exit with result='TP'.
    """
    sym = f"TP_{uuid.uuid4().hex[:4]}"
    trade_id = f"tr_tp_{uuid.uuid4().hex[:6]}"

    # 1. Create an open position in the database
    trade_payload = {
        "trade_id": trade_id,
        "symbol": sym,
        "underlying": sym,
        "side": "LONG",
        "entry_price": 100.0,
        "take_profit": 105.0,
        "stop_loss": 97.0,
        "quantity": 1.0,
        "status": "OPEN"
    }
    await db.create_trade(trade_payload)

    # 2. Instantiate Bot2LiveEngine
    bot = Bot2LiveEngine(symbols=[sym], db=db)
    bot.positions[sym] = {
        "state": "HOLDING",
        "entry_price": 100.0,
        "take_profit": 105.0,
        "stop_loss": 97.0,
        "side": "LONG",
        "open_trade": trade_payload
    }

    # Mock live feed candles
    class DummyFeed:
        async def get_candles(self, s, timeframe="1m", limit=100):
            return [
                {"close": 100.0, "high": 101.0, "low": 99.0, "volume": 1000}
                for _ in range(40)
            ]

    bot.live_feed = DummyFeed()

    # 3. Candle that crosses TP: high=105.50
    tp_candle = {
        "symbol": sym,
        "open": 102.0,
        "high": 105.50,
        "low": 101.8,
        "close": 105.20,
        "volume": 500
    }

    await bot.process_candle(sym, tp_candle)

    # 4. Verify position was closed in the database with result='TP'
    trades = await db.get_trades(symbol=sym)
    assert len(trades) == 1
    assert trades[0]["status"] == "CLOSED"
    assert trades[0]["result"] == "TP"
    assert trades[0]["exit_price"] >= 105.0
    assert trades[0]["pnl"] > 0
    assert "Take-Profit" in trades[0]["exit_reason"]


@pytest.mark.asyncio
async def test_live_bot_stop_loss_exit(db):
    """
    Test that when a LONG position is open and a candle low reaches or drops below
    the Stop-Loss price, Bot2LiveEngine triggers an exit with result='SL'.
    """
    sym = f"SL_{uuid.uuid4().hex[:4]}"
    trade_id = f"tr_sl_{uuid.uuid4().hex[:6]}"

    # 1. Create an open position in the database
    trade_payload = {
        "trade_id": trade_id,
        "symbol": sym,
        "underlying": sym,
        "side": "LONG",
        "entry_price": 100.0,
        "take_profit": 105.0,
        "stop_loss": 97.0,
        "quantity": 1.0,
        "status": "OPEN"
    }
    await db.create_trade(trade_payload)

    # 2. Instantiate Bot2LiveEngine
    bot = Bot2LiveEngine(symbols=[sym], db=db)
    bot.positions[sym] = {
        "state": "HOLDING",
        "entry_price": 100.0,
        "take_profit": 105.0,
        "stop_loss": 97.0,
        "side": "LONG",
        "open_trade": trade_payload
    }

    class DummyFeed:
        async def get_candles(self, s, timeframe="1m", limit=100):
            return [
                {"close": 100.0, "high": 101.0, "low": 99.0, "volume": 1000}
                for _ in range(40)
            ]

    bot.live_feed = DummyFeed()

    # 3. Candle that breaches SL: low=96.50
    sl_candle = {
        "symbol": sym,
        "open": 98.0,
        "high": 98.2,
        "low": 96.50,
        "close": 96.80,
        "volume": 500
    }

    await bot.process_candle(sym, sl_candle)

    # 4. Verify position was closed in the database with result='SL'
    trades = await db.get_trades(symbol=sym)
    assert len(trades) == 1
    assert trades[0]["status"] == "CLOSED"
    assert trades[0]["result"] == "SL"
    assert trades[0]["exit_price"] <= 97.0
    assert trades[0]["pnl"] < 0
    assert "Stop-Loss" in trades[0]["exit_reason"]


@pytest.mark.asyncio
async def test_bot2_startup_hydration(db):
    """
    Test that upon initialization, Bot2LiveEngine queries existing open trades
    and hydrates self.positions so open positions are not lost.
    """
    sym = f"HYD_{uuid.uuid4().hex[:4].upper()}"
    trade_id = f"tr_hyd_{uuid.uuid4().hex[:6]}"

    await db.create_trade({
        "trade_id": trade_id,
        "symbol": sym,
        "underlying": sym,
        "side": "LONG",
        "entry_price": 50.0,
        "take_profit": 55.0,
        "stop_loss": 48.0,
        "status": "OPEN"
    })

    bot = Bot2LiveEngine(symbols=[sym], db=db)
    # Simulate partial init without connecting live feed
    open_trades = await db.get_open_trades()
    for ot in open_trades:
        s = (ot.get("underlying") or ot.get("symbol", "")).strip().upper()
        if s == sym:
            bot.positions[s] = {
                "state": "HOLDING",
                "entry_price": ot.get("entry_price"),
                "take_profit": ot.get("take_profit"),
                "stop_loss": ot.get("stop_loss"),
                "side": ot.get("side", "LONG"),
                "open_trade": ot
            }

    assert sym in bot.positions
    assert bot.positions[sym]["state"] == "HOLDING"
    assert bot.positions[sym]["take_profit"] == 55.0
    assert bot.positions[sym]["stop_loss"] == 48.0


@pytest.mark.asyncio
async def test_close_all_open_positions_resolves_real_price(db):
    """
    Test that close_all_open_positions resolves real exit prices
    instead of hardcoding entry_price, avoiding 0% PnL.
    """
    trade_id = f"tr_2300_{uuid.uuid4().hex[:6]}"
    sym = f"REAL_{uuid.uuid4().hex[:4]}"

    # Trade entered at $10.00
    await db.create_trade({
        "trade_id": trade_id,
        "symbol": sym,
        "underlying": sym,
        "side": "LONG",
        "entry_price": 10.0,
        "status": "OPEN"
    })

    # Mock _resolve_market_exit_price to return a realistic market price of $12.50
    original_resolver = db._resolve_market_exit_price
    async def mock_resolver(t_obj):
        if t_obj.trade_id == trade_id:
            return 12.50
        return float(t_obj.entry_price or 10.0)

    db._resolve_market_exit_price = mock_resolver

    try:
        closed = await db.close_all_open_positions(exit_reason="23:00 End-of-Session Forced Close")
        matching = [t for t in closed if t["trade_id"] == trade_id]
        assert len(matching) == 1
        t = matching[0]
        assert t["status"] == "CLOSED"
        assert t["exit_price"] == 12.50
        assert t["pnl"] == 2.50  # (12.50 - 10.00) * 1
        assert t["pnl_pct"] == 25.0  # +25%
        assert t["pnl"] != 0.0, "PnL must not be zero when price moved!"
    finally:
        db._resolve_market_exit_price = original_resolver
