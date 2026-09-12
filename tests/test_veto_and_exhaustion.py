"""
Unit and regression tests for Metobot v0.5 Veto Filter,
Momentum Exhaustion, Divergence, and 0DTE/IV Crush Protection.
"""
import pytest
import math
import sys
import os
from datetime import datetime, timezone, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.features import FeatureEngine
from engine.quant_core import QuantEngine
from filters.veto_filter import VetoFilter


def test_fvg_calculation_and_distance():
    """Verify that Fair Value Gap (FVG) is detected and distance is computed in ATR."""
    # Create 5 candles with a clear bullish FVG between bar 0 and bar 2
    # Bar 0: high = 100.0
    # Bar 1: explosive move up (low = 100.5, high = 103.0)
    # Bar 2: low = 101.5 (low of bar 2 > high of bar 0 -> Bullish FVG [100.0, 101.5], mid = 100.75)
    highs = [100.0, 103.0, 104.0, 105.0, 108.0]
    lows = [98.0, 100.5, 101.5, 103.0, 106.0]
    closes = [99.5, 102.5, 103.5, 104.5, 107.5]
    volumes = [10000.0] * 5

    f = FeatureEngine.extract_features(highs, lows, closes, volumes)
    assert "fvg_info" in f
    fvg = f["fvg_info"]
    assert fvg["active"] is True
    assert fvg["type"] == "BULLISH"
    assert fvg["bottom"] == 104.0
    assert fvg["top"] == 106.0
    assert fvg["mid"] == 105.0

    # At bar 4, price is 107.5. Distance to mid (100.75) is ~6.75.
    assert f["fvg_distance_atr"] > 0
    assert "z_fvg_distance" in f


