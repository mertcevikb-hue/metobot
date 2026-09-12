"""
Stock Engine - Specialized quantitative analysis and scoring for Equity instruments.
Evaluates trend, momentum, structure, volume, VWAP, volatility, and risk/reward.
Keeps stock metrics strictly decoupled from options pricing.
"""
from typing import Dict, Any, List, Optional
import math


class StockEngine:
    """Evaluates stock-specific metrics and generates a pure Stock Score (0 - 100)."""

    @classmethod
    def evaluate(
        cls,
        features: Dict[str, Any],
        direction_bias: str,
        regime: str
    ) -> Dict[str, Any]:
        if not features:
            return {
                "instrument": "STOCK",
                "stock_score": 0.0,
                "metrics": {},
                "valid": False,
                "reason": "Missing feature data."
            }

        price = float(features.get("price", 0.0))
        atr = float(features.get("atr", 1.0))
        atr_pct = (atr / price * 100.0) if price > 0 else 1.0
        z_sep = float(features.get("z_ema_separation", 0.0))
        slope = float(features.get("ema_slope", 0.0))
        z_vwap = float(features.get("z_vwap_distance", 0.0))
        z_mom = float(features.get("z_momentum", 0.0))
        z_vol = float(features.get("z_volume", 0.0))
        rsi = float(features.get("rsi", 50.0))
        bb_width = float(features.get("bb_width", 0.02))

        is_bull = direction_bias == "BULLISH"
        is_bear = direction_bias == "BEARISH"

        # 1. Trend Quality (30% Weight)
        if is_bull:
            trend_score = 50.0 + (z_sep * 25.0) + (slope * 25.0)
        elif is_bear:
            trend_score = 50.0 - (z_sep * 25.0) - (slope * 25.0)
        else:
            trend_score = 40.0 - (abs(slope) * 20.0)
        trend_score = max(0.0, min(100.0, trend_score))

        # 2. Institutional VWAP Alignment (20% Weight)
        if is_bull:
            vwap_score = 50.0 + (z_vwap * 40.0)
        elif is_bear:
            vwap_score = 50.0 - (z_vwap * 40.0)
        else:
            vwap_score = max(0.0, 50.0 - (abs(z_vwap) * 30.0))
        vwap_score = max(0.0, min(100.0, vwap_score))

        # 3. Momentum & Relative Strength (20% Weight)
        if is_bull:
            rsi_pts = 30.0 if (52.0 <= rsi <= 68.0) else (15.0 if rsi < 52.0 else 10.0)
            mom_pts = max(min(z_mom * 30.0, 30.0), -10.0)
        elif is_bear:
            rsi_pts = 30.0 if (32.0 <= rsi <= 48.0) else (15.0 if rsi > 48.0 else 10.0)
            mom_pts = max(min(-z_mom * 30.0, 30.0), -10.0)
        else:
            rsi_pts = 20.0
            mom_pts = 10.0
        momentum_score = max(0.0, min(100.0, 40.0 + rsi_pts + mom_pts))

        # 4. Volume & Market Structure (15% Weight)
        vol_score = 50.0 + (z_vol * 25.0)
        fvg = features.get("fvg_info", {})
        if (is_bull and fvg.get("type") == "BULLISH") or (is_bear and fvg.get("type") == "BEARISH"):
            vol_score += 15.0
        vol_score = max(0.0, min(100.0, vol_score))

        # 5. Volatility & Risk Efficiency (15% Weight)
        # Penalize excessive ATR runaway (> 4% of price)
        if atr_pct > 4.5:
            volatility_efficiency = 40.0
        elif atr_pct < 0.3:
            volatility_efficiency = 50.0
        else:
            volatility_efficiency = 85.0

        # Composite Stock Score
        composite_score = round(
            (trend_score * 0.30) +
            (vwap_score * 0.20) +
            (momentum_score * 0.20) +
            (vol_score * 0.15) +
            (volatility_efficiency * 0.15),
            1
        )

        # Expected Holding Time based on regime & ATR
        expected_bars = 45 if "TREND" in regime else (20 if "BREAK" in regime else 30)

        # Planned dynamic targets for Stock
        stop_dist = round(1.75 * atr, 2)
        tp1_dist = round(1.75 * atr, 2)
        tp2_dist = round(3.50 * atr, 2)

        if is_bull:
            planned_sl = round(price - stop_dist, 2)
            planned_tp1 = round(price + tp1_dist, 2)
            planned_tp2 = round(price + tp2_dist, 2)
        else:
            planned_sl = round(price + stop_dist, 2)
            planned_tp1 = round(price - tp1_dist, 2)
            planned_tp2 = round(price - tp2_dist, 2)

        reward_risk_tp1 = round(tp1_dist / max(stop_dist, 1e-4), 2)
        reward_risk_tp2 = round(tp2_dist / max(stop_dist, 1e-4), 2)

        return {
            "instrument": "STOCK",
            "stock_score": composite_score,
            "trend_score": round(trend_score, 1),
            "vwap_score": round(vwap_score, 1),
            "momentum_score": round(momentum_score, 1),
            "volume_score": round(vol_score, 1),
            "volatility_efficiency": round(volatility_efficiency, 1),
            "atr": atr,
            "atr_pct": round(atr_pct, 2),
            "bb_width": round(bb_width, 4),
            "expected_holding_bars": expected_bars,
            "planned_levels": {
                "entry": round(price, 2),
                "sl": planned_sl,
                "tp1": planned_tp1,
                "tp2": planned_tp2,
                "rr_tp1": reward_risk_tp1,
                "rr_tp2": reward_risk_tp2
            },
            "valid": composite_score >= 50.0
        }
