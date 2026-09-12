"""
Quant Engine - Centralized Quantitative Orchestrator for Metobot 0.5.
Unifies Market Regime, Direction Engine, Strategy Selector, Stock Engine,
Option Engine, Confidence Decomposition, Risk Gate, and Trade Engine Veto.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from engine.data_guard import DataGuard
from engine.features import FeatureEngine
from engine.regime import RegimeEngine, StrategySelector
from engine.direction import DirectionEngine
from engine.stock_engine import StockEngine
from engine.option_engine import OptionEngine
from engine.confidence import ConfidenceEngine
from engine.risk_engine import RiskEngine
from engine.trade_engine import global_trade_engine
from engine.position_state import PositionStateMachine
from filters.veto_filter import VetoFilter


class QuantEngine:
    """Main algorithmic orchestration pipeline for equity & options analysis."""

    @classmethod
    def analyze_ticker(
        cls,
        symbol: str,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        current_position_state: str = "WATCH",
        option_data: Optional[Dict[str, Any]] = None,
        dt: Optional[datetime] = None,
        is_market_open: bool = True,
        market_reason: str = "Market Open"
    ) -> Dict[str, Any]:
        # 1. Data Integrity Gate
        valid, err = DataGuard.validate_ohlcv(closes, highs, lows, volumes)
        if not valid:
            return {
                "symbol": symbol,
                "regime": "UNKNOWN",
                "strategy": "NONE",
                "setup_score": 0.0,
                "entry_valid": False,
                "position_state": "INVALIDATED",
                "risk_level": "HIGH",
                "estimated_probability": None,
                "reasons": [],
                "warnings": [f"Data Gate Error: {err}"],
                "invalidation": "Corrupted or insufficient data feed.",
                "decision": "NO TRADE",
                "bullish_score": 0.0,
                "bearish_score": 0.0,
                "direction_edge": 0.0,
                "direction_bias": "NEUTRAL"
            }

        # 2. Continuous Technical Feature Extraction
        f = FeatureEngine.extract_features(highs, lows, closes, volumes)
        spot_price = float(f.get("price", closes[-1]))
        spot_atr = float(f.get("atr", 1.0))

        # 3. Market Regime Classification (8 explicit regimes)
        regime, trend_intensity = RegimeEngine.detect_regime(f)

        # 4. Direction Engine (Simultaneous Bullish and Bearish Scoring)
        dir_res = DirectionEngine.evaluate(f)
        direction_bias = dir_res.get("bias", "NEUTRAL")  # "BULLISH", "BEARISH", or "NEUTRAL"

        # 5. Modular Strategy Selector (10 targeted strategies)
        primary_strat = StrategySelector.select_best_strategy(f, regime, direction_bias)

        # 6. Instrument Specific Engines (Stock vs Option)
        stock_eval = StockEngine.evaluate(f, direction_bias, regime)
        opt_eval = None
        if option_data:
            opt_eval = OptionEngine.evaluate(
                spot_price=spot_price,
                spot_atr=spot_atr,
                direction_bias=direction_bias,
                option_quote=option_data.get("quote") or option_data,
                contract_meta=option_data.get("meta") or option_data
            )

        # 7. Risk Gate Assessment
        strat_side = primary_strat.get("side", "NEUTRAL")
        effective_side = strat_side if strat_side in ("LONG", "SHORT", "BUY", "SELL") else (
            "LONG" if direction_bias in ("BULLISH", "BUY", "LONG") else (
                "SHORT" if direction_bias in ("BEARISH", "SELL", "SHORT") else "LONG"
            )
        )
        risk = RiskEngine.evaluate_risk(spot_price, spot_atr, strat_side if strat_side in ("LONG", "SHORT") else effective_side)

        # 8. Institutional Veto Gate (Chasing, Divergence, Sweep, 0DTE/IV)
        is_vetoed, veto_reasons = VetoFilter.evaluate_veto(
            features=f,
            decision_side=effective_side,
            option_data=opt_eval or option_data,
            dt=dt
        )
        if is_vetoed:
            risk["approved"] = False
            risk["reason"] = "; ".join(veto_reasons)
            primary_strat["trigger_valid"] = False

        # 9. Confidence Engine Decomposition (Direction, Setup, Execution)
        conf_eval = ConfidenceEngine.calculate_confidence(
            direction_result=dir_res,
            strategy_result=primary_strat,
            stock_result=stock_eval,
            option_result=opt_eval,
            is_vetoed=is_vetoed
        )
        model_confidence = conf_eval["model_confidence"]

        # 10. Position State Transition
        state = PositionStateMachine.transition(
            current_position_state,
            regime,
            primary_strat["score"],
            primary_strat["trigger_valid"],
            risk["approved"]
        )

        # 11. Assemble Candidate Signal for Trade Engine Gatekeeper
        warnings = []
        if is_vetoed:
            for vr in veto_reasons:
                warnings.append(f"VETO: {vr}")
        if spot_atr / spot_price > 0.04:
            warnings.append(f"Elevated ATR ({spot_atr:.2f}) relative to price.")
        if f.get("rsi", 50.0) > 70.0 or f.get("rsi", 50.0) < 30.0:
            warnings.append(f"RSI extended ({f.get('rsi', 50.0):.1f}). Momentum warning.")
        if not risk["approved"] and primary_strat["active"] and not is_vetoed:
            warnings.append(risk.get("reason", "Risk gate rejected setup."))

        reasons = list(primary_strat.get("confirmations", []))
        if not reasons and not primary_strat["active"]:
            reasons.append(primary_strat.get("reason", "No valid setup criteria met."))

        entry_candidate = primary_strat["trigger_valid"] and risk["approved"] and not is_vetoed and (state == "ENTRY_READY")
        candidate_decision = strat_side if entry_candidate else "NO TRADE"

        candidate_payload = {
            "symbol": symbol,
            "underlying": symbol,
            "decision": candidate_decision,
            "price": spot_price,
            "score": primary_strat["score"],
            "strategy": primary_strat["strategy"],
            "direction_evaluation": dir_res,
            "confidence_evaluation": conf_eval,
            "risk_metrics": risk,
            "entry_valid": entry_candidate,
            "option_data": opt_eval or option_data,
            "is_vetoed": is_vetoed,
            "veto_reasons": veto_reasons
        }

        # 12. Trade Engine Institutional Evaluation (Gatekeeper with VETO authority)
        trade_eval = global_trade_engine.evaluate_candidate(
            candidate_signal=candidate_payload,
            is_market_open=is_market_open,
            market_reason=market_reason
        )

        final_decision = "NO TRADE"
        if trade_eval["approved"] and entry_candidate:
            final_decision = strat_side
        elif state == "HOLDING":
            final_decision = "HOLD"
        elif state in ("EXIT_READY", "EXIT_WARNING"):
            final_decision = "EXIT"

        return {
            "symbol": symbol,
            "price": spot_price,
            "regime": regime,
            "strategy": primary_strat["strategy"],
            "strategy_side": strat_side,
            "setup_score": primary_strat["score"],
            "entry_valid": trade_eval["approved"] and entry_candidate,
            "position_state": state,
            "risk_level": risk.get("risk_level", "LOW") if risk["approved"] else "HIGH",
            "risk_metrics": risk,
            "features": f,
            "is_vetoed": is_vetoed,
            "veto_reasons": veto_reasons,
            "estimated_probability": None,  # uncalibrated
            "model_confidence": model_confidence,
            "confidence_evaluation": conf_eval,
            "direction_evaluation": dir_res,
            "direction_bias": direction_bias,
            "bullish_score": dir_res["bullish_score"],
            "bearish_score": dir_res["bearish_score"],
            "direction_edge": dir_res["direction_edge"],
            "stock_evaluation": stock_eval,
            "option_evaluation": opt_eval,
            "trade_evaluation": trade_eval,
            "trade_verdict": trade_eval["verdict"],
            "decision_explanation": trade_eval["decision_explanation"],
            "reasons": reasons,
            "warnings": warnings,
            "invalidation": f"Price close beyond {risk.get('stop_loss', 'VWAP threshold')}",
            "decision": final_decision
        }