def test_cvd_series_and_divergence():
    """Verify Cumulative Volume Delta (CVD) and divergence detection."""
    # Simulate price making Higher High, but CVD and RSI making Lower High (Bearish Divergence)
    # 25 bars: initial base
    closes = [100.0 + i * 0.1 for i in range(15)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    volumes = [50000.0] * 15

    # First peak at bar 18
    for i in range(5):
        c = 102.0 + i * 0.8  # peaks around 105.2
        closes.append(c)
        highs.append(c + 0.6)
        lows.append(c - 0.4)
        volumes.append(100000.0)  # strong buyer volume

    # Pullback to 103.0
    for i in range(3):
        c = 104.5 - i * 0.7
        closes.append(c)
        highs.append(c + 0.4)
        lows.append(c - 0.4)
        volumes.append(30000.0)

    # Second peak: Price shoots to 106.5 (Higher High), but sellers dominate (closes near lows of bar, weak CVD)
    for i in range(4):
        c = 104.0 + i * 0.8
        closes.append(c)
        highs.append(c + 1.2)
        lows.append(c - 0.1)  # close is near low -> negative intra-bar delta
        volumes.append(25000.0)  # declining volume

    rsi_series = FeatureEngine.calculate_rsi_series(closes, 14)
    cvd_series = FeatureEngine.calculate_cvd_series(highs, lows, closes, volumes)
    assert len(rsi_series) == len(closes)
    assert len(cvd_series) == len(closes)

    div = FeatureEngine.detect_divergences(highs, lows, closes, rsi_series, cvd_series)
    assert "bearish_divergence" in div
    assert "bullish_divergence" in div


def test_climax_pinbar_detection():
    """Verify detection of liquidity sweep / pinbar traps at extreme highs."""
    atr = 2.0
    highs = [100.0, 101.0, 102.0, 103.0, 108.0]  # high spiked to 108
    lows = [98.0, 99.0, 100.0, 101.0, 103.0]
    closes = [99.5, 100.5, 101.5, 102.5, 103.5]  # closed near low, huge upper wick (108 - 103.5 = 4.5)
    volumes = [10000.0, 10000.0, 10000.0, 10000.0, 30000.0]  # high volume

    climax = FeatureEngine.detect_climax_candle(highs, lows, closes, volumes, atr)
    assert climax["is_climax"] is True
    assert climax["is_bearish_sweep"] is True
    assert climax["upper_wick_pct"] >= 45.0


def test_veto_filter_chasing_momentum():
    """Verify that VetoFilter rejects Long setups when price is > 2.2 ATR extended from FVG/Base."""
    features = {
        "fvg_distance_atr": 3.1,  # overextended (> 2.2)
        "z_vwap_distance": 1.2,
        "z_ema9_distance": 1.1,
        "divergence": {},
        "climax": {}
    }
    vetoed, reason = VetoFilter.check_chasing_distance(features, "LONG")
    assert vetoed is True
    assert "Chasing Momentum Veto" in reason


def test_veto_filter_bearish_divergence():
    """Verify that VetoFilter rejects Long setups when Bearish Divergence is present."""
    features = {
        "fvg_distance_atr": 1.0,  # healthy distance
        "z_vwap_distance": 0.5,
        "z_ema9_distance": 0.4,
        "divergence": {
            "bearish_divergence": True,
            "div_description": "Bearish RSI Divergence"
        },
        "climax": {}
    }
    vetoed, reasons = VetoFilter.check_divergence_exhaustion(features, "LONG")
    assert vetoed is True
    assert "Momentum Exhaustion Veto" in reasons


def test_veto_filter_0dte_afternoon_cutoff():
    """Verify that 0DTE options are vetoed after 13:30 ET (20:30 GMT+3) to avoid terminal theta bleed."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    option_data = {
        "expiration_date": today_str,
        "dte": 0,
        "spread_friction_pct": 4.0,
        "iv": 0.45
    }

    # Time at 21:00 GMT+3 (14:00 ET) -> within 0DTE danger zone
    dt_afternoon = datetime(2026, 9, 9, 18, 0, 0, tzinfo=timezone.utc)  # 18:00 UTC = 21:00 GMT+3
    vetoed, reason = VetoFilter.check_options_time_and_iv(option_data, dt=dt_afternoon)
    assert vetoed is True
    assert "0DTE Theta Risk Veto" in reason

    # Time at 17:00 GMT+3 (10:00 ET) -> early session, allowed
    dt_morning = datetime(2026, 9, 9, 14, 0, 0, tzinfo=timezone.utc)  # 14:00 UTC = 17:00 GMT+3
    vetoed, reason = VetoFilter.check_options_time_and_iv(option_data, dt=dt_morning)
    assert vetoed is False


def test_veto_filter_illiquid_option_and_iv_crush():
    """Verify that illiquid options (spread > 12%) and extreme IV (> 125%) are vetoed."""
    # Excessive spread
    opt_illiquid = {
        "expiration_date": "2026-09-18",
        "spread_friction_pct": 18.5,
        "iv": 0.50
    }
    vetoed, reason = VetoFilter.check_options_time_and_iv(opt_illiquid)
    assert vetoed is True
    assert "Illiquid Option Veto" in reason

    # Extreme IV crush risk
    opt_crush = {
        "expiration_date": "2026-09-18",
        "spread_friction_pct": 3.0,
        "iv": 1.45  # 145% IV
    }
    vetoed, reason = VetoFilter.check_options_time_and_iv(opt_crush)
    assert vetoed is True
    assert "IV Crush Veto" in reason


def test_quant_engine_intc_exhaustion_simulation():
    """
    Simulate the exact INTC problem:
    Price makes an explosive late-stage momentum run (30 bars),
    becoming heavily extended (> 3.0 ATR from FVG) with a top rejection sweep.
    QuantEngine must VETO the entry and declare NO TRADE.
    """
    base = 25.0
    closes, highs, lows, volumes = [], [], [], []
    
    # 25 bars: ascending base
    for i in range(25):
        p = base + (i * 0.15)
        closes.append(p)
        highs.append(p + 0.25)
        lows.append(p - 0.25)
        volumes.append(200000.0)

    # Bars 25-34: Parabolic exhaustion spike
    for i in range(10):
        p = closes[-1] + 0.65
        closes.append(p)
        highs.append(p + 0.5)
        lows.append(p - 0.2)
        volumes.append(500000.0 + i * 50000.0)

    # Bar 35: High wick sweep (pinbar rejection at top)
    top_p = closes[-1]
    closes.append(top_p - 0.5)
    highs.append(top_p + 1.8)  # swept highs
    lows.append(top_p - 0.6)
    volumes.append(1200000.0)  # volume spike

    res = QuantEngine.analyze_ticker("INTC", highs, lows, closes, volumes)
    assert res["is_vetoed"] is True
    assert res["entry_valid"] is False
    assert res["decision"] == "NO TRADE"
    assert len(res["veto_reasons"]) > 0
    print(f"\n✓ INTC Exhaustion Correctly Vetoed: {res['veto_reasons']}")
