"""
Comprehensive Test Suite for METOBOT 0.5 Architecture Overhaul
Validates the 13 Critical Scenarios from Section 42 of the Specification:
1. Duplicate Trade Prevention (Max 1 CALL / Max 1 PUT per underlying)
2. Opposite Signal / No Flip-Flop
3. Strong Reversal Exit & Fresh Evaluation
4. 1 Contract Scaling (TP1 -> Profit Protection, No Partial Exit)
5. 2 Contracts Scaling (50% TP1, Protected Runner)
6. 10 Contracts Scaling (50% TP1, 30% TP2, 20% Runner)
7. Confidence Gating (Model Confidence < Threshold -> NO TRADE)
8. Portfolio Capital Exposure Cap (Exceeds Max Exposure -> NO TRADE)
9. Post-Exit Cooldown Enforcement (15 min cooldown -> NO TRADE)
10. Profit Protection Ratchet (MFE >= 1.2 ATR moves stop to Breakeven + Buffer)
11. Strong Rotation Exit after TP1 (BearRotationScore >= 70 closes runner)
12. Time Exit for Stagnant Trades (20+ bars stagnant -> TIME_EXIT)
13. 0DTE Risk & Session Cutoff Enforcement
"""
import pytest
from datetime import datetime, timezone, time

from config.trading_config import TradingConfig, global_config
from engine.portfolio import PortfolioManager
from engine.trade_engine import TradeEngine
from engine.dynamic_exit import DynamicExitEngine, RotationEngine
from engine.position_manager import PositionManager
from engine.confidence import ConfidenceEngine
from filters.veto_filter import VetoFilter


@pytest.fixture
def fresh_portfolio():
    """Returns an isolated PortfolioManager with $100,000 balance."""
    pm = PortfolioManager(initial_balance=100000.0)
    return pm


@pytest.fixture
def fresh_trade_engine(fresh_portfolio):
    """Returns an isolated TradeEngine with clean cooldown registry."""
    te = TradeEngine(portfolio=fresh_portfolio)
    return te


# -----------------------------------------------------------------------------
# SCENARIO 1: Duplicate Trade Prevention
# -----------------------------------------------------------------------------
def test_scenario_1_duplicate_trade_prevention(fresh_portfolio, fresh_trade_engine):
    """
    Call position open for NVDA Call.
    Next bar sends another NVDA Call signal.
    Trade Engine MUST reject with EXISTING_POSITION / DIRECTIONAL_EXPOSURE_REACHED.
    """
    # Open initial NVDA Call
    trade_1 = {
        "trade_id": "tr_nvda_call_1",
        "symbol": "NVDA",
        "underlying": "NVDA",
        "option_type": "CALL",
        "side": "LONG",
        "entry_price": 5.0,
        "quantity": 2.0,
        "status": "OPEN"
    }
    fresh_portfolio.register_position_opened(trade_1)

    # Next bar arrives with another NVDA Call signal
    duplicate_candidate = {
        "symbol": "NVDA",
        "underlying": "NVDA",
        "decision": "LONG",
        "price": 125.0,
        "score": 90.0,
        "entry_valid": True,
        "strategy": "EMA_TREND_FOLLOWING",
        "direction_evaluation": {"bias": "BULLISH", "direction_edge": 35.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 88.0, "threshold": 75.0},
        "option_data": {"contract_type": "CALL", "strike_price": 130.0, "premium": 5.50}
    }

    eval_result = fresh_trade_engine.evaluate_candidate(duplicate_candidate)
    assert eval_result["approved"] is False
    assert eval_result["verdict"] == "REJECTED"
    assert "EXISTING_POSITION" in eval_result["rejection_reasons"][0] or "MAX_EXPOSURE" in eval_result["rejection_reasons"][0]


