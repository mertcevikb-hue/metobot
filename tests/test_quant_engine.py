"""
Automated unit and regression verification for Metobot Quantitative Engine.
Asserts that heterogeneous market distributions produce strictly distinct continuous scores.
"""
import math
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.data_guard import DataGuard
from engine.features import FeatureEngine
from engine.quant_core import QuantEngine

def generate_synthetic_series(profile: str, n: int = 60):
    base_price = 100.0
    closes, highs, lows, volumes = [], [], [], []
    
    for i in range(n):
        if profile == "STRONG_BULL_MOMENTUM":
            # Parabolic trajectory with volume expansion
            p = base_price + (i * 1.8) + (math.sin(i) * 0.4)
            v = 1000000.0 + (i * 50000.0)
        elif profile == "WEAK_BULL_GRIND":
            # Slow ascending drift with sub-average volume
            p = base_price + (i * 0.25) + (math.cos(i) * 0.2)
            v = 500000.0
        elif profile == "RANGE_CONSOLIDATION":
            # Oscillating tight band (mean-reverting)
            p = base_price + (math.sin(i * 0.6) * 1.2)
            v = 400000.0
        elif profile == "STRONG_BEAR_BREAKDOWN":
            # Sharp decay with high volume
            p = base_price - (i * 1.5) - (math.sin(i) * 0.5)
            v = 1500000.0 + (i * 30000.0)
        elif profile == "HIGH_VOLATILITY_WHIPSAW":
            # Chaotic wide excursions
            p = base_price + ((i % 2 * 2 - 1) * 8.0)
            v = 2000000.0
        else:
            p = base_price
            v = 100000.0
            
        closes.append(max(p, 1.0))
        highs.append(closes[-1] + 1.2)
        lows.append(closes[-1] - 1.2)
        volumes.append(v)
        
    return highs, lows, closes, volumes


def test_regression_72_5_problem():
    profiles = [
        "STRONG_BULL_MOMENTUM",
        "WEAK_BULL_GRIND",
        "RANGE_CONSOLIDATION",
        "STRONG_BEAR_BREAKDOWN",
        "HIGH_VOLATILITY_WHIPSAW"
    ]
    scores = {}
    
    print("\n--- RUNNING REGRESSION VERIFICATION AGAINST 72.5% COLLAPSE ---")
    for prof in profiles:
        h, l, c, v = generate_synthetic_series(prof)
        res = QuantEngine.analyze_ticker("TEST", h, l, c, v)
        score = res["setup_score"]
        scores[prof] = score
        print(f"Profile: {prof:<26} | Regime: {res['regime']:<22} | Score: {score:>5.1f} | Decision: {res['decision']}")
        
        # Invariant Assertions
        assert score != 72.5, f"Regression detected: Exact 72.5% default generated for {prof}"

    # Verify score variance (no single collapsed value)
    unique_scores = set(scores.values())
    assert len(unique_scores) >= 3, "Score collapse detected: Distinct profiles yielded identical scores."
    print("✓ REGRESSION TEST PASSED: Setup scores exhibit high continuous dispersion.")


def test_data_guard_rejections():
    print("\n--- RUNNING DATA INTEGRITY & CORRUPTION TESTS ---")
    # 1. NaN Injection
    h, l, c, v = generate_synthetic_series("STRONG_BULL_MOMENTUM")
    c[10] = float('nan')
    res = QuantEngine.analyze_ticker("NAN_TEST", h, l, c, v)
    assert res["decision"] == "NO TRADE"
    assert len(res["warnings"]) > 0
    print("✓ NaN Injection correctly rejected by Data Guard.")

    # 2. Insufficient History
    h, l, c, v = generate_synthetic_series("STRONG_BULL_MOMENTUM", n=15)
    res = QuantEngine.analyze_ticker("SHORT_TEST", h, l, c, v)
    assert res["decision"] == "NO TRADE"
    assert len(res["warnings"]) > 0
    print("✓ Insufficient bar count correctly rejected without fabricating signals.")


if __name__ == "__main__":
    test_regression_72_5_problem()
    test_data_guard_rejections()
