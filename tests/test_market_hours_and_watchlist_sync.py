import pytest
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.api import app
from database.db_manager import DatabaseManager
from engine.market_hours import MarketSchedule
from execution.signal_dispatcher import SignalDispatcher
from execution.broker import MockBroker
from bot2_live import Bot2LiveEngine


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# db fixture is provided by conftest.py (isolated in test_suite.db)


# ==========================================
# 1. MARKET SCHEDULE EVALUATION TESTS
# ==========================================

def test_market_hours_evaluation():
    """
    Verify that at 12:24:29 UTC (08:24:29 ET), US markets are strictly PRE_MARKET
    and cannot open positions, while regular trading hours (09:30 - 16:00 ET) are open.
    """
    ny_tz = ZoneInfo("America/New_York")
    
    # Case A: User's reported timestamp (12:24:29 UTC = 08:24:29 ET) on a Tuesday
    # 2026-09-08 is a Tuesday
    dt_pre_market = datetime(2026, 9, 8, 12, 24, 29, tzinfo=timezone.utc)
    is_open, session, reason = MarketSchedule.is_market_open("SPY", dt_pre_market)
    assert is_open is False, "US market must NOT be open at 08:24:29 ET"
    assert session == "PRE_MARKET"
    assert "Pre-Market" in reason

    # Case B: Regular Trading Hours (14:30 UTC = 10:30 ET)
    dt_rth = datetime(2026, 9, 8, 14, 30, 0, tzinfo=timezone.utc)
    is_open, session, reason = MarketSchedule.is_market_open("SPY", dt_rth)
    assert is_open is True, "US market must be open at 10:30 ET"
    assert session == "REGULAR_HOURS"

    # Case C: After Hours (21:00 UTC = 17:00 ET)
    dt_after_hours = datetime(2026, 9, 8, 21, 0, 0, tzinfo=timezone.utc)
    is_open, session, reason = MarketSchedule.is_market_open("SPY", dt_after_hours)
    assert is_open is False, "US market must NOT be open at 17:00 ET"
    assert session == "AFTER_HOURS"

    # Case D: Overnight Closed (03:00 UTC = 23:00 ET previous day)
    dt_closed = datetime(2026, 9, 8, 3, 0, 0, tzinfo=timezone.utc)
    is_open, session, reason = MarketSchedule.is_market_open("SPY", dt_closed)
    assert is_open is False
    assert session == "CLOSED"

    # Case E: Weekend (Saturday 2026-09-12)
    dt_weekend = datetime(2026, 9, 12, 15, 0, 0, tzinfo=timezone.utc)
    is_open, session, reason = MarketSchedule.is_market_open("SPY", dt_weekend)
    assert is_open is False
    assert session == "CLOSED"
    assert "Saturday" in reason


def test_bist_market_hours():
    """Verify Borsa Istanbul (.IS) hours (10:00 - 18:00 TRT)."""
    # 2026-09-08 08:00 UTC = 11:00 TRT (Open)
    dt_bist_open = datetime(2026, 9, 8, 8, 0, 0, tzinfo=timezone.utc)
    is_open, session, _ = MarketSchedule.is_market_open("THYAO.IS", dt_bist_open)
    assert is_open is True
    assert session == "REGULAR_HOURS"

    # 2026-09-08 06:00 UTC = 09:00 TRT (Closed before 10:00)
    dt_bist_closed = datetime(2026, 9, 8, 6, 0, 0, tzinfo=timezone.utc)
    is_open, session, _ = MarketSchedule.is_market_open("THYAO.IS", dt_bist_closed)
    assert is_open is False


# ==========================================
# 2. POSITION TIMESTAMP & DURATION FORMATTING
# ==========================================

def test_position_timestamp_formatting():
    """Verify format_position_timestamp handles ISO strings, datetimes, and durations."""
    # Entry at 09:30:00 ET (13:30:00 UTC), End 25 minutes later
    entry = datetime(2026, 9, 8, 13, 30, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 9, 8, 13, 55, 30, tzinfo=timezone.utc)

    info = MarketSchedule.format_position_timestamp(entry, closed)
    assert "16:30:00 GMT+3" in info["time_session"]
    assert "13:30:00 UTC" in info["time_utc"]
    assert info["duration_str"] == "25m 30s"
    assert info["is_closed"] is True

    # String ISO timestamp format
    info_str = MarketSchedule.format_position_timestamp("2026-09-08T13:30:00Z")
    assert "16:30:00 GMT+3" in info_str["time_session"]
    assert "13:30:00 UTC" in info_str["time_utc"]
    assert info_str["is_closed"] is False


# ==========================================
# 3. API MARKET STATUS & GATING TESTS
# ==========================================

