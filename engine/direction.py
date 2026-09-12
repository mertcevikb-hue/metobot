"""
Direction Engine - Independent Bullish and Bearish Directional Evaluation.
Computes Bullish Score, Bearish Score, and Directional Edge simultaneously.
Eliminates directional bias and single-score flip-flops.
"""
from typing import Dict, Any, Tuple


class BullishEngine:
    """Evaluates bullish evidence across trend, VWAP, momentum, structure, and volume."""

    @staticmethod
    def evaluate(f: Dict[str, Any]) -> Dict[str, Any]:
        if not f:
            return {"score": 0.0, "factors": {}, "confirmations": [], "warnings": []}

        price = f.get("price", 0.0)
        ema9 = f.get("ema9", price)
        ema21 = f.get("ema21", price)
        vwap = f.get("vwap", price)
        slope = f.get("ema_slope", 0.0)
        z_sep = f.get("z_ema_separation", 0.0)
        z_vwap = f.get("z_vwap_distance", 0.0)
        z_mom = f.get("z_momentum", 0.0)
        z_vol = f.get("z_volume", 0.0)
        rsi = f.get("rsi", 50.0)
        fvg = f.get("fvg_info", {})
        div = f.get("divergence", {})
        climax = f.get("climax", {})

        confirmations = []
        warnings = []
        score = 50.0  # neutral starting point

        # 1. EMA Structure & Trend Alignment
        ema_aligned = price >= ema9 >= ema21
        if ema_aligned:
            score += 15.0
            confirmations.append("Bullish EMA Alignment (Price >= 9 EMA >= 21 EMA)")
        elif price > ema9 and slope > 0:
            score += 8.0
        elif price < ema21:
            score -= 15.0
            warnings.append("Price below 21 EMA")

        # Slope & Separation
        score += max(min(slope * 15.0, 12.0), -12.0)
        score += max(min(z_sep * 15.0, 12.0), -12.0)

        # 2. VWAP Alignment
        if price > vwap:
            score += 12.0
            confirmations.append("Above Institutional VWAP")
        else:
            score -= 15.0
            warnings.append("Below VWAP")
        score += max(min(z_vwap * 10.0, 10.0), -10.0)

        # 3. Momentum & RSI
        if 50.0 < rsi <= 68.0:
            score += 10.0
            confirmations.append(f"Healthy Bullish RSI ({rsi:.1f})")
        elif rsi > 72.0:
            score -= 10.0
            warnings.append(f"RSI Overbought ({rsi:.1f}) - Exhaustion Risk")
        elif rsi < 45.0:
            score -= 12.0
        score += max(min(z_mom * 12.0, 10.0), -10.0)

        # 4. Volume Support
        if z_vol > 0.2 and z_mom > 0:
            score += 8.0
            confirmations.append(f"Volume Surge on Upside (Z: {z_vol:.2f})")
        elif z_vol < -0.5:
            score -= 5.0

        # 5. Market Structure & Smart Money Concepts (FVG, Climax, Divergence)
        if fvg.get("active") and fvg.get("type") == "BULLISH":
            score += 8.0
            confirmations.append("Unmitigated Bullish FVG Support below price")

        if div.get("bearish_divergence"):
            score -= 22.0
            warnings.append("Adverse Bearish Divergence detected (Buyer Exhaustion)")

        if climax.get("is_bearish_sweep"):
            score -= 20.0
            warnings.append("Bearish Liquidity Sweep (Pinbar Climax Trap)")

        final_score = max(0.0, min(100.0, round(score, 1)))
        return {
            "score": final_score,
            "confirmations": confirmations,
            "warnings": warnings,
            "ema_aligned": ema_aligned,
            "above_vwap": price > vwap
        }


