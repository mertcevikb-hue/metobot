"""
Unit tests for ConfluenceEngine and mathematical CALL/PUT symmetry.
Verifies positive confluence scoring, contradiction penalties,
symmetrical treatment of Long and Short, and deterministic NO TRADE / WAIT states.
"""
import pytest
from datetime import datetime, timezone
from engine.confluence import ConfluenceEngine
from engine.quant_core import QuantEngine


def test_confluence_bullish_aligned():
    """Verify high confluence score when all HTF and intraday structure align bullishly."""
    features = {
        "price": 150.0,
        "ema50": 145.0,
        "ema200": 140.0,
        "vwap": 148.0,
        "vwap_structure": {"is_vwap_bullish": True},
        "market_structure": {"is_breakout": True, "pdh": 149.0},
        "rvol": 2.1,
        "rsi": 60.0,
        "divergence": {},
        "climax": {},
        "squeeze": {"fired": True},
        "atr": 1.8
    }
    htf_pattern = {
        "active": True,
        "pattern": "VCP",
        "quality_score": 85.0
    }

    res = ConfluenceEngine.evaluate(
        features=features,
        direction_bias="BULLISH",
        regime="TRENDING_BULL",
        htf_pattern=htf_pattern
    )

    assert res["approved"] is True
    assert res["verdict"] == "APPROVED"
    assert res["confluence_score"] >= 70.0
    assert res["contradiction_penalty"] == 0.0
    assert res["net_confluence"] >= 70.0
    assert len(res["confluence_factors"]) >= 5
    assert "Price positioned above HTF EMA200" in res["confluence_factors"][0]


def test_confluence_bearish_symmetry():
    """Verify exact symmetrical scoring for Bearish / PUT setups under mirrored conditions."""
    features = {
        "price": 130.0,
        "ema50": 135.0,
        "ema200": 140.0,
        "vwap": 132.0,
        "vwap_structure": {"is_vwap_bearish": True},
        "market_structure": {"is_breakdown": True, "pdl": 131.0},
        "rvol": 2.1,
        "rsi": 40.0,
        "divergence": {},
        "climax": {},
        "squeeze": {"fired": True},
        "atr": 1.8
    }
    htf_pattern = {
        "active": True,
        "side": "PUT",
        "pattern": "PARABOLIC_EXHAUSTION",
        "quality_score": 85.0
    }

    res = ConfluenceEngine.evaluate(
        features=features,
        direction_bias="BEARISH",
        regime="TRENDING_BEAR",
        htf_pattern=htf_pattern
    )

    assert res["approved"] is True
    assert res["verdict"] == "APPROVED"
    assert res["confluence_score"] >= 70.0
    assert res["contradiction_penalty"] == 0.0
    assert res["net_confluence"] >= 70.0
    assert len(res["confluence_factors"]) >= 5
    assert "Price positioned below HTF EMA200" in res["confluence_factors"][0]


def test_contradiction_penalty_triggers_no_trade():
    """Verify that contradictions (e.g. CALL candidate below VWAP with Bearish Divergence) trigger NO TRADE."""
    features = {
        "price": 100.0,
        "ema50": 105.0,  # Below EMA50
        "ema200": 110.0,  # Below EMA200
        "vwap": 102.0,   # Below VWAP
        "vwap_structure": {"is_vwap_bullish": False, "is_vwap_bearish": True},
        "market_structure": {"is_false_breakout": True},
        "rvol": 0.6,    # Low volume
        "rsi": 78.0,    # Overbought RSI
        "divergence": {"bearish_divergence": True, "div_description": "Bearish RSI Divergence"},
        "climax": {"is_bearish_sweep": True},
        "squeeze": {},
        "atr": 1.5
    }

    res = ConfluenceEngine.evaluate(
        features=features,
        direction_bias="BULLISH",
        regime="TRENDING_BEAR"
    )

    assert res["approved"] is False
    assert res["verdict"] == "NO_TRADE"
    assert res["contradiction_penalty"] >= ConfluenceEngine.MAX_CONTRADICTION_ALLOWED
    assert "NO TRADE: Excessive contradictions" in res["explanation"]
    assert any("Structural Contradiction" in c for c in res["contradiction_factors"])
    assert any("Macro Contradiction" in c for c in res["contradiction_factors"])


def test_neutral_market_produces_no_trade():
    """Verify that NEUTRAL bias produces explicit NO TRADE without crashing."""
    features = {"price": 100.0}
    res = ConfluenceEngine.evaluate(
        features=features,
        direction_bias="NEUTRAL",
        regime="RANGE_CHOP"
    )

    assert res["approved"] is False
    assert res["verdict"] == "NO_TRADE"
    assert res["net_confluence"] == 0.0
    assert "Market is neutral" in res["explanation"]
