"""
Unit tests for OptionStrategyLibrary.
Verifies multi-leg structure generation, exact Black-Scholes pricing,
risk-defined spread parameters, and 0DTE suitability gating.
"""
import pytest
from engine.option_strategy_library import OptionStrategyLibrary


def test_black_scholes_pricing_logic():
    """Verify Black-Scholes theoretical call and put pricing respects put-call parity properties."""
    spot = 100.0
    strike = 100.0
    t_years = 0.05  # ~18 days
    iv = 0.30

    c = OptionStrategyLibrary.black_scholes_price(spot, strike, t_years, iv, is_call=True)
    p = OptionStrategyLibrary.black_scholes_price(spot, strike, t_years, iv, is_call=False)

    assert c > 0
    assert p > 0
    # At-the-money options with low interest rate are close in price
    assert abs(c - p) < 1.0


def test_bull_call_spread_structure():
    """Verify Bull Call Spread creates defined risk 2-leg debit spread."""
    res = OptionStrategyLibrary.evaluate_strategy(
        strategy_name="BULL_CALL_SPREAD",
        spot_price=150.0,
        iv=0.35,
        expected_move=3.0,
        dte=0,
        is_0dte=True,
        primary_strike=150.0,
        primary_mid=1.20,
        spread_width=2.5
    )

    assert res["strategy"] == "BULL_CALL_SPREAD"
    assert res["bias"] == "BULLISH"
    assert len(res["legs"]) == 2
    assert res["legs"][0]["action"] == "BUY"
    assert res["legs"][0]["strike"] == 150.0
    assert res["legs"][1]["action"] == "SELL"
    assert res["legs"][1]["strike"] == 152.5
    assert res["net_debit"] > 0
    assert res["max_loss"] == round(res["net_debit"] * 100.0, 2)
    assert res["max_profit"] == round((2.5 - res["net_debit"]) * 100.0, 2)
    assert res["breakeven"][0] == round(150.0 + res["net_debit"], 2)


def test_bear_put_spread_structure():
    """Verify Bear Put Spread creates defined risk 2-leg debit spread."""
    res = OptionStrategyLibrary.evaluate_strategy(
        strategy_name="BEAR_PUT_SPREAD",
        spot_price=100.0,
        iv=0.40,
        expected_move=2.0,
        dte=3,
        is_0dte=False,
        primary_strike=100.0,
        primary_mid=1.10,
        spread_width=2.0
    )

    assert res["strategy"] == "BEAR_PUT_SPREAD"
    assert res["bias"] == "BEARISH"
    assert len(res["legs"]) == 2
    assert res["legs"][0]["action"] == "BUY"
    assert res["legs"][0]["strike"] == 100.0
    assert res["legs"][1]["action"] == "SELL"
    assert res["legs"][1]["strike"] == 98.0
    assert res["net_debit"] > 0
    assert res["breakeven"][0] == round(100.0 - res["net_debit"], 2)


def test_iron_condor_4_legs():
    """Verify Iron Condor creates 4-leg defined risk credit structure."""
    res = OptionStrategyLibrary.evaluate_strategy(
        strategy_name="IRON_CONDOR",
        spot_price=200.0,
        iv=0.55,
        expected_move=5.0,
        dte=7,
        is_0dte=False,
        spread_width=5.0
    )

    assert res["strategy"] == "IRON_CONDOR"
    assert len(res["legs"]) == 4
    assert res["net_credit"] > 0
    assert res["max_profit"] == round(res["net_credit"] * 100.0, 2)
    assert len(res["breakeven"]) == 2


def test_strategy_selector_routing():
    """Verify optimal strategy selection based on IV regime and 0DTE time."""
    # High IV with Bullish bias should prefer spread to mitigate crush
    strat1 = OptionStrategyLibrary.select_optimal_strategy(
        spot_price=100.0,
        direction_bias="BULLISH",
        regime="TRENDING_BULL",
        iv=0.60,
        expected_move=3.0,
        dte=0,
        is_0dte=True,
        time_bucket="AFTERNOON"
    )
    assert strat1 == "BULL_CALL_SPREAD"

    # Low IV morning session can take Long Call
    strat2 = OptionStrategyLibrary.select_optimal_strategy(
        spot_price=100.0,
        direction_bias="BULLISH",
        regime="TRENDING_BULL",
        iv=0.20,
        expected_move=1.5,
        dte=7,
        is_0dte=False,
        time_bucket="MORNING"
    )
    assert strat2 == "LONG_CALL"