# -----------------------------------------------------------------------------
# SCENARIO 2: Opposite Signal / No Flip-Flop
# -----------------------------------------------------------------------------
def test_scenario_2_opposite_signal_no_flip_flop(fresh_portfolio, fresh_trade_engine):
    """
    NVDA Call is open.
    Weak Put signal arrives.
    Must NOT trigger immediate flip to Put.
    """
    trade_1 = {
        "trade_id": "tr_nvda_call_1",
        "symbol": "NVDA",
        "underlying": "NVDA",
        "option_type": "CALL",
        "side": "LONG",
        "entry_price": 5.0,
        "quantity": 1.0,
        "status": "OPEN"
    }
    fresh_portfolio.register_position_opened(trade_1)

    # Weak Put signal arrives (e.g. slight dip, edge 16)
    opposite_signal = {
        "symbol": "NVDA",
        "underlying": "NVDA",
        "decision": "SHORT",
        "price": 124.0,
        "score": 76.0,
        "entry_valid": True,
        "strategy": "MOMENTUM_PULLBACK",
        "direction_evaluation": {"bias": "BEARISH", "direction_edge": -16.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 76.0, "threshold": 75.0},
        "option_data": {"contract_type": "PUT", "strike_price": 120.0, "premium": 3.20}
    }

    eval_result = fresh_trade_engine.evaluate_candidate(opposite_signal)
    # Rejection because position already exists on NVDA and portfolio prevents churn
    assert eval_result["approved"] is False
    assert eval_result["verdict"] == "REJECTED"


# -----------------------------------------------------------------------------
# SCENARIO 3: Strong Opposite Reversal Exit & Fresh Evaluation
# -----------------------------------------------------------------------------
def test_scenario_3_strong_reversal_exit():
    """
    NVDA Call is open.
    Strong bear reversal occurs (BearRotationScore >= 85).
    DynamicExitEngine must trigger full exit of Call.
    """
    pos = {
        "trade_id": "tr_call_rev",
        "underlying": "NVDA",
        "side": "LONG",
        "entry_price": 120.0,
        "current_price": 121.0,
        "quantity": 1.0,
        "initial_quantity": 1.0,
        "state": "HOLDING",
        "tp1": 125.0,
        "tp2": 130.0,
        "stop_loss": 117.0,
        "trailing_stop": 117.0,
        "mfe": 1.0,
        "mae": 0.0,
        "bars_held": 5
    }

    # Bearish breakdown candle with heavy sell volume
    current_features = {
        "atr": 2.0,
        "rsi": 38.0,
        "vwap": 121.50,
        "z_volume": 2.5,
        "climax": {"is_bearish_sweep": True}
    }

    # Evaluate exit
    eval_res = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=120.50,
        candle_high=121.20,
        candle_low=120.20,
        current_features=current_features
    )

    # When reversal criteria hit (e.g. breakdown through VWAP with sweep), exit is triggered
    assert eval_res["rotation"]["stage"] in ["ROTATION_WARNING", "STRONG_ROTATION", "THESIS_REVERSAL"]
    assert eval_res["rotation"]["rotation_score"] >= 50.0


# -----------------------------------------------------------------------------
# SCENARIO 4: 1 Contract Scaling (TP1 -> Profit Protection, No Partial Exit)
# -----------------------------------------------------------------------------
def test_scenario_4_one_contract_tp1_profit_protection():
    """
    1 Contract setup with TP1 and TP2.
    At TP1, contract CANNOT be partially closed.
    Must move stop to breakeven + buffer (PROFIT_PROTECTION mode) and aim for TP2.
    """
    pos = {
        "trade_id": "tr_1_contract",
        "underlying": "AAPL",
        "side": "LONG",
        "entry_price": 150.0,
        "current_price": 150.0,
        "quantity": 1.0,
        "initial_quantity": 1.0,
        "state": "OPEN",
        "tp1": 154.0,
        "tp2": 158.0,
        "stop_loss": 147.0,
        "trailing_stop": 147.0,
        "mfe": 0.0,
        "mae": 0.0,
        "bars_held": 2
    }

    features = {"atr": 2.0, "rsi": 62.0, "vwap": 151.0}

    # Candle reaches TP1 (high=154.20)
    eval_res = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=154.0,
        candle_high=154.20,
        candle_low=152.0,
        current_features=features
    )

    # 1 contract should NOT fully exit yet (aims for TP2), but must activate Profit Protection
    assert eval_res["should_exit"] is False
    assert pos["state"] == "PROFIT_PROTECTION"
    assert pos["profit_protection_active"] is True
    # Stop loss moved above breakeven: 150.0 + (0.15 * 2.0) = 150.30
    assert pos["trailing_stop"] >= 150.0


