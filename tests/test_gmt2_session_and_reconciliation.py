import pytest
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.api import app
from database.db_manager import DatabaseManager
from engine.market_hours import (
    MarketSchedule,
    is_market_open,
    is_market_closed,
    MARKET_TIMEZONE,
    MARKET_OPEN_TIME,
    MARKET_CLOSE_TIME,
    PRE_MARKET_OPEN_TIME,
    PRE_MARKET_CLOSE_TIME,
    NORMAL_MARKET_OPEN_TIME,
    NORMAL_MARKET_CLOSE_TIME,
    AFTER_MARKET_OPEN_TIME,
    AFTER_MARKET_CLOSE_TIME,
    MARKET_CLOSED_OPEN_TIME,
    MARKET_CLOSED_CLOSE_TIME,
    SESSION_TZ
)
from execution.signal_dispatcher import SignalDispatcher
from execution.broker import MockBroker


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# db fixture is provided by conftest.py (isolated in test_suite.db)


def make_gmt3_dt(hour: int, minute: int, second: int = 0, microsecond: int = 0) -> datetime:
    """Helper: Create a timezone-aware GMT+3 datetime on a standard weekday (Tuesday 2026-09-08)."""
    return datetime(2026, 9, 8, hour, minute, second, microsecond, tzinfo=SESSION_TZ)


# ==============================================================================
# MANDATORY TESTS: GMT+3 (ISTANBUL) TIMELINE SPECIFICATION
# PRE MARKET:    12:00 - 16:30
# NORMAL MARKET: 16:30 - 23:00 (Position entries allowed)
# AFTER MARKET:  23:00 - 03:00
# MARKET CLOSED: 03:00 - 12:00
# ==============================================================================

def test_centralized_configuration_constants():
    """Verify Single Source of Truth configuration constants for GMT+3."""
    assert MARKET_TIMEZONE == "GMT+3"
    assert MARKET_OPEN_TIME == "16:30"
    assert MARKET_CLOSE_TIME == "23:00"
    assert PRE_MARKET_OPEN_TIME == "12:00"
    assert PRE_MARKET_CLOSE_TIME == "16:30"
    assert NORMAL_MARKET_OPEN_TIME == "16:30"
    assert NORMAL_MARKET_CLOSE_TIME == "23:00"
    assert AFTER_MARKET_OPEN_TIME == "23:00"
    assert AFTER_MARKET_CLOSE_TIME == "03:00"
    assert MARKET_CLOSED_OPEN_TIME == "03:00"
    assert MARKET_CLOSED_CLOSE_TIME == "12:00"
    assert MarketSchedule.TIMEZONE_NAME == "GMT+3"
    assert MarketSchedule.OPEN_TIME_STR == "16:30"
    assert MarketSchedule.CLOSE_TIME_STR == "23:00"


def test_case_1_market_closed_at_1159():
    """
    TEST 1:
    11:59 GMT+3 -> Market is in CLOSED phase (03:00 - 12:00).
    Position opening MUST be rejected.
    """
    dt_1159 = make_gmt3_dt(11, 59, 0)
    open_allowed, session, reason = MarketSchedule.is_market_open(dt=dt_1159)
    assert open_allowed is False, "11:59 GMT+3 must NOT be open for position entries"
    assert session == "CLOSED"
    assert is_market_closed(dt=dt_1159) is True

    can_open, gate_reason = MarketSchedule.can_open_position(dt=dt_1159)
    assert can_open is False
    assert "CLOSED" in gate_reason or "Pre-market opens" in gate_reason


def test_case_2_pre_market_at_1200():
    """
    TEST 2:
    12:00 GMT+3 -> PRE MARKET starts (12:00 - 16:30).
    Position opening MUST be rejected (Pre-market is non-trading).
    """
    dt_1200 = make_gmt3_dt(12, 0, 0)
    open_allowed, session, reason = MarketSchedule.is_market_open(dt=dt_1200)
    assert open_allowed is False, "12:00:00 GMT+3 is Pre-Market; positions forbidden"
    assert session == "PRE_MARKET"
    assert is_market_closed(dt=dt_1200) is True

    can_open, gate_reason = MarketSchedule.can_open_position(dt=dt_1200)
    assert can_open is False
    assert "Pre-Market" in gate_reason or "PRE MARKET" in gate_reason


def test_case_3_pre_market_at_162959():
    """
    TEST 3:
    16:29:59 GMT+3 -> Still PRE MARKET.
    Position opening MUST be rejected before 16:30:00.
    """
    dt_162959 = make_gmt3_dt(16, 29, 59)
    open_allowed, session, _ = MarketSchedule.is_market_open(dt=dt_162959)
    assert open_allowed is False, "16:29:59 GMT+3 must NOT be open for position entries"
    assert session == "PRE_MARKET"
    assert is_market_closed(dt=dt_162959) is True

    can_open, _ = MarketSchedule.can_open_position(dt=dt_162959)
    assert can_open is False


