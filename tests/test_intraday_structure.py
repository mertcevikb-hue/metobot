import pytest
from engine.features import FeatureEngine


def test_rvol_calculation():
    """Verify Relative Volume calculation against 20-period baseline."""
    # 20 bars of 100,000 volume, current bar is 250,000 -> RVOL = 2.5x
    volumes = [100000.0] * 20 + [250000.0]
    rvol = FeatureEngine.calculate_rvol(volumes, 20)
    assert rvol == 2.5

    # Low volume bar: 40,000 -> RVOL = 0.4x
    volumes[-1] = 40000.0
    rvol_low = FeatureEngine.calculate_rvol(volumes, 20)
    assert rvol_low == 0.4


def test_vwap_structure_reclaim_and_loss():
    """Verify VWAP Reclaim and VWAP Loss event detection."""
    # Scenario 1: VWAP Reclaim
    # Price was below VWAP, then crosses cleanly above with positive slope
    highs = [100.0, 99.5, 100.2, 101.5]
    lows = [98.5, 98.0, 98.8, 100.2]
    closes = [99.0, 98.5, 99.2, 101.2]
    volumes = [10000.0, 15000.0, 12000.0, 35000.0]

    struct = FeatureEngine.calculate_vwap_structure(highs, lows, closes, volumes, atr=1.5)
    assert struct["vwap"] > 0
    assert struct["reclaim"] is True
    assert struct["loss"] is False
    assert struct["vwap_distance"] > 0

    # Scenario 2: VWAP Loss
    # Price was above VWAP, then drops below VWAP
    highs = [102.0, 101.8, 101.5, 99.0]
    lows = [100.5, 100.2, 100.0, 97.5]
    closes = [101.5, 101.0, 100.5, 98.0]
    volumes = [10000.0, 12000.0, 15000.0, 40000.0]

    struct_loss = FeatureEngine.calculate_vwap_structure(highs, lows, closes, volumes, atr=1.5)
    assert struct_loss["loss"] is True
    assert struct_loss["reclaim"] is False
    assert struct_loss["vwap_distance"] < 0


def test_market_structure_opening_range_breakout():
    """Verify Opening Range ORH / ORL framework, breakout confirmation, and false breakouts."""
    # Build 30 bars of synthetic data
    highs = []
    lows = []
    closes = []
    volumes = []

    # 10 bars of previous session around 100
    for i in range(10):
        closes.append(100.0 + (i * 0.1))
        highs.append(100.5 + (i * 0.1))
        lows.append(99.5 + (i * 0.1))
        volumes.append(50000.0)

    # Opening Range: bars 11-15, range between 101.0 and 103.0
    for i in range(5):
        closes.append(102.0)
        highs.append(103.0)
        lows.append(101.0)
        volumes.append(50000.0)

    # Bullish Breakout: bar 16 with high volume (RVOL = 2.4x) breaking above ORH (103.0)
    closes.append(104.5)
    highs.append(104.8)
    lows.append(102.8)
    volumes.append(120000.0)

    ms = FeatureEngine.calculate_market_structure(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        atr=1.2,
        rvol=2.4,
        vwap=102.2
    )

    assert ms["orh"] == 103.0
    assert ms["orl"] == 101.0
    assert ms["or_size"] == 2.0
    assert ms["or_breakout"] is True
    assert ms["or_breakdown"] is False
    assert ms["pdh"] > 0
    assert ms["pdl"] > 0


def test_market_structure_false_breakout_detection():
    """Verify detection of false breakout above ORH when price fails and wicks back inside."""
    highs = [100.5] * 10 + [103.0] * 5 + [104.5]
    lows = [99.5] * 10 + [101.0] * 5 + [101.5]
    # Closes back below ORH (103.0) with long upper wick
    closes = [100.0] * 10 + [102.0] * 5 + [102.2]
    volumes = [50000.0] * 16

    ms = FeatureEngine.calculate_market_structure(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        atr=1.2,
        rvol=1.5,
        vwap=101.8
    )

    assert ms["or_false_breakout_bullish"] is True
    assert ms["or_breakout"] is False