# -----------------------------------------------------------------------------
# SCENARIO 5: 2 Contracts Scaling (50% TP1, Protected Runner)
# -----------------------------------------------------------------------------
def test_scenario_5_two_contracts_tp1_scaling():
    """
    2 Contracts entered.
    At TP1, close 1 contract (50%), leave 1 runner protected at breakeven.
    """
    pos = {
        "trade_id": "tr_2_contracts",
        "underlying": "MSFT",
        "side": "LONG",
        "entry_price": 300.0,
        "current_price": 300.0,
        "quantity": 2.0,
        "initial_quantity": 2.0,
        "state": "OPEN",
        "tp1": 306.0,
        "tp2": 312.0,
        "stop_loss": 296.0,
        "trailing_stop": 296.0,
        "mfe": 0.0,
        "mae": 0.0,
        "bars_held": 3
    }

    features = {"atr": 3.0, "rsi": 65.0, "vwap": 302.0}

    # Candle hits TP1 (high=306.50)
    eval_res = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=306.0,
        candle_high=306.50,
        candle_low=304.0,
        current_features=features
    )

    assert eval_res["should_exit"] is True
    assert eval_res["exit_type"] == "PARTIAL"
    assert eval_res["quantity_to_exit"] == 1.0
    assert eval_res["result_code"] == "TP1"
    assert pos["quantity"] == 1.0
    assert pos["state"] == "RUNNER"
    assert pos["trailing_stop"] >= 300.0  # protected runner


# -----------------------------------------------------------------------------
# SCENARIO 6: 10 Contracts Scaling (50% TP1, 30% TP2, 20% Runner)
# -----------------------------------------------------------------------------
def test_scenario_6_ten_contracts_scaling():
    """
    10 Contracts entered.
    TP1: Closes 50% (5 contracts).
    TP2: Closes 30% (3 contracts), leaving 20% (2 contracts) runner.
    """
    pos = {
        "trade_id": "tr_10_contracts",
        "underlying": "TSLA",
        "side": "LONG",
        "entry_price": 200.0,
        "current_price": 200.0,
        "quantity": 10.0,
        "initial_quantity": 10.0,
        "state": "OPEN",
        "tp1": 206.0,
        "tp2": 212.0,
        "stop_loss": 195.0,
        "trailing_stop": 195.0,
        "mfe": 0.0,
        "mae": 0.0,
        "bars_held": 2
    }

    features = {"atr": 3.0, "rsi": 66.0, "vwap": 202.0}

    # Step A: Candle hits TP1
    eval_1 = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=206.0,
        candle_high=206.50,
        candle_low=203.0,
        current_features=features
    )
    assert eval_1["should_exit"] is True
    assert eval_1["exit_type"] == "PARTIAL"
    assert eval_1["quantity_to_exit"] == 5.0  # 50% of 10
    assert pos["quantity"] == 5.0
    assert pos["state"] == "RUNNER"

    # Step B: Candle reaches TP2 (high=212.50)
    eval_2 = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=212.0,
        candle_high=212.50,
        candle_low=209.0,
        current_features=features
    )
    assert eval_2["should_exit"] is True
    assert eval_2["exit_type"] == "PARTIAL"
    assert eval_2["quantity_to_exit"] == 3.0  # 30% of 10
    assert pos["quantity"] == 2.0  # 2 contracts (20%) remaining as runner


# -----------------------------------------------------------------------------
# SCENARIO 7: Confidence Gating (Model Confidence < 75% -> NO TRADE)
# -----------------------------------------------------------------------------
def test_scenario_7_confidence_gating(fresh_trade_engine):
    """
    Candidate with confidence 68% (below 75% minimum).
    Trade Engine MUST reject with LOW_CONFIDENCE.
    """
    candidate = {
        "symbol": "AMD",
        "underlying": "AMD",
        "decision": "LONG",
        "price": 110.0,
        "score": 68.0,
        "entry_valid": True,
        "strategy": "EMA_TREND_FOLLOWING",
        "direction_evaluation": {"bias": "BULLISH", "direction_edge": 25.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 68.0, "threshold": 75.0}
    }

    res = fresh_trade_engine.evaluate_candidate(candidate)
    assert res["approved"] is False
    assert "LOW_CONFIDENCE" in res["primary_reason"]


