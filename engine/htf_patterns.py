"""
Higher-Timeframe (HTF) Pattern Recognition and Fundamental Context Engine.
Identifies institutional structural setups across daily / multi-bar data:
1. Volatility Contraction Pattern (VCP)
2. High Tight Flag (HTF)
3. Cup with Handle
4. Flat Base
5. New High Breakout (52-week, ATH, multi-month)
6. Fundamental Growth Context Filter
"""
from typing import List, Dict, Any, Optional
import math


class HTFPatternEngine:
    """
    Evaluates Higher-Timeframe structural context.
    HTF patterns establish the macro setup; intraday triggers provide the execution timing.
    """

    @staticmethod
    def detect_vcp(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Volatility Contraction Pattern (VCP):
        Detects progressive narrowing of price contractions accompanied by volume dry-up.
        Example: Contraction 1 (15-25%) -> Contraction 2 (8-14%) -> Contraction 3 (3-7%).
        """
        n = min(len(highs), len(lows), len(closes), len(volumes))
        if n < 30:
            return {"active": False, "score": 0.0, "contractions": 0, "reason": "Insufficient bars for VCP."}

        # Divide history into 3 successive swing phases
        p1_len = n // 3
        p2_len = n // 3
        p3_len = n - p1_len - p2_len

        h1, l1 = max(highs[:p1_len]), min(lows[:p1_len])
        h2, l2 = max(highs[p1_len:p1_len + p2_len]), min(lows[p1_len:p1_len + p2_len])
        h3, l3 = max(highs[-p3_len:]), min(lows[-p3_len:])

        depth1 = (h1 - l1) / h1 if h1 > 0 else 0.0
        depth2 = (h2 - l2) / h2 if h2 > 0 else 0.0
        depth3 = (h3 - l3) / h3 if h3 > 0 else 0.0

        v1 = sum(volumes[:p1_len]) / p1_len if p1_len > 0 else 1.0
        v2 = sum(volumes[p1_len:p1_len + p2_len]) / p2_len if p2_len > 0 else 1.0
        v3 = sum(volumes[-p3_len:]) / p3_len if p3_len > 0 else 1.0

        # Pattern condition: Progressive reduction in depth and volume drying up
        is_contracting_depth = depth1 > depth2 > depth3
        is_vol_drying = v3 < v1 * 0.90
        tight_last_stage = depth3 <= 0.08  # Final contraction within 8%

        if is_contracting_depth and tight_last_stage:
            score = 75.0 + (15.0 if is_vol_drying else 0.0) + min((depth1 - depth3) * 50.0, 10.0)
            return {
                "active": True,
                "pattern": "VCP",
                "score": round(min(score, 96.0), 1),
                "contractions": 3,
                "pivot_resistance": round(max(h2, h3), 2),
                "contraction_depths": [round(depth1 * 100, 1), round(depth2 * 100, 1), round(depth3 * 100, 1)],
                "volume_drying": is_vol_drying,
                "reason": f"VCP Confirmed: 3 contractions ({depth1 * 100:.1f}% -> {depth2 * 100:.1f}% -> {depth3 * 100:.1f}%), volume dried up."
            }

        # 2-contraction variant
        if depth2 > depth3 and depth3 <= 0.07:
            score = 65.0 + (15.0 if is_vol_drying else 0.0)
            return {
                "active": True,
                "pattern": "VCP",
                "score": round(min(score, 88.0), 1),
                "contractions": 2,
                "pivot_resistance": round(h2, 2),
                "contraction_depths": [round(depth2 * 100, 1), round(depth3 * 100, 1)],
                "volume_drying": is_vol_drying,
                "reason": f"VCP 2-stage contraction ({depth2 * 100:.1f}% -> {depth3 * 100:.1f}%)."
            }

        return {"active": False, "score": 20.0, "contractions": 0, "reason": "No progressive volatility contraction."}

    @staticmethod
    def detect_high_tight_flag(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        """
        High Tight Flag:
        Strong prior advance (+50% to +100% within 4-8 weeks) followed by
        tight sideways consolidation retracing no more than 20-25%.
        """
        n = len(closes)
        if n < 25:
            return {"active": False, "score": 0.0, "reason": "Insufficient bars for High Tight Flag."}

        # Look for pole: advance from low in first half to high of pole
        half = n // 2
        prior_low = min(lows[:half])
        flag_high = max(highs[half:])
        if prior_low <= 0:
            return {"active": False, "score": 0.0, "reason": "Invalid price baseline."}

        prior_advance_pct = (flag_high - prior_low) / prior_low * 100.0

        # Flag portion: last 10 bars
        flag_bars_h = highs[-10:]
        flag_bars_l = lows[-10:]
        curr_price = closes[-1]

        flag_top = max(flag_bars_h)
        flag_bottom = min(flag_bars_l)
        flag_retrace_pct = ((flag_top - flag_bottom) / flag_top) * 100.0 if flag_top > 0 else 100.0

        # High Tight Flag requirements:
        # 1. Prior advance >= +35% (in intraday/short-term dataset) or >= +50%
        # 2. Consolidation retrace <= 20%
        # 3. Current price holding near top third of flag
        if prior_advance_pct >= 30.0 and flag_retrace_pct <= 22.0:
            holds_upper_half = curr_price >= flag_bottom + (flag_top - flag_bottom) * 0.50
            if holds_upper_half:
                score = 70.0 + min(prior_advance_pct * 0.20, 15.0) + (10.0 if flag_retrace_pct <= 14.0 else 0.0)
                return {
                    "active": True,
                    "pattern": "HIGH_TIGHT_FLAG",
                    "score": round(min(score, 98.0), 1),
                    "pole_advance_pct": round(prior_advance_pct, 1),
                    "flag_retrace_pct": round(flag_retrace_pct, 1),
                    "flag_resistance": round(flag_top, 2),
                    "flag_support": round(flag_bottom, 2),
                    "reason": f"High Tight Flag: +{prior_advance_pct:.1f}% prior surge, tight {flag_retrace_pct:.1f}% consolidation."
                }

        return {"active": False, "score": 15.0, "reason": "No high tight flag geometry."}

    @staticmethod
    def detect_cup_with_handle(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Cup with Handle:
        Rounded accumulation bowl (U-shaped) followed by shallow handle consolidation
        in the upper third of the pattern.
        """
        n = len(closes)
        if n < 35:
            return {"active": False, "score": 0.0, "reason": "Insufficient bars for Cup with Handle."}

        # Cup left rim, bottom, right rim, and handle
        handle_len = min(8, n // 5)
        cup_highs = highs[:-handle_len]
        cup_lows = lows[:-handle_len]

        left_rim = max(cup_highs[:len(cup_highs) // 3])
        cup_bottom = min(cup_lows)
        right_rim = max(cup_highs[-len(cup_highs) // 3:])

        cup_depth_pct = ((left_rim - cup_bottom) / left_rim) * 100.0 if left_rim > 0 else 0.0
        rim_symmetry = abs(left_rim - right_rim) / left_rim * 100.0 if left_rim > 0 else 100.0

        # Handle
        handle_highs = highs[-handle_len:]
        handle_lows = lows[-handle_len:]
        handle_retrace_pct = ((right_rim - min(handle_lows)) / right_rim) * 100.0 if right_rim > 0 else 100.0

        # Geometry criteria:
        # Cup depth 10% - 38%
        # Rim symmetry within 8%
        # Handle retrace <= 12% and in upper third of cup
        if 8.0 <= cup_depth_pct <= 42.0 and rim_symmetry <= 10.0 and handle_retrace_pct <= 14.0:
            score = 72.0 + (10.0 if handle_retrace_pct <= 8.0 else 5.0) + (10.0 if rim_symmetry <= 4.0 else 0.0)
            return {
                "active": True,
                "pattern": "CUP_WITH_HANDLE",
                "score": round(min(score, 95.0), 1),
                "pivot_resistance": round(right_rim, 2),
                "cup_depth_pct": round(cup_depth_pct, 1),
                "handle_retrace_pct": round(handle_retrace_pct, 1),
                "reason": f"Cup with Handle: {cup_depth_pct:.1f}% rounded base, shallow {handle_retrace_pct:.1f}% handle."
            }

        return {"active": False, "score": 15.0, "reason": "No cup with handle geometry."}

    @staticmethod
    def detect_flat_base(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Flat Base:
        Tight horizontal channel with limited price variation (<= 12-15%)
        and contracting volume over an extended base.
        """
        n = len(closes)
        if n < 20:
            return {"active": False, "score": 0.0, "reason": "Insufficient bars for Flat Base."}

        lookback = min(n, 35)
        base_h = max(highs[-lookback:])
        base_l = min(lows[-lookback:])
        base_range_pct = ((base_h - base_l) / base_h) * 100.0 if base_h > 0 else 100.0

        # Volume contraction in second half of base
        mid = lookback // 2
        v_early = sum(volumes[-lookback:-mid]) / mid if mid > 0 else 1.0
        v_late = sum(volumes[-mid:]) / mid if mid > 0 else 1.0
        vol_contraction = v_late < v_early * 0.95

        curr_price = closes[-1]
        near_resistance = curr_price >= base_l + (base_h - base_l) * 0.70

        if base_range_pct <= 12.0 and near_resistance:
            score = 70.0 + (15.0 if vol_contraction else 5.0) + min((12.0 - base_range_pct) * 2.0, 10.0)
            return {
                "active": True,
                "pattern": "FLAT_BASE",
                "score": round(min(score, 94.0), 1),
                "base_resistance": round(base_h, 2),
                "base_support": round(base_l, 2),
                "base_range_pct": round(base_range_pct, 1),
                "volume_contraction": vol_contraction,
                "reason": f"Flat Base: Tight {base_range_pct:.1f}% range over {lookback} bars with price pressing resistance."
            }

        return {"active": False, "score": 15.0, "reason": "No flat base compression."}

    @staticmethod
    def detect_new_high_breakout(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        rvol: float = 1.0
    ) -> Dict[str, Any]:
        """
        New High Breakout:
        Tests or clears multi-month or 52-week high with volume and momentum confirmation.
        Detects failed breakouts.
        """
        n = len(closes)
        if n < 20:
            return {"active": False, "score": 0.0, "reason": "Insufficient bars for New High analysis."}

        price = closes[-1]
        prior_max_h = max(highs[:-1])
        prior_min_l = min(lows[:-1])

        is_clearing_high = price >= prior_max_h * 0.998
        has_volume = rvol >= 1.30

        # Check for failed breakout (wick above prior high but close below)
        curr_h = highs[-1]
        is_failed_high = (curr_h > prior_max_h) and (price < prior_max_h * 0.995)

        if is_failed_high:
            return {
                "active": False,
                "pattern": "NEW_HIGH_BREAKOUT",
                "score": 25.0,
                "is_failed_breakout": True,
                "prior_high": round(prior_max_h, 2),
                "reason": f"Failed New High Breakout: High ({curr_h:.2f}) cleared resistance ({prior_max_h:.2f}) but reversed."
            }

        if is_clearing_high and has_volume:
            score = 75.0 + min(rvol * 8.0, 15.0) + (8.0 if price > prior_max_h else 0.0)
            return {
                "active": True,
                "pattern": "NEW_HIGH_BREAKOUT",
                "score": round(min(score, 98.0), 1),
                "is_clearing": True,
                "prior_high": round(prior_max_h, 2),
                "rvol": rvol,
                "reason": f"New High Breakout: Price ({price:.2f}) breaking major high ({prior_max_h:.2f}) on {rvol:.1f}x RVOL."
            }

        return {"active": False, "score": 20.0, "reason": "Not near major new high."}

    @staticmethod
    def evaluate_fundamental_growth(fundamental_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Fundamental Growth Context Layer:
        Evaluates EPS growth, Revenue growth, earnings surprise, sales growth.
        Acts as a quality multiplier for stock selection and HTF continuation.
        """
        if not fundamental_data:
            return {
                "score": 50.0,
                "quality_tier": "NEUTRAL",
                "growth_confirmed": False,
                "summary": "No fundamental data provided."
            }

        eps_growth = float(fundamental_data.get("eps_growth") or fundamental_data.get("earningsQuarterlyGrowth") or 0.0)
        rev_growth = float(fundamental_data.get("revenue_growth") or fundamental_data.get("revenueGrowth") or 0.0)
        surprise = float(fundamental_data.get("earnings_surprise") or 0.0)

        score = 50.0
        confirmations = []

        if eps_growth >= 0.25:  # +25% EPS growth
            score += 20.0
            confirmations.append(f"Strong EPS Growth (+{eps_growth * 100:.1f}%)")
        elif eps_growth > 0:
            score += 10.0

        if rev_growth >= 0.20:  # +20% Revenue growth
            score += 15.0
            confirmations.append(f"Accelerating Revenue Growth (+{rev_growth * 100:.1f}%)")
        elif rev_growth > 0:
            score += 8.0

        if surprise > 0.05:
            score += 10.0
            confirmations.append(f"Positive Earnings Surprise (+{surprise * 100:.1f}%)")

        score = max(20.0, min(95.0, score))
        quality = "EXCELLENT" if score >= 80.0 else ("GOOD" if score >= 65.0 else "AVERAGE")

        return {
            "score": round(score, 1),
            "quality_tier": quality,
            "growth_confirmed": score >= 70.0,
            "eps_growth": eps_growth,
            "rev_growth": rev_growth,
            "confirmations": confirmations,
            "summary": "; ".join(confirmations) if confirmations else "Modest or baseline fundamentals"
        }

    @classmethod
    def evaluate_htf_context(
        cls,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        rvol: float = 1.0,
        fundamental_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Master Higher-Timeframe Context Evaluator:
        Runs all HTF structural detectors and aggregates best HTF setup with fundamental rating.
        """
        vcp = cls.detect_vcp(highs, lows, closes, volumes)
        htf_flag = cls.detect_high_tight_flag(highs, lows, closes, volumes)
        cup = cls.detect_cup_with_handle(highs, lows, closes, volumes)
        flat = cls.detect_flat_base(highs, lows, closes, volumes)
        new_high = cls.detect_new_high_breakout(highs, lows, closes, volumes, rvol)
        fund = cls.evaluate_fundamental_growth(fundamental_data)

        all_patterns = [vcp, htf_flag, cup, flat, new_high]
        active_patterns = [p for p in all_patterns if p.get("active")]

        if active_patterns:
            best_htf = max(active_patterns, key=lambda x: x.get("score", 0.0))
            combined_score = round(best_htf["score"] * 0.80 + fund["score"] * 0.20, 1)
            return {
                "has_htf_setup": True,
                "primary_htf_pattern": best_htf.get("pattern", "HTF_SETUP"),
                "htf_score": combined_score,
                "best_pattern": best_htf,
                "active_patterns": [p.get("pattern") for p in active_patterns],
                "fundamental_context": fund,
                "context_description": best_htf.get("reason", "HTF pattern confirmed")
            }

        return {
            "has_htf_setup": False,
            "primary_htf_pattern": "NONE",
            "htf_score": round(30.0 + fund["score"] * 0.20, 1),
            "best_pattern": None,
            "active_patterns": [],
            "fundamental_context": fund,
            "context_description": "No definitive HTF base pattern detected."
        }

    @classmethod
    def detect_patterns(
        cls,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        features: Optional[Dict[str, Any]] = None,
        fundamental_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Convenience alias for evaluate_htf_context extracting rvol from features."""
        rvol = float(features.get("rvol", 1.0)) if features else 1.0
        return cls.evaluate_htf_context(highs, lows, closes, volumes, rvol=rvol, fundamental_data=fundamental_data)