class BearishEngine:
    """Evaluates bearish evidence across trend, VWAP, momentum, structure, and volume."""

    @staticmethod
    def evaluate(f: Dict[str, Any]) -> Dict[str, Any]:
        if not f:
            return {"score": 0.0, "factors": {}, "confirmations": [], "warnings": []}

        price = f.get("price", 0.0)
        ema9 = f.get("ema9", price)
        ema21 = f.get("ema21", price)
        vwap = f.get("vwap", price)
        slope = f.get("ema_slope", 0.0)
        z_sep = f.get("z_ema_separation", 0.0)
        z_vwap = f.get("z_vwap_distance", 0.0)
        z_mom = f.get("z_momentum", 0.0)
        z_vol = f.get("z_volume", 0.0)
        rsi = f.get("rsi", 50.0)
        fvg = f.get("fvg_info", {})
        div = f.get("divergence", {})
        climax = f.get("climax", {})

        confirmations = []
        warnings = []
        score = 50.0  # neutral starting point

        # 1. EMA Structure & Trend Alignment
        ema_aligned = price <= ema9 <= ema21
        if ema_aligned:
            score += 15.0
            confirmations.append("Bearish EMA Alignment (Price <= 9 EMA <= 21 EMA)")
        elif price < ema9 and slope < 0:
            score += 8.0
        elif price > ema21:
            score -= 15.0
            warnings.append("Price above 21 EMA")

        # Slope & Separation (inverse)
        score += max(min(-slope * 15.0, 12.0), -12.0)
        score += max(min(-z_sep * 15.0, 12.0), -12.0)

        # 2. VWAP Alignment
        if price < vwap:
            score += 12.0
            confirmations.append("Below Institutional VWAP")
        else:
            score -= 15.0
            warnings.append("Above VWAP")
        score += max(min(-z_vwap * 10.0, 10.0), -10.0)

        # 3. Momentum & RSI
        if 32.0 <= rsi < 50.0:
            score += 10.0
            confirmations.append(f"Healthy Bearish RSI ({rsi:.1f})")
        elif rsi < 28.0:
            score -= 10.0
            warnings.append(f"RSI Oversold ({rsi:.1f}) - Exhaustion Risk")
        elif rsi > 55.0:
            score -= 12.0
        score += max(min(-z_mom * 12.0, 10.0), -10.0)

        # 4. Volume Support on Breakdown
        if z_vol > 0.2 and z_mom < 0:
            score += 8.0
            confirmations.append(f"Volume Surge on Breakdown (Z: {z_vol:.2f})")
        elif z_vol < -0.5:
            score -= 5.0

        # 5. Market Structure & Smart Money Concepts (FVG, Climax, Divergence)
        if fvg.get("active") and fvg.get("type") == "BEARISH":
            score += 8.0
            confirmations.append("Unmitigated Bearish FVG Resistance above price")

        if div.get("bullish_divergence"):
            score -= 22.0
            warnings.append("Adverse Bullish Divergence detected (Seller Exhaustion)")

        if climax.get("is_bullish_sweep"):
            score -= 20.0
            warnings.append("Bullish Liquidity Sweep (Pinbar Bottom Trap)")

        final_score = max(0.0, min(100.0, round(score, 1)))
        return {
            "score": final_score,
            "confirmations": confirmations,
            "warnings": warnings,
            "ema_aligned": ema_aligned,
            "below_vwap": price < vwap
        }


class DirectionEngine:
    """
    Coordinates BullishEngine and BearishEngine simultaneously.
    Calculates Directional Edge = Bullish Score - Bearish Score.
    Decides whether a clear directional thesis exists or if NO TRADE should be declared.
    """

    @classmethod
    def evaluate(
        cls,
        features: Dict[str, Any],
        min_edge: float = 15.0,
        conflict_threshold: float = 55.0
    ) -> Dict[str, Any]:
        bull = BullishEngine.evaluate(features)
        bear = BearishEngine.evaluate(features)

        bull_score = bull["score"]
        bear_score = bear["score"]
        direction_edge = round(bull_score - bear_score, 1)

        # Evaluate Directional State
        is_conflict = (bull_score >= conflict_threshold and bear_score >= conflict_threshold)
        is_choppy = abs(direction_edge) < min_edge

        if is_conflict:
            bias = "NEUTRAL"
            decision_valid = False
            status_desc = "Directional Conflict: Simultaneous high Bullish and Bearish pressure (Market Chop/Divergence)."
        elif is_choppy:
            bias = "NEUTRAL"
            decision_valid = False
            status_desc = f"Insufficient Directional Edge ({direction_edge:+.1f} vs required ±{min_edge:.1f})."
        elif direction_edge >= min_edge:
            bias = "BULLISH"
            decision_valid = True
            status_desc = f"Confirmed Bullish Advantage (+{direction_edge:.1f} Edge)."
        else:
            bias = "BEARISH"
            decision_valid = True
            status_desc = f"Confirmed Bearish Advantage ({direction_edge:.1f} Edge)."

        return {
            "bias": bias,
            "bullish_score": bull_score,
            "bearish_score": bear_score,
            "direction_edge": direction_edge,
            "is_valid": decision_valid,
            "is_conflict": is_conflict,
            "status_description": status_desc,
            "bull_confirmations": bull["confirmations"],
            "bull_warnings": bull["warnings"],
            "bear_confirmations": bear["confirmations"],
            "bear_warnings": bear["warnings"]
        }