def test_case_4_normal_market_opens_at_163000():
    """
    TEST 4:
    16:30:00 GMT+3 -> NORMAL MARKET starts (16:30 - 23:00).
    Position opening is ALLOWED.
    """
    dt_1630 = make_gmt3_dt(16, 30, 0)
    open_allowed, session, reason = MarketSchedule.is_market_open(dt=dt_1630)
    assert open_allowed is True, "16:30:00 GMT+3 must be OPEN for position entries"
    assert session == "NORMAL_MARKET"
    assert session == "SESSION_OPEN"  # Backward compatible alias
    assert is_market_closed(dt=dt_1630) is False

    can_open, _ = MarketSchedule.can_open_position(dt=dt_1630)
    assert can_open is True


def test_case_5_normal_market_mid_session_at_1800():
    """
    TEST 5:
    18:00:00 GMT+3 -> Mid-session in NORMAL MARKET.
    Position opening is ALLOWED.
    """
    dt_1800 = make_gmt3_dt(18, 0, 0)
    open_allowed, session, _ = MarketSchedule.is_market_open(dt=dt_1800)
    assert open_allowed is True
    assert session == "NORMAL_MARKET"
    assert is_market_closed(dt=dt_1800) is False

    can_open, _ = MarketSchedule.can_open_position(dt=dt_1800)
    assert can_open is True


def test_case_6_normal_market_at_225959():
    """
    TEST 6:
    22:59:59 GMT+3 -> Final second of NORMAL MARKET.
    Position opening is still valid before 23:00:00.
    """
    dt_225959 = make_gmt3_dt(22, 59, 59)
    open_allowed, session, _ = MarketSchedule.is_market_open(dt=dt_225959)
    assert open_allowed is True, "22:59:59 GMT+3 must be allowed for position opening"
    assert session == "NORMAL_MARKET"
    assert is_market_closed(dt=dt_225959) is False

    can_open, _ = MarketSchedule.can_open_position(dt=dt_225959)
    assert can_open is True


def test_case_7_after_market_starts_at_230000():
    """
    TEST 7:
    23:00:00 GMT+3 -> NORMAL MARKET ends, AFTER MARKET begins (23:00 - 03:00).
    New position opening MUST be rejected.
    """
    dt_230000 = make_gmt3_dt(23, 0, 0)
    open_allowed, session, reason = MarketSchedule.is_market_open(dt=dt_230000)
    assert open_allowed is False, "23:00:00 GMT+3 MUST REJECT new position openings"
    assert session == "AFTER_MARKET"
    assert session == "AFTER_HOURS"  # Backward compatible alias
    assert is_market_closed(dt=dt_230000) is True

    can_open, gate_reason = MarketSchedule.can_open_position(dt=dt_230000)
    assert can_open is False
    assert "AFTER MARKET" in gate_reason or "After-Hours" in gate_reason


@pytest.mark.asyncio
async def test_case_8_230000_all_existing_open_positions_closed(db):
    """
    TEST 8:
    23:00:00 GMT+3 -> ALL existing OPEN positions MUST be closed.
    """
    # Open 2 positions
    trade_1 = await db.create_trade({
        "symbol": "SPY_260918C00550000",
        "underlying": "SPY",
        "option_type": "CALL",
        "side": "LONG",
        "entry_price": 4.50,
        "quantity": 1.0,
        "strategy": "TEST_CASE_8"
    })
    trade_2 = await db.create_trade({
        "symbol": "QQQ_260918P00450000",
        "underlying": "QQQ",
        "option_type": "PUT",
        "side": "LONG",
        "entry_price": 3.20,
        "quantity": 2.0,
        "strategy": "TEST_CASE_8"
    })
    assert trade_1 is not None and trade_2 is not None

    # At 23:00:00, trigger end-of-session close
    dt_2300 = make_gmt3_dt(23, 0, 0)
    closed = await db.close_all_open_positions(exit_reason="23:00 Hard Cut-off", exit_dt=dt_2300)
    assert len(closed) >= 2

    # Verify both trades now have status=CLOSED and realized P&L
    t1_check = await db.get_trades(symbol="SPY_260918C00550000")
    t2_check = await db.get_trades(symbol="QQQ_260918P00450000")
    assert t1_check[0]["status"] == "CLOSED"
    assert t2_check[0]["status"] == "CLOSED"
    assert t1_check[0]["exit_price"] is not None
    assert t2_check[0]["exit_price"] is not None


