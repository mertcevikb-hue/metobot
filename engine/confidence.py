"""
Confidence Engine - Multidimensional Model Confidence Decomposition.
Separates Direction Confidence, Setup Confidence, and Execution Confidence.
Eliminates opaque single scores and enforces configurable confidence gating.
"""
from typing import Dict, Any, Tuple, Optional
from config.trading_config import global_config


class ConfidenceEngine:
    """
    Computes three independent confidence tiers:
    1. Direction Confidence: Edge clarity, regime alignment, absence of conflict.
    2. Setup Confidence: Strategy trigger completeness and structural confluence.
    3. Execution Confidence: Spread friction, liquidity, volatility safety, and anti-chasing.
    """

    @classmethod
    def calculate_confidence(
        cls,
        direction_result: Dict[str, Any],
        strategy_result: Dict[str, Any],
        stock_result: Optional[Dict[str, Any]] = None,
        option_result: Optional[Dict[str, Any]] = None,
        is_vetoed: bool = False,
        min_threshold: float = None,
        confluence_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        threshold = min_threshold if min_threshold is not None else global_config.minimum_confidence

        # 1. Direction Confidence (0 - 100)
        edge = abs(float(direction_result.get("direction_edge", 0.0)))
        bias = direction_result.get("bias", "NEUTRAL")
        is_conflict = direction_result.get("is_conflict", False)

        if is_conflict:
            dir_conf = 20.0
        elif bias == "NEUTRAL":
            dir_conf = 35.0
        else:
            # Scale edge from [15, 60] -> [50, 98]
            dir_conf = min(50.0 + (edge * 0.90), 98.0)

        # 2. Setup Confidence (0 - 100)
        strat_score = float(strategy_result.get("score", 20.0))
        trigger_valid = bool(strategy_result.get("trigger_valid", False))
        confirmations_cnt = len(strategy_result.get("confirmations", []))

        if confluence_result:
            net_conf = float(confluence_result.get("net_confluence", strat_score))
            contradiction_pen = float(confluence_result.get("contradiction_penalty", 0.0))
            setup_conf = (strat_score * 0.35) + (net_conf * 0.45) + (confirmations_cnt * 4.0)
            if contradiction_pen > 0:
                setup_conf -= (contradiction_pen * 0.30)
        else:
            setup_conf = strat_score * 0.70 + (confirmations_cnt * 6.0)

        if not trigger_valid:
            setup_conf = min(setup_conf * 0.60, 45.0)
        setup_conf = max(5.0, min(98.0, setup_conf))

        # 3. Execution Confidence (0 - 100)
        exec_conf = 80.0
        if is_vetoed:
            exec_conf = 10.0
        else:
            if option_result and option_result.get("status") != "DATA_UNAVAILABLE":
                # Option-specific execution drag: spread friction
                spread_pct = float(option_result.get("spread_friction_pct", 5.0))
                if spread_pct > 10.0:
                    exec_conf -= (spread_pct - 10.0) * 3.0
                if option_result.get("is_0dte"):
                    exec_conf -= 10.0  # 0DTE time penalty
            elif stock_result:
                atr_pct = float(stock_result.get("atr_pct", 1.0))
                if atr_pct > 4.0:
                    exec_conf -= 15.0

        exec_conf = max(5.0, min(98.0, exec_conf))

        # Final Weighted Confidence (Direction: 40%, Setup: 35%, Execution: 25%)
        final_confidence = round(
            (dir_conf * 0.40) +
            (setup_conf * 0.35) +
            (exec_conf * 0.25),
            1
        )

        passes_gate = (final_confidence >= threshold) and not is_vetoed and (bias != "NEUTRAL")

        return {
            "model_confidence": final_confidence,
            "direction_confidence": round(dir_conf, 1),
            "setup_confidence": round(setup_conf, 1),
            "execution_confidence": round(exec_conf, 1),
            "threshold": threshold,
            "passes_threshold": passes_gate,
            "confidence_level": "HIGH" if final_confidence >= 85 else ("MODERATE" if final_confidence >= 75 else "LOW"),
            "confidence_label": "Model Confidence (Calibrated Quantitative Score)"
        }
