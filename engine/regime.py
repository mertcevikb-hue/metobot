"""
Regime classification and modular Strategy Pool.
Provides 8 explicit market regimes and 10 targeted quantitative strategies.
"""
from typing import Dict, Any, Tuple, List, Optional
import math


class RegimeEngine:
    """
    Classifies market regime into 8 explicit market structures:
    - TRENDING_BULLISH
    - TRENDING_BEARISH
    - RANGING
    - HIGH_VOLATILITY
    - LOW_VOLATILITY
    - BREAKOUT
    - BREAKDOWN
    - CHAOTIC_UNCLEAR
    """

    @staticmethod
    def detect_regime(f: Dict[str, Any]) -> Tuple[str, float]:
        z_sep = float(f.get("z_ema_separation", 0.0))
        slope = float(f.get("ema_slope", 0.0))
        rsi = float(f.get("rsi", 50.0))
        compressed = bool(f.get("is_compressed", False))
        bb_width = float(f.get("bb_width", 0.02))
        atr = float(f.get("atr", 1.0))
        price = float(f.get("price", 1.0))
        z_mom = float(f.get("z_momentum", 0.0))
        z_vol = float(f.get("z_volume", 0.0))

        atr_pct = (atr / price) if price > 0 else 0.01
        trend_intensity = (z_sep * 0.4) + (slope * 0.4) + (((rsi - 50.0) / 50.0) * 0.2)

        # 1. High Volatility / Chaotic Whipsaw
        if atr_pct > 0.05 or bb_width > 0.08:
            if abs(slope) < 0.1 and abs(z_sep) < 0.2:
                return "CHAOTIC_UNCLEAR", trend_intensity
            return "HIGH_VOLATILITY", trend_intensity

        # 2. Breakout / Breakdown from Compression
        if compressed and z_vol > 0.35:
            if z_mom > 0.25:
                return "BREAKOUT", trend_intensity
            elif z_mom < -0.25:
                return "BREAKDOWN", trend_intensity

        # 3. Volatility Compression / Low Volatility
        if compressed or bb_width < 0.012:
            return "LOW_VOLATILITY", trend_intensity

        # 4. Trending Regimes
        if trend_intensity > 0.30 and rsi > 52.0:
            return "TRENDING_BULLISH", trend_intensity
        elif trend_intensity < -0.30 and rsi < 48.0:
            return "TRENDING_BEARISH", trend_intensity

        # 5. Ranging Market
        return "RANGING", trend_intensity


