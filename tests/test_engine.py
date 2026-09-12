import pytest
import asyncio
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scoring.calculator import ScoreCalculator
from filters.veto_filter import VetoFilter
from risk.regime.detector import RegimeDetector

def test_score_calculator():
    calculator = ScoreCalculator()
    features = {
        "BOS_confirmation": 1.0,
        "OB_freshness": 1.0,
        "FVG_proximity": 1.0,
        "liquidity_sweep": 1.0,
        "HTF_trend_alignment": 1.0
    }
    weights = {
        "BOS_confirmation": 25.0,
        "OB_freshness": 20.0,
        "FVG_proximity": 15.0,
        "liquidity_sweep": 25.0,
        "HTF_trend_alignment": 15.0
    }
    score = calculator.compute(features, "trending_bull", weights)
    assert score > 0.0
    assert score <= 100.0

def test_veto_filter():
    veto = VetoFilter()
    tick_data = {"high": 70000, "low": 60000} # Anormal spread
    # Yüksek volatilite rejiminde veto etmeli
    is_vetoed = veto.is_vetoed(tick_data, "high_volatility")
    assert is_vetoed is True

def test_regime_detector():
    detector = RegimeDetector()
    regime = detector.detect({"close": 65000, "open": 64000})
    assert regime in ["trending_bull", "ranging", "high_volatility"]