def test_api_market_status(client):
    """Test GET /api/v1/market-status endpoint."""
    res = client.get("/api/v1/market-status?symbol=SPY")
    assert res.status_code == 200
    data = res.json()
    assert "session" in data
    assert "current_time_gmt3" in data
    assert "current_time_utc" in data
    assert "regular_hours" in data
    assert data["regular_hours"] == "16:30 - 23:00 GMT+3"


def test_trades_open_market_hours_rejection(client):
    """
    Ensure POST /api/v1/trades/open rejects trades when US market is closed,
    unless explicitly allowed with allow_outside_hours=True.
    """
    # Inspect current market state
    is_open, session, reason = MarketSchedule.is_market_open("SPY")

    trade_payload = {
        "symbol": "SPY_260918C00550000",
        "underlying": "SPY",
        "option_type": "CALL",
        "strike_price": 550.0,
        "expiration": "2026-09-18",
        "side": "LONG",
        "entry_price": 4.50,
        "stop_loss": 3.80,
        "take_profit": 6.20,
        "quantity": 1,
        "score": 88.0,
        "strategy": "MOMENTUM_BREAKOUT",
        "signal_type": "BUY"
    }

    if not is_open:
        # Should be rejected with 400 Bad Request
        res = client.post("/api/v1/trades/open", json=trade_payload)
        assert res.status_code == 400
        assert "Cannot open position" in res.json()["detail"]

        # With bypass flag, it must succeed for testing / paper overrides
        res_bypass = client.post("/api/v1/trades/open", json={**trade_payload, "allow_outside_hours": True})
        assert res_bypass.status_code == 200
        data = res_bypass.json()
        assert data["success"] is True
        trade_id = data["trade"]["trade_id"]

        # Clean up by closing trade
        client.post(f"/api/v1/trades/{trade_id}/close", json={"exit_price": 5.00, "exit_reason": "Test Cleanup"})


# ==========================================
# 4. MARKET METRICS ENRICHMENT TESTS
# ==========================================

@pytest.mark.asyncio
async def test_market_metrics_enriched_with_position_time(client, db, monkeypatch):
    """
    Ensure GET /api/v1/market-metrics returns open_position timing
    and market_status for symbols on the watchlist.
    """
    from engine.market_hours import MarketSchedule
    monkeypatch.setattr(MarketSchedule, "is_market_closed", classmethod(lambda cls, *args, **kwargs: False))

    # Ensure SPY is on the watchlist
    await db.add_symbol("SPY")

    # Create an open trade for SPY
    open_trade = await db.create_trade({
        "symbol": "SPY_TEST_METRICS_CALL",
        "underlying": "SPY",
        "option_type": "CALL",
        "side": "LONG",
        "entry_price": 545.0,
        "quantity": 1,
        "strategy": "TEST_STRAT"
    })
    assert open_trade is not None
    trade_id = open_trade["trade_id"]

    try:
        res = client.get("/api/v1/market-metrics")
        assert res.status_code == 200
        items = res.json()
        spy_item = next((i for i in items if i.get("symbol") == "SPY"), None)
        assert spy_item is not None, "SPY must be in market metrics"
        assert spy_item["has_open_position"] is True
        assert spy_item["open_position"] is not None
        assert "time_et" in spy_item["open_position"]
        assert "duration_str" in spy_item["open_position"]
        assert "market_status" in spy_item
    finally:
        # Close test trade
        await db.close_trade(trade_id, exit_price=546.0, exit_reason="Test Finished")


# ==========================================
# 5. BOT 2 DYNAMIC WATCHLIST SYNC TESTS
# ==========================================

@pytest.mark.asyncio
async def test_bot2_dynamic_watchlist_sync(db):
    """
    Verify Bot2LiveEngine.sync_watchlist_symbols() dynamically tracks
    watchlist items added/removed via Watchlist Terminal.
    """
    bot = Bot2LiveEngine(db=db)
    sym_a = "TESTA"
    sym_b = "TESTB"

    # Ensure clean state
    await db.remove_symbol(sym_a)
    await db.remove_symbol(sym_b)

    try:
        # Initial sync establishes initial baseline
        await bot.sync_watchlist_symbols()
        assert sym_a not in bot.symbols

        # Dynamically add sym_a to watchlist
        await db.add_symbol(sym_a)
        added, removed = await bot.sync_watchlist_symbols()
        assert sym_a in bot.symbols
        assert sym_a in added

        # Dynamically add sym_b
        await db.add_symbol(sym_b)
        added2, removed2 = await bot.sync_watchlist_symbols()
        assert sym_b in bot.symbols
        assert sym_b in added2
        assert len(removed2) == 0

        # Dynamically remove sym_a
        await db.remove_symbol(sym_a)
        added3, removed3 = await bot.sync_watchlist_symbols()
        assert sym_a not in bot.symbols
        assert sym_a in removed3
    finally:
        await db.remove_symbol(sym_a)
        await db.remove_symbol(sym_b)