class StrategyEngine:
    """
    Modular Strategy Pool featuring 10 distinct quantitative strategies:
    1. EMA_TREND_FOLLOWING
    2. VWAP_RECLAIM
    3. VWAP_REJECTION
    4. MOMENTUM_PULLBACK
    5. BREAKOUT
    6. BREAKDOWN
    7. SQUEEZE_BREAKOUT
    8. MEAN_REVERSION
    9. SUPPORT_RESISTANCE_REVERSAL
    10. TREND_CONTINUATION
    """

    # 1. EMA Trend Following
    @staticmethod
    def evaluate_trend_following(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        if "BULL" not in regime and "BEAR" not in regime:
            return {"active": False, "score": 0.0, "side": "NEUTRAL", "strategy": "EMA_TREND_FOLLOWING", "reason": "Requires trending regime."}

        is_bull = "BULL" in regime
        z_sep = f["z_ema_separation"] if is_bull else -f["z_ema_separation"]
        z_vwap = f["z_vwap_distance"] if is_bull else -f["z_vwap_distance"]
        slope = f["ema_slope"] if is_bull else -f["ema_slope"]
        z_vol = max(f["z_volume"], -0.5)
        z_mom = f["z_momentum"] if is_bull else -f["z_momentum"]

        raw_score = (z_sep * 30.0) + (slope * 25.0) + (z_vwap * 15.0) + (z_mom * 20.0) + (z_vol * 10.0)

        # Anti-Chasing & Divergence Penalties
        fvg_dist = float(f.get("fvg_distance_atr", 0.0))
        if fvg_dist > 1.6:
            raw_score -= min((fvg_dist - 1.6) * 20.0, 40.0)

        div = f.get("divergence", {})
        if (is_bull and div.get("bearish_divergence")) or (not is_bull and div.get("bullish_divergence")):
            raw_score -= 30.0

        climax = f.get("climax", {})
        if (is_bull and climax.get("is_bearish_sweep")) or (not is_bull and climax.get("is_bullish_sweep")):
            raw_score -= 25.0

        score = max(min(raw_score + 50.0, 98.0), 5.0)
        trigger = (
            f["price"] > f["vwap"] and slope > 0.05 and f["price"] >= f["ema9"] and fvg_dist <= 2.2
        ) if is_bull else (
            f["price"] < f["vwap"] and slope < -0.05 and f["price"] <= f["ema9"] and fvg_dist <= 2.2
        )

        return {
            "strategy": "EMA_TREND_FOLLOWING",
            "active": True,
            "side": "LONG" if is_bull else "SHORT",
            "score": round(score, 1),
            "trigger_valid": trigger,
            "confirmations": [
                f"Trend Alignment (Z-Sep: {z_sep:.2f}, Slope: {slope:.2f})",
                f"VWAP Alignment (Z: {z_vwap:.2f})",
                f"Momentum Z: {z_mom:.2f}"
            ]
        }

    # 2. VWAP Reclaim (Bullish)
    @staticmethod
    def evaluate_vwap_reclaim(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        price = f["price"]
        vwap = f["vwap"]
        z_vwap = f["z_vwap_distance"]
        slope = f["ema_slope"]
        rsi = f["rsi"]

        # Price crossed back above VWAP from below with supportive momentum
        if 0.0 <= z_vwap <= 0.60 and price > vwap and slope >= -0.05 and 45.0 <= rsi <= 65.0:
            score = min(60.0 + (z_vwap * 30.0) + (max(f["z_volume"], 0) * 15.0), 95.0)
            return {
                "strategy": "VWAP_RECLAIM",
                "active": True,
                "side": "LONG",
                "score": round(score, 1),
                "trigger_valid": price > f["ema9"],
                "confirmations": [
                    f"Price reclaimed above VWAP (${vwap:.2f})",
                    f"RSI in accumulation zone ({rsi:.1f})",
                    "Supportive volume on reclaim"
                ]
            }
        return {"strategy": "VWAP_RECLAIM", "active": False, "score": 20.0, "side": "NEUTRAL", "reason": "No VWAP reclaim setup."}

    # 3. VWAP Rejection (Bearish)
    @staticmethod
    def evaluate_vwap_rejection(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        price = f["price"]
        vwap = f["vwap"]
        z_vwap = f["z_vwap_distance"]
        slope = f["ema_slope"]
        rsi = f["rsi"]

        # Price tested VWAP from below and got rejected
        if -0.60 <= z_vwap <= 0.0 and price < vwap and slope <= 0.05 and 35.0 <= rsi <= 55.0:
            score = min(60.0 + (abs(z_vwap) * 30.0) + (max(f["z_volume"], 0) * 15.0), 95.0)
            return {
                "strategy": "VWAP_REJECTION",
                "active": True,
                "side": "SHORT",
                "score": round(score, 1),
                "trigger_valid": price < f["ema9"],
                "confirmations": [
                    f"Price rejected below VWAP (${vwap:.2f})",
                    f"RSI in distribution zone ({rsi:.1f})",
                    "Bearish pressure confirmed at resistance"
                ]
            }
        return {"strategy": "VWAP_REJECTION", "active": False, "score": 20.0, "side": "NEUTRAL", "reason": "No VWAP rejection setup."}

    # 4. Momentum Pullback
    @staticmethod
    def evaluate_momentum_pullback(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        if "BULL" not in regime and "BEAR" not in regime:
            return {"strategy": "MOMENTUM_PULLBACK", "active": False, "score": 15.0, "side": "NEUTRAL", "reason": "Requires trending regime."}

        is_bull = "BULL" in regime
        price = f["price"]
        ema9 = f["ema9"]
        ema21 = f["ema21"]
        z_ema9 = f["z_ema9_distance"]
        rsi = f["rsi"]

        # Pullback into 9-21 EMA gap during healthy trend
        if is_bull and ema9 > ema21:
            in_pocket = ema21 <= price <= (ema9 + 0.3 * f["atr"])
            if in_pocket and 45.0 <= rsi <= 60.0:
                score = min(65.0 + (1.0 - abs(z_ema9)) * 25.0, 96.0)
                return {
                    "strategy": "MOMENTUM_PULLBACK",
                    "active": True,
                    "side": "LONG",
                    "score": round(score, 1),
                    "trigger_valid": price >= ema21,
                    "confirmations": [
                        "Healthy pullback into 9/21 EMA dynamic support pocket",
                        f"RSI reset to support ({rsi:.1f})"
                    ]
                }
        elif not is_bull and ema9 < ema21:
            in_pocket = (ema9 - 0.3 * f["atr"]) <= price <= ema21
            if in_pocket and 40.0 <= rsi <= 55.0:
                score = min(65.0 + (1.0 - abs(z_ema9)) * 25.0, 96.0)
                return {
                    "strategy": "MOMENTUM_PULLBACK",
                    "active": True,
                    "side": "SHORT",
                    "score": round(score, 1),
                    "trigger_valid": price <= ema21,
                    "confirmations": [
                        "Healthy pullback into 9/21 EMA dynamic resistance pocket",
                        f"RSI reset to resistance ({rsi:.1f})"
                    ]
                }

        return {"strategy": "MOMENTUM_PULLBACK", "active": False, "score": 20.0, "side": "NEUTRAL", "reason": "No pullback into EMA pocket."}

    # 5. Volatility Breakout
    @staticmethod
    def evaluate_breakout(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        z_vol = f["z_volume"]
        z_mom = f["z_momentum"]
        climax = f.get("climax", {})

        if z_vol > 0.30 and z_mom > 0.25:
            score = min(55.0 + (z_vol * 22.0) + (z_mom * 22.0), 96.0)
            trigger_valid = True
            if climax.get("is_bearish_sweep"):
                score = max(score - 35.0, 20.0)
                trigger_valid = False

            return {
                "strategy": "BREAKOUT",
                "active": True,
                "side": "LONG",
                "score": round(score, 1),
                "trigger_valid": trigger_valid,
                "confirmations": [
                    f"Volume surge Z: {z_vol:.2f}",
                    f"Momentum expansion Z: {z_mom:.2f}",
                    "Clean upside resistance clearance"
                ]
            }
        return {"strategy": "BREAKOUT", "active": False, "score": 25.0, "side": "NEUTRAL", "reason": "No breakout volume/momentum trigger."}

    # 6. Volatility Breakdown
    @staticmethod
    def evaluate_breakdown(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        z_vol = f["z_volume"]
        z_mom = f["z_momentum"]
        climax = f.get("climax", {})

        if z_vol > 0.30 and z_mom < -0.25:
            score = min(55.0 + (z_vol * 22.0) + (abs(z_mom) * 22.0), 96.0)
            trigger_valid = True
            if climax.get("is_bullish_sweep"):
                score = max(score - 35.0, 20.0)
                trigger_valid = False

            return {
                "strategy": "BREAKDOWN",
                "active": True,
                "side": "SHORT",
                "score": round(score, 1),
                "trigger_valid": trigger_valid,
                "confirmations": [
                    f"Volume surge on breakdown Z: {z_vol:.2f}",
                    f"Downside momentum expansion Z: {z_mom:.2f}",
                    "Support floor violated"
                ]
            }
        return {"strategy": "BREAKDOWN", "active": False, "score": 25.0, "side": "NEUTRAL", "reason": "No breakdown volume/momentum trigger."}

    # 7. Squeeze Breakout (Compression Release)
    @staticmethod
    def evaluate_squeeze_breakout(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        if not f.get("is_compressed") and regime != "LOW_VOLATILITY":
            return {"strategy": "SQUEEZE_BREAKOUT", "active": False, "score": 15.0, "side": "NEUTRAL", "reason": "Volatility bands not in compression."}

        z_vol = f["z_volume"]
        z_mom = f["z_momentum"]
        if z_vol > 0.20 and abs(z_mom) > 0.20:
            is_long = z_mom > 0
            score = min(60.0 + (z_vol * 20.0) + (abs(z_mom) * 20.0), 96.0)
            return {
                "strategy": "SQUEEZE_BREAKOUT",
                "active": True,
                "side": "LONG" if is_long else "SHORT",
                "score": round(score, 1),
                "trigger_valid": True,
                "confirmations": [
                    f"Release from volatility compression (BB Width: {f['bb_width']:.3f})",
                    f"Directional thrust Z: {z_mom:.2f}"
                ]
            }
        return {"strategy": "SQUEEZE_BREAKOUT", "active": False, "score": 35.0, "side": "NEUTRAL", "reason": "Compression active; awaiting ignition bar."}

    # 8. Mean Reversion
    @staticmethod
    def evaluate_mean_reversion(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        if "TRENDING" in regime:
            return {"strategy": "MEAN_REVERSION", "active": False, "score": 10.0, "side": "NEUTRAL", "reason": "Mean reversion disabled during active trend."}

        z_vwap = f["z_vwap_distance"]
        rsi = f["rsi"]

        if z_vwap < -0.65 and rsi < 36.0:
            score = min(abs(z_vwap) * 40.0 + (36.0 - rsi) * 2.0 + 30.0, 95.0)
            return {
                "strategy": "MEAN_REVERSION",
                "active": True,
                "side": "LONG",
                "score": round(score, 1),
                "trigger_valid": f["price"] > f["ema9"],
                "confirmations": [f"Oversold RSI ({rsi:.1f})", f"Stretched below VWAP ({z_vwap:.2f} ATR)"]
            }
        elif z_vwap > 0.65 and rsi > 64.0:
            score = min(abs(z_vwap) * 40.0 + (rsi - 64.0) * 2.0 + 30.0, 95.0)
            return {
                "strategy": "MEAN_REVERSION",
                "active": True,
                "side": "SHORT",
                "score": round(score, 1),
                "trigger_valid": f["price"] < f["ema9"],
                "confirmations": [f"Overbought RSI ({rsi:.1f})", f"Extended above VWAP ({z_vwap:.2f} ATR)"]
            }

        return {"strategy": "MEAN_REVERSION", "active": False, "score": 15.0, "side": "NEUTRAL", "reason": "No mean-reversion boundary breach."}

    # 9. Support / Resistance Reversal (SMC FVG & Liquidity Sweep Reversal)
    @staticmethod
    def evaluate_sr_reversal(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        climax = f.get("climax", {})
        fvg = f.get("fvg_info", {})
        price = f["price"]

        # Bullish reversal at support (e.g. swept lows with pinbar rejection)
        if climax.get("is_bullish_sweep") and fvg.get("type") == "BULLISH":
            score = 82.0 + min(f.get("z_volume", 0) * 10.0, 12.0)
            return {
                "strategy": "SUPPORT_RESISTANCE_REVERSAL",
                "active": True,
                "side": "LONG",
                "score": round(min(score, 95.0), 1),
                "trigger_valid": price > f["ema9"],
                "confirmations": [
                    "Liquidity sweep bottom with rejection wick",
                    "Confluence at bullish Fair Value Gap support"
                ]
            }
        elif climax.get("is_bearish_sweep") and fvg.get("type") == "BEARISH":
            score = 82.0 + min(f.get("z_volume", 0) * 10.0, 12.0)
            return {
                "strategy": "SUPPORT_RESISTANCE_REVERSAL",
                "active": True,
                "side": "SHORT",
                "score": round(min(score, 95.0), 1),
                "trigger_valid": price < f["ema9"],
                "confirmations": [
                    "Liquidity sweep top with rejection wick",
                    "Confluence at bearish Fair Value Gap resistance"
                ]
            }

        return {"strategy": "SUPPORT_RESISTANCE_REVERSAL", "active": False, "score": 15.0, "side": "NEUTRAL", "reason": "No S/R reversal confluence."}

    # 10. Trend Continuation
    @staticmethod
    def evaluate_trend_continuation(f: Dict[str, Any], regime: str) -> Dict[str, Any]:
        if "TRENDING" not in regime:
            return {"strategy": "TREND_CONTINUATION", "active": False, "score": 10.0, "side": "NEUTRAL", "reason": "Requires trending regime."}

        is_bull = "BULL" in regime
        slope = f["ema_slope"]
        price = f["price"]

        if is_bull and price > f["ema9"] and slope > 0.10:
            score = 75.0 + min(slope * 40.0, 20.0)
            return {
                "strategy": "TREND_CONTINUATION",
                "active": True,
                "side": "LONG",
                "score": round(min(score, 96.0), 1),
                "trigger_valid": price > f["vwap"],
                "confirmations": ["Strong bullish slope acceleration", "Price sustained above 9 EMA"]
            }
        elif not is_bull and price < f["ema9"] and slope < -0.10:
            score = 75.0 + min(abs(slope) * 40.0, 20.0)
            return {
                "strategy": "TREND_CONTINUATION",
                "active": True,
                "side": "SHORT",
                "score": round(min(score, 96.0), 1),
                "trigger_valid": price < f["vwap"],
                "confirmations": ["Strong bearish slope acceleration", "Price sustained below 9 EMA"]
            }

        return {"strategy": "TREND_CONTINUATION", "active": False, "score": 20.0, "side": "NEUTRAL", "reason": "No slope continuation."}


class StrategySelector:
    """
    Routes market regime and directional bias to the Strategy Pool.
    Selects the optimal strategy matching current market conditions.
    Rejects setups where strategy criteria are incomplete.
    """

    @classmethod
    def select_best_strategy(
        cls,
        features: Dict[str, Any],
        regime: str,
        direction_bias: str = "NEUTRAL"
    ) -> Dict[str, Any]:
        # Evaluate all 10 strategies
        all_evals = [
            StrategyEngine.evaluate_trend_following(features, regime),
            StrategyEngine.evaluate_vwap_reclaim(features, regime),
            StrategyEngine.evaluate_vwap_rejection(features, regime),
            StrategyEngine.evaluate_momentum_pullback(features, regime),
            StrategyEngine.evaluate_breakout(features, regime),
            StrategyEngine.evaluate_breakdown(features, regime),
            StrategyEngine.evaluate_squeeze_breakout(features, regime),
            StrategyEngine.evaluate_mean_reversion(features, regime),
            StrategyEngine.evaluate_sr_reversal(features, regime),
            StrategyEngine.evaluate_trend_continuation(features, regime)
        ]

        # Filter active strategies matching directional bias (if directional bias is established)
        matching_evals = []
        for e in all_evals:
            if not e.get("active"):
                continue
            if direction_bias != "NEUTRAL" and e.get("side") != direction_bias:
                continue
            matching_evals.append(e)

        if not matching_evals:
            # Baseline continuous setup score derived from technical confluence:
            # momentum, ema slope, rsi deviation, and volume
            rsi = float(features.get("rsi", 50.0))
            z_mom = float(features.get("z_momentum", 0.0))
            slope = float(features.get("ema_slope", 0.0))
            z_vol = float(features.get("z_volume", 0.0))

            base_score = 22.0 + (abs(slope) * 15.0) + (abs(z_mom) * 8.0) + (abs(rsi - 50.0) * 0.3) + (max(z_vol, -1.0) * 2.5)
            cont_score = round(max(10.0, min(base_score, 49.0)), 1)

            return {
                "strategy": "NO_STRATEGY_MATCH",
                "active": False,
                "side": "NEUTRAL",
                "score": cont_score,
                "trigger_valid": False,
                "confirmations": [],
                "reason": f"No active strategy criteria satisfied for regime '{regime}' and direction '{direction_bias}'."
            }

        # Select highest-scoring strategy among matching candidates
        best = max(matching_evals, key=lambda x: x.get("score", 0.0))
        return best