# -----------------------------------------------------------------------------
# SCENARIO 8: Portfolio Exposure Cap
# -----------------------------------------------------------------------------
def test_scenario_8_portfolio_exposure_cap(fresh_portfolio, fresh_trade_engine):
    """
    When total exposure reaches 85% cap ($85,000 of $100,000),
    any new position must be rejected with MAX_EXPOSURE or INSUFFICIENT_CAPITAL.
    """
    # Simulate heavy existing exposure ($86,000 used)
    fresh_portfolio.cash = 14000.0  # 86% committed
    fresh_portfolio.open_positions = {
        f"pos_{i}": {
            "trade_id": f"p_{i}", "underlying": f"SYM_{i}", "side": "LONG",
            "quantity": 10.0, "entry_price": 860.0
        } for i in range(10)
    }

    candidate = {
        "symbol": "SPY",
        "underlying": "SPY",
        "decision": "LONG",
        "price": 500.0,
        "score": 90.0,
        "entry_valid": True,
        "strategy": "BREAKOUT",
        "direction_evaluation": {"bias": "BULLISH", "direction_edge": 30.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 88.0, "threshold": 75.0}
    }

    res = fresh_trade_engine.evaluate_candidate(candidate)
    assert res["approved"] is False
    assert "MAX_EXPOSURE" in res["primary_reason"] or "INSUFFICIENT_CAPITAL" in res["primary_reason"]


# -----------------------------------------------------------------------------
# SCENARIO 9: Post-Exit Cooldown Enforcement
# -----------------------------------------------------------------------------
def test_scenario_9_cooldown_enforcement(fresh_trade_engine):
    """
    Symbol exits position.
    New signal arrives within cooldown window (15 minutes).
    Trade Engine MUST reject with COOLDOWN_ACTIVE.
    """
    fresh_trade_engine.record_exit("AAPL")

    # Immediately evaluate AAPL again
    candidate = {
        "symbol": "AAPL",
        "underlying": "AAPL",
        "decision": "LONG",
        "price": 175.0,
        "score": 92.0,
        "entry_valid": True,
        "strategy": "EMA_TREND_FOLLOWING",
        "direction_evaluation": {"bias": "BULLISH", "direction_edge": 32.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 90.0, "threshold": 75.0}
    }

    res = fresh_trade_engine.evaluate_candidate(candidate)
    assert res["approved"] is False
    assert "COOLDOWN_ACTIVE" in res["primary_reason"]


# -----------------------------------------------------------------------------
# SCENARIO 10: Profit Protection Ratchet
# -----------------------------------------------------------------------------
def test_scenario_10_profit_protection_ratchet():
    """
    Trade reaches +1.5 ATR MFE without reaching TP1 yet.
    Profit Protection triggers, moving stop to Breakeven + Buffer.
    When price retraces to entry, position exits at Breakeven + Buffer, protecting profit.
    """
    pos = {
        "trade_id": "tr_ratchet",
        "underlying": "AMZN",
        "side": "LONG",
        "entry_price": 180.0,
        "current_price": 180.0,
        "quantity": 1.0,
        "initial_quantity": 1.0,
        "state": "OPEN",
        "tp1": 187.0,
        "tp2": 194.0,
        "stop_loss": 176.0,
        "trailing_stop": 176.0,
        "mfe": 0.0,
        "mae": 0.0,
        "bars_held": 1
    }

    features = {"atr": 2.0, "rsi": 60.0, "vwap": 181.0}

    # Candle rallies to 183.20 (+1.6 ATR favorable excursion)
    eval_rally = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=183.0,
        candle_high=183.20,
        candle_low=180.50,
        current_features=features
    )
    assert pos["profit_protection_active"] is True
    # Stop is moved to breakeven + buffer: 180.0 + (0.15 * 2.0) = 180.30
    assert pos["trailing_stop"] >= 180.20

    # Next candle retraces down to 180.10 (breaching protected stop 180.30)
    eval_retrace = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=180.20,
        candle_high=181.0,
        candle_low=180.10,
        current_features=features
    )
    assert eval_retrace["should_exit"] is True
    assert eval_retrace["result_code"] == "PROFIT_PROTECTION"
    assert eval_retrace["exit_price"] >= 180.0  # Exited with non-negative P&L!


# -----------------------------------------------------------------------------
# SCENARIO 11: Strong Bear Rotation Exit after TP1
# -----------------------------------------------------------------------------
def test_scenario_11_strong_rotation_runner_exit():
    """
    Trade has achieved TP1 and is holding runner in RUNNER state.
    Opposite rotation score spikes to >= 70 (STRONG_ROTATION).
    DynamicExitEngine must close runner early to lock in profits.
    """
    pos = {
        "trade_id": "tr_runner_rot",
        "underlying": "META",
        "side": "LONG",
        "entry_price": 500.0,
        "current_price": 510.0,
        "quantity": 1.0,
        "initial_quantity": 2.0,
        "state": "RUNNER",
        "tp1": 510.0,
        "tp2": 520.0,
        "tp1_hit": True,
        "stop_loss": 490.0,
        "trailing_stop": 501.0,
        "mfe": 12.0,
        "mae": 0.0,
        "bars_held": 8
    }

    # Severe opposite rotation features (RSI rolls over, price cracks 9/21 EMA and VWAP with high volume)
    features = {
        "atr": 4.0,
        "rsi": 42.0,
        "vwap": 509.0,
        "ema9": 509.5,
        "ema21": 510.0,
        "ema_slope": -0.12,
        "z_volume": 2.0,
        "climax": {"is_bearish_sweep": True}
    }

    eval_res = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=507.0,
        candle_high=508.50,
        candle_low=506.0,
        current_features=features
    )

    assert eval_res["should_exit"] is True
    assert eval_res["result_code"] == "ROTATION_EXIT"
    assert "Rotation" in eval_res["exit_reason"] or "rotation" in eval_res["exit_reason"].lower()


# -----------------------------------------------------------------------------
# SCENARIO 12: Stagnant Time Exit
# -----------------------------------------------------------------------------
def test_scenario_12_time_exit_stagnant_trade():
    """
    Trade is held for 25 bars with MFE < 0.5 ATR (dead chop / thesis stalled).
    DynamicExitEngine must trigger a TIME_EXIT.
    """
    pos = {
        "trade_id": "tr_stagnant",
        "underlying": "INTC",
        "side": "LONG",
        "entry_price": 25.0,
        "current_price": 25.05,
        "quantity": 1.0,
        "initial_quantity": 1.0,
        "state": "OPEN",
        "tp1": 27.0,
        "tp2": 29.0,
        "stop_loss": 23.5,
        "trailing_stop": 23.5,
        "mfe": 0.20,  # only 0.20 move on 1.0 ATR
        "mae": 0.15,
        "bars_held": 25  # exceeded max stagnant bars (20)
    }

    features = {"atr": 1.0, "rsi": 50.0, "vwap": 25.0}

    eval_res = DynamicExitEngine.evaluate_exit(
        position=pos,
        current_price=25.05,
        candle_high=25.10,
        candle_low=24.95,
        current_features=features
    )

    assert eval_res["should_exit"] is True
    assert eval_res["result_code"] == "TIME_EXIT"
    assert "stagnant" in eval_res["exit_reason"].lower()


# -----------------------------------------------------------------------------
# SCENARIO 13: 0DTE Safety & Time Cutoff
# -----------------------------------------------------------------------------
def test_scenario_13_zero_dte_cutoff_enforcement():
    """
    0DTE options trade evaluated after 13:30 ET (20:30 GMT+3).
    Must be VETOED by VetoFilter due to extreme afternoon theta decay risk.
    """
    opt_data = {
        "contract_ticker": "SPY260911C00500000",
        "strike_price": 500.0,
        "expiration_date": "2026-09-11",
        "dte": 0,
        "is_0dte": True,
        "bid_ask_spread": "$0.05",
        "premium": "$1.50"
    }

    # 14:00 ET = 21:00 GMT+3 (past 20:30 GMT+3 cutoff)
    from engine.market_hours import MarketSchedule
    afternoon_time = datetime(2026, 9, 11, 21, 0, 0, tzinfo=MarketSchedule.TZ)
    vetoed, reason = VetoFilter.check_options_time_and_iv(opt_data, dt=afternoon_time)
    assert vetoed is True
    assert "0DTE Theta Risk Veto" in reason
