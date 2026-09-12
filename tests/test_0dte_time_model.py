import pytest
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from engine.market_hours import MarketSchedule, US_EASTERN_TZ, TRADING_MINUTES_PER_DAY
from engine.option_engine import OptionEngine


def test_market_schedule_us_session_and_progress():
    """Verify US Eastern time conversions, session progress fraction, and time-of-day buckets."""
    # Tuesday 2026-09-08 13:30:00 UTC = 09:30:00 ET (Session Open)
    dt_open = datetime(2026, 9, 8, 13, 30, 0, tzinfo=timezone.utc)
    et_open = MarketSchedule.get_us_market_time(dt_open)
    assert et_open.hour == 9 and et_open.minute == 30
    assert MarketSchedule.get_minutes_to_market_close(dt_open) == 390.0
    assert MarketSchedule.get_session_progress(dt_open) == 0.0
    assert MarketSchedule.get_fraction_of_trading_day_remaining(dt_open) == 1.0
    assert MarketSchedule.get_time_of_day_bucket(dt_open) == "OPEN"

    # Midday: 17:30:00 UTC = 13:30:00 ET (4 hours in, 2.5 hours remaining = 150 mins)
    dt_mid = datetime(2026, 9, 8, 17, 30, 0, tzinfo=timezone.utc)
    assert MarketSchedule.get_minutes_to_market_close(dt_mid) == 150.0
    assert abs(MarketSchedule.get_session_progress(dt_mid) - (240.0 / 390.0)) < 0.001
    assert MarketSchedule.get_time_of_day_bucket(dt_mid) == "MIDDAY"

    # Expiration Window: 19:45:00 UTC = 15:45:00 ET (15 mins remaining)
    dt_exp_window = datetime(2026, 9, 8, 19, 45, 0, tzinfo=timezone.utc)
    assert MarketSchedule.get_minutes_to_market_close(dt_exp_window) == 15.0
    assert MarketSchedule.get_time_of_day_bucket(dt_exp_window) == "EXPIRATION_WINDOW"

    # Closed: 20:00:00 UTC = 16:00:00 ET
    dt_closed = datetime(2026, 9, 8, 20, 0, 0, tzinfo=timezone.utc)
    assert MarketSchedule.get_minutes_to_market_close(dt_closed) == 0.0
    assert MarketSchedule.get_session_progress(dt_closed) == 1.0


def test_0dte_expected_move_time_decay():
    """
    Verify that 0DTE expected move calculation scales with square root of remaining time.
    10:00 ET (360 mins left) vs 15:00 ET (60 mins left) must produce fundamentally different moves.
    """
    spot = 500.0
    iv = 0.25

    # 10:00 ET (360 minutes remaining)
    em_morning = OptionEngine.calculate_expected_move(
        spot_price=spot,
        iv=iv,
        is_0dte=True,
        minutes_to_expiry=360.0,
        dte=0
    )

    # 15:00 ET (60 minutes remaining)
    em_afternoon = OptionEngine.calculate_expected_move(
        spot_price=spot,
        iv=iv,
        is_0dte=True,
        minutes_to_expiry=60.0,
        dte=0
    )

    # 360 mins vs 60 mins -> sqrt(360 / 60) = sqrt(6) ~ 2.45x
    ratio = em_morning["expected_move"] / em_afternoon["expected_move"]
    assert ratio > 2.0 and ratio < 2.8
    assert em_morning["expected_move"] > em_afternoon["expected_move"]
    assert em_morning["expected_move_high"] > spot
    assert em_afternoon["expected_move_low"] < spot


def test_0dte_greeks_gamma_and_theta_acceleration():
    """Verify that as expiration approaches on 0DTE, gamma accelerates and theta decays rapidly."""
    spot = 500.0
    strike = 500.0  # ATM
    iv = 0.20

    # At open (390 mins left)
    g_open = OptionEngine.estimate_greeks(
        spot=spot,
        strike=strike,
        dte=0,
        iv=iv,
        is_call=True,
        minutes_to_expiry=390.0
    )

    # Late session (30 mins left)
    g_late = OptionEngine.estimate_greeks(
        spot=spot,
        strike=strike,
        dte=0,
        iv=iv,
        is_call=True,
        minutes_to_expiry=30.0
    )

    # Gamma increases significantly near expiration for ATM contracts
    assert g_late["gamma"] > g_open["gamma"]
    # Hourly theta is calculated
    assert "theta_hourly" in g_late
    assert g_late["theta_hourly"] < 0


def test_0dte_liquidity_gating():
    """Verify that 0DTE contracts with high spread friction or during expiration window are rejected."""
    # Case 1: Healthy 0DTE contract at 10:30 ET
    dt_morning = datetime(2026, 9, 8, 14, 30, 0, tzinfo=timezone.utc)
    healthy_eval = OptionEngine.evaluate(
        spot_price=500.0,
        spot_atr=5.0,
        direction_bias="BULLISH",
        option_quote={
            "bid_price": 2.50,
            "ask_price": 2.55,
            "last_trade_price": 2.52,
            "implied_volatility": 0.22,
            "volume": 3500,
            "open_interest": 4200
        },
        contract_meta={
            "ticker": "O:SPY260908C00500000",
            "strike_price": 500.0,
            "expiration_date": "2026-09-08"
        },
        dt=dt_morning
    )
    assert healthy_eval["is_0dte"] is True
    assert healthy_eval["valid"] is True
    assert healthy_eval["is_liquid"] is True

    # Case 2: 0DTE with severe spread friction (15% spread) -> must be rejected
    wide_spread_eval = OptionEngine.evaluate(
        spot_price=500.0,
        spot_atr=5.0,
        direction_bias="BULLISH",
        option_quote={
            "bid_price": 2.00,
            "ask_price": 2.40,  # 18% spread
            "last_trade_price": 2.20,
            "implied_volatility": 0.25,
            "volume": 20,
            "open_interest": 50
        },
        contract_meta={
            "ticker": "O:SPY260908C00500000",
            "strike_price": 500.0,
            "expiration_date": "2026-09-08"
        },
        dt=dt_morning
    )
    assert wide_spread_eval["valid"] is False
    assert wide_spread_eval["is_liquid"] is False

    # Case 3: 0DTE in Expiration Window (15:45 ET) -> must be invalid for new entries
    dt_late = datetime(2026, 9, 8, 19, 45, 0, tzinfo=timezone.utc)
    late_eval = OptionEngine.evaluate(
        spot_price=500.0,
        spot_atr=5.0,
        direction_bias="BULLISH",
        option_quote={
            "bid_price": 2.50,
            "ask_price": 2.55,
            "last_trade_price": 2.52,
            "implied_volatility": 0.22,
            "volume": 3500,
            "open_interest": 4200
        },
        contract_meta={
            "ticker": "O:SPY260908C00500000",
            "strike_price": 500.0,
            "expiration_date": "2026-09-08"
        },
        dt=dt_late
    )
    assert late_eval["is_0dte"] is True
    assert late_eval["valid"] is False  # Blocked in expiration window!