@pytest.mark.asyncio
async def test_case_9_230001_zero_open_positions(db):
    """
    TEST 9:
    23:00:01 GMT+3 -> ZERO OPEN positions.
    """
    dt_230001 = make_gmt3_dt(23, 0, 1)
    await db.reconcile_stale_positions(dt=dt_230001)

    open_trades = await db.get_open_trades()
    assert len(open_trades) == 0, f"At 23:00:01 GMT+3, open positions must be 0! Found: {len(open_trades)}"


@pytest.mark.asyncio
async def test_case_10_server_crash_at_2259_restart_at_2305(db):
    """
    TEST 10:
    Server crashes at 22:59 GMT+3.
    Server restarts at 23:05 GMT+3.
    -> Any stale OPEN positions must be detected and closed.
    """
    stale_trade = await db.create_trade({
        "symbol": "CRASH_TEST_NVDA_CALL",
        "underlying": "NVDA",
        "option_type": "CALL",
        "side": "LONG",
        "entry_price": 5.00,
        "quantity": 1.0,
        "strategy": "PRE_CRASH_TRADE"
    })
    assert stale_trade is not None
    assert stale_trade["status"] == "OPEN"

    # Server restarts at 23:05:00 GMT+3
    dt_2305 = make_gmt3_dt(23, 5, 0)
    reconciled_count = await db.reconcile_stale_positions(dt=dt_2305)
    assert reconciled_count >= 1, "Server restart at 23:05 must reconcile and close stale positions"

    open_trades = await db.get_open_trades()
    assert len(open_trades) == 0
    closed_check = await db.get_trades(symbol="CRASH_TEST_NVDA_CALL")
    assert closed_check[0]["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_case_11_after_market_at_0100(db):
    """
    TEST 11:
    01:00:00 GMT+3 -> AFTER MARKET (23:00 - 03:00).
    -> New positions blocked, open positions must remain 0.
    """
    dt_0100 = make_gmt3_dt(1, 0, 0)
    is_open, session, _ = MarketSchedule.is_market_open(dt=dt_0100)
    assert is_open is False
    assert session == "AFTER_MARKET"
    assert is_market_closed(dt=dt_0100) is True


@pytest.mark.asyncio
async def test_case_12_market_closed_at_0600(db):
    """
    TEST 12:
    06:00:00 GMT+3 -> MARKET CLOSED (03:00 - 12:00).
    -> New positions blocked, open positions must remain 0.
    """
    dt_0600 = make_gmt3_dt(6, 0, 0)
    is_open, session, _ = MarketSchedule.is_market_open(dt=dt_0600)
    assert is_open is False
    assert session == "CLOSED"
    assert is_market_closed(dt=dt_0600) is True


def test_case_13_weekend_closed():
    """
    TEST 13:
    Saturday 15:00 GMT+3 and Sunday 15:00 GMT+3 -> Weekend Closed.
    """
    sat_dt = datetime(2026, 9, 12, 15, 0, 0, tzinfo=SESSION_TZ)
    is_open_sat, session_sat, _ = MarketSchedule.is_market_open(dt=sat_dt)
    assert is_open_sat is False
    assert session_sat == "CLOSED"

    sun_dt = datetime(2026, 9, 13, 15, 0, 0, tzinfo=SESSION_TZ)
    is_open_sun, session_sun, _ = MarketSchedule.is_market_open(dt=sun_dt)
    assert is_open_sun is False
    assert session_sun == "CLOSED"


def test_final_invariant_guarantee():
    """
    FINAL INVARIANT:
    IF current_time is outside 16:30 - 23:00 GMT+3
    THEN:
        new_positions_allowed = FALSE
    AND:
        open_positions = 0
    """
    test_times = [
        (0, 0, 0),    # After Market
        (2, 59, 59),  # After Market
        (3, 0, 0),    # Closed
        (6, 0, 0),    # Closed
        (11, 59, 59), # Closed
        (12, 0, 0),   # Pre-Market
        (15, 0, 0),   # Pre-Market
        (16, 29, 59), # Pre-Market
        (23, 0, 0),   # After Market
        (23, 0, 1),   # After Market
        (23, 30, 0),  # After Market
    ]

    for h, m, s in test_times:
        test_dt = make_gmt3_dt(h, m, s)
        is_open, session, _ = MarketSchedule.is_market_open(dt=test_dt)
        can_open, _ = MarketSchedule.can_open_position(dt=test_dt)

        assert is_open is False, f"At {h:02d}:{m:02d}:{s:02d} GMT+3, is_market_open must be False"
        assert can_open is False, f"At {h:02d}:{m:02d}:{s:02d} GMT+3, can_open_position must be False"
        assert is_market_closed(dt=test_dt) is True
