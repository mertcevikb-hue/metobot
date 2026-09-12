"""
Confluence & Contradiction Engine for Metobot 0.5.2.
Separates Higher-Timeframe (HTF) context from intraday triggers.
Enforces strict CALL / PUT mathematical symmetry, computes independent
positive confluence score and contradiction penalty, and outputs deterministic
trade approval or explicit NO TRADE / WAIT states.
"""
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from loguru import logger
from engine.market_hours import MarketSchedule


class ConfluenceEngine:
    """
    Evaluates multi-timeframe structural alignment and contradiction penalties.
    Strictly symmetrical between BULLISH (CALL) and BEARISH (PUT).
    """

    MIN_CONFLUENCE_THRESHOLD: float = 55.0
    MAX_CONTRADICTION_ALLOWED: float = 35.0

    @classmethod
    def evaluate(
        cls,
        features: Dict[str, Any],
        direction_bias: str,
        regime: str,
        htf_pattern: Optional[Dict[str, Any]] = None,
        option_data: Optional[Dict[str, Any]] = None,
        dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates positive confluence and contradiction penalties.
        Returns full breakdown with human-readable English explanations.
        """
        is_call = direction_bias in ("BULLISH", "CALL", "LONG", "BUY")
        is_put = direction_bias in ("BEARISH", "PUT", "SHORT", "SELL")

        if not is_call and not is_put:
            return {
                "confluence_score": 0.0,
                "contradiction_penalty": 0.0,
                "net_confluence": 0.0,
                "verdict": "NO_TRADE",
                "approved": False,
                "direction_bias": "NEUTRAL",
                "confluence_factors": [],
                "contradiction_factors": ["Direction bias is NEUTRAL. Market lacks directional edge."],
                "htf_alignment": "NEUTRAL",
                "structure_alignment": "NEUTRAL",
                "flow_alignment": "NEUTRAL",
                "volatility_alignment": "NEUTRAL",
                "options_alignment": "NEUTRAL",
                "explanation": "No trade: Market is neutral with no directional edge or actionable regime."
            }

        confluence_factors: List[str] = []
        contradiction_factors: List[str] = []

        # -------------------------------------------------------------
        # 1. Higher-Timeframe (HTF) Context Alignment (0 - 25 points)
        # -------------------------------------------------------------
        htf_score = 0.0
        price = float(features.get("price", 0.0))
        ema50 = float(features.get("ema50", price))
        ema200 = float(features.get("ema200", price))
        trend_intensity = float(features.get("trend_intensity", 50.0))

        if is_call:
            if price > ema200:
                htf_score += 8.0
                confluence_factors.append("Price positioned above HTF EMA200 (Macro Bullish Trend)")
            else:
                contradiction_factors.append("Macro Contradiction: Price trading below daily EMA200")

            if price > ema50:
                htf_score += 7.0
                confluence_factors.append("Price positioned above HTF EMA50 (Intermediate Bullish Trend)")

            if htf_pattern and htf_pattern.get("active"):
                pat_name = htf_pattern.get("pattern", "HTF Setup")
                pat_qual = float(htf_pattern.get("quality_score", 70.0))
                htf_score += min(10.0, (pat_qual / 100.0) * 10.0)
                confluence_factors.append(f"Confirmed HTF Bullish Pattern: {pat_name} (Quality: {pat_qual:.0f}%)")
            elif "BULL" in regime:
                htf_score += 6.0
                confluence_factors.append(f"Regime aligns with Bullish thesis ({regime})")

        elif is_put:
            if price < ema200:
                htf_score += 8.0
                confluence_factors.append("Price positioned below HTF EMA200 (Macro Bearish Trend)")
            else:
                contradiction_factors.append("Macro Contradiction: Price trading above daily EMA200")

            if price < ema50:
                htf_score += 7.0
                confluence_factors.append("Price positioned below HTF EMA50 (Intermediate Bearish Trend)")

            if htf_pattern and htf_pattern.get("active") and htf_pattern.get("side") == "PUT":
                pat_name = htf_pattern.get("pattern", "HTF Bearish Setup")
                pat_qual = float(htf_pattern.get("quality_score", 70.0))
                htf_score += min(10.0, (pat_qual / 100.0) * 10.0)
                confluence_factors.append(f"Confirmed HTF Bearish Pattern: {pat_name} (Quality: {pat_qual:.0f}%)")
            elif "BEAR" in regime:
                htf_score += 6.0
                confluence_factors.append(f"Regime aligns with Bearish thesis ({regime})")

        htf_score = min(25.0, htf_score)

        # -------------------------------------------------------------
        # 2. Intraday Market Structure (0 - 25 points)
        # -------------------------------------------------------------
        struct_score = 0.0
        vwap_struct = features.get("vwap_structure", {})
        mkt_struct = features.get("market_structure", {})
        vwap = float(features.get("vwap", price))

        if is_call:
            # VWAP relation
            if price > vwap:
                struct_score += 7.0
                confluence_factors.append("Price trading firmly above intraday VWAP")
                if vwap_struct.get("is_vwap_bullish"):
                    struct_score += 4.0
                    confluence_factors.append("VWAP slope is upward trending")
            else:
                contradiction_factors.append("Structural Contradiction: Price below VWAP on CALL candidate")

            # Breakout / Key Level hold
            if mkt_struct.get("is_breakout"):
                struct_score += 9.0
                confluence_factors.append("Intraday Opening Range / PDH Bullish Breakout active")
            elif mkt_struct.get("retest_status") == "BULLISH_RETEST_HELD":
                struct_score += 8.0
                confluence_factors.append("Successful Bullish retest of prior resistance as support")
            elif price > float(mkt_struct.get("pdh", 0.0)) > 0:
                struct_score += 5.0
                confluence_factors.append("Holding above Prior Day High (PDH)")

            if mkt_struct.get("is_false_breakout"):
                contradiction_factors.append("Trap Warning: Recent failed breakout (False Breakout) detected")

        elif is_put:
            # VWAP relation
            if price < vwap:
                struct_score += 7.0
                confluence_factors.append("Price trading firmly below intraday VWAP")
                if vwap_struct.get("is_vwap_bearish"):
                    struct_score += 4.0
                    confluence_factors.append("VWAP slope is downward trending")
            else:
                contradiction_factors.append("Structural Contradiction: Price above VWAP on PUT candidate")

            # Breakdown / Key Level loss
            if mkt_struct.get("is_breakdown"):
                struct_score += 9.0
                confluence_factors.append("Intraday Opening Range / PDL Bearish Breakdown active")
            elif mkt_struct.get("retest_status") == "BEARISH_RETEST_HELD":
                struct_score += 8.0
                confluence_factors.append("Successful Bearish retest of prior support as resistance")
            elif 0 < price < float(mkt_struct.get("pdl", float("inf"))):
                struct_score += 5.0
                confluence_factors.append("Trading below Prior Day Low (PDL)")

            if mkt_struct.get("is_false_breakdown"):
                contradiction_factors.append("Trap Warning: Recent failed breakdown (False Breakdown) detected")

        struct_score = min(25.0, struct_score)

        # -------------------------------------------------------------
        # 3. Momentum & Volume Flow (0 - 20 points)
        # -------------------------------------------------------------
        flow_score = 0.0
        rvol = float(features.get("rvol", 1.0))
        rsi = float(features.get("rsi", 50.0))
        div = features.get("divergence", {})
        climax = features.get("climax", {})

        if rvol >= 1.5:
            flow_score += 7.0
            confluence_factors.append(f"Strong relative volume confirmation (RVOL: {rvol:.2f}x)")
        elif rvol >= 1.1:
            flow_score += 4.0
            confluence_factors.append(f"Healthy volume participation (RVOL: {rvol:.2f}x)")
        else:
            contradiction_factors.append(f"Volume Contradiction: Below-average participation (RVOL: {rvol:.2f}x)")

        if is_call:
            if 52.0 <= rsi <= 68.0:
                flow_score += 7.0
                confluence_factors.append(f"RSI in optimal bullish expansion zone ({rsi:.1f})")
            elif rsi > 75.0:
                contradiction_factors.append(f"Overextended Warning: RSI overbought ({rsi:.1f})")
            elif rsi < 45.0:
                contradiction_factors.append(f"Weak Momentum: RSI below centerline ({rsi:.1f}) for CALL")

            if div.get("bearish_divergence"):
                contradiction_factors.append(f"Exhaustion Warning: Bearish Divergence ({div.get('div_description', 'RSI Divergence')})")
            else:
                flow_score += 6.0

            if climax.get("is_bearish_sweep"):
                contradiction_factors.append("Liquidity Trap: Upper wick rejection sweep detected")

        elif is_put:
            if 32.0 <= rsi <= 48.0:
                flow_score += 7.0
                confluence_factors.append(f"RSI in optimal bearish expansion zone ({rsi:.1f})")
            elif rsi < 25.0:
                contradiction_factors.append(f"Overextended Warning: RSI oversold ({rsi:.1f})")
            elif rsi > 55.0:
                contradiction_factors.append(f"Weak Momentum: RSI above centerline ({rsi:.1f}) for PUT")

            if div.get("bullish_divergence"):
                contradiction_factors.append(f"Exhaustion Warning: Bullish Divergence ({div.get('div_description', 'RSI Divergence')})")
            else:
                flow_score += 6.0

            if climax.get("is_bullish_sweep"):
                contradiction_factors.append("Liquidity Trap: Lower wick rejection sweep detected")

        flow_score = min(20.0, flow_score)

        # -------------------------------------------------------------
        # 4. Volatility & ATR State (0 - 15 points)
        # -------------------------------------------------------------
        vol_score = 0.0
        squeeze = features.get("squeeze", {})
        bb_width = float(features.get("bb_width", 0.0))
        atr = float(features.get("atr", 1.0))
        atr_pct = (atr / price * 100.0) if price > 0 else 1.0

        if squeeze.get("in_squeeze"):
            # Squeeze is coiling - good for breakout anticipation, but not if trading inside without breakout
            vol_score += 5.0
            confluence_factors.append("Volatility compression active (potential explosive release)")
        elif squeeze.get("fired"):
            vol_score += 10.0
            confluence_factors.append("Volatility squeeze fired - directional volatility expansion underway")
        else:
            vol_score += 6.0

        if 0.5 <= atr_pct <= 4.0:
            vol_score += 5.0
            confluence_factors.append(f"Intraday ATR volatility healthy ({atr_pct:.2f}% of spot)")
        else:
            contradiction_factors.append(f"Excessive Volatility: ATR is {atr_pct:.2f}% of spot")

        vol_score = min(15.0, vol_score)

        # -------------------------------------------------------------
        # 5. Options Greeks & Execution Surface (0 - 15 points)
        # -------------------------------------------------------------
        opt_score = 0.0
        if option_data and option_data.get("status") != "DATA_UNAVAILABLE":
            delta = abs(float(option_data.get("delta", 0.50)))
            spread_pct = float(option_data.get("spread_friction_pct", 5.0))
            is_0dte = bool(option_data.get("is_0dte", False))

            if 0.35 <= delta <= 0.65:
                opt_score += 6.0
                confluence_factors.append(f"Option delta ({delta:.2f}) optimal for intraday directional leverage")
            elif delta < 0.25:
                contradiction_factors.append(f"Option Delta Warning: Deep OTM delta ({delta:.2f}) has high gamma risk")

            if spread_pct <= 5.0:
                opt_score += 6.0
                confluence_factors.append(f"Tight institutional option spread friction ({spread_pct:.1f}%)")
            elif spread_pct > 10.0:
                contradiction_factors.append(f"Execution Drag: Wide option bid-ask spread ({spread_pct:.1f}%)")

            # 0DTE time-of-day check
            s_dt = MarketSchedule.get_us_market_time(dt)
            curr_time = s_dt.time()
            from datetime import time
            if is_0dte:
                if curr_time >= time(14, 30):
                    contradiction_factors.append("0DTE Expiration Risk: After 14:30 ET, catastrophic theta decay active")
                elif curr_time >= time(13, 30):
                    contradiction_factors.append("0DTE Warning: Post-13:30 ET entry restricted due to rapid decay")
                else:
                    opt_score += 3.0
                    confluence_factors.append("0DTE window within liquid trading hours")
            else:
                opt_score += 3.0
        else:
            opt_score = 8.0  # Equity fallback

        opt_score = min(15.0, opt_score)

        # -------------------------------------------------------------
        # Positive Confluence Sum (0 - 100)
        # -------------------------------------------------------------
        positive_confluence = round(htf_score + struct_score + flow_score + vol_score + opt_score, 1)

        # -------------------------------------------------------------
        # Contradiction Penalty Calculation (0 - 100)
        # -------------------------------------------------------------
        contradiction_penalty = 0.0
        for cf in contradiction_factors:
            if "Contradiction" in cf:
                contradiction_penalty += 20.0
            elif "Trap" in cf or "Exhaustion" in cf or "Expiration" in cf:
                contradiction_penalty += 15.0
            elif "Warning" in cf:
                contradiction_penalty += 10.0
            else:
                contradiction_penalty += 8.0

        contradiction_penalty = min(100.0, round(contradiction_penalty, 1))
        net_confluence = max(0.0, round(positive_confluence - (contradiction_penalty * 0.60), 1))

        # -------------------------------------------------------------
        # Deterministic Gating & Verdict
        # -------------------------------------------------------------
        if contradiction_penalty >= cls.MAX_CONTRADICTION_ALLOWED:
            verdict = "NO_TRADE"
            approved = False
            explanation = (
                f"NO TRADE: Excessive contradictions ({contradiction_penalty:.0f} pts penalty) "
                f"outweighed positive confluence ({positive_confluence:.0f} pts). "
                f"Primary blockers: {'; '.join(contradiction_factors[:2])}."
            )
        elif net_confluence < cls.MIN_CONFLUENCE_THRESHOLD:
            verdict = "WAIT"
            approved = False
            explanation = (
                f"WAIT: Net confluence ({net_confluence:.0f}%) below minimum required threshold "
                f"({cls.MIN_CONFLUENCE_THRESHOLD:.0f}%). Setup lacks sufficient multi-timeframe confirmation."
            )
        else:
            verdict = "APPROVED"
            approved = True
            explanation = (
                f"APPROVED ({direction_bias}): Net confluence {net_confluence:.0f}% with low contradiction "
                f"({contradiction_penalty:.0f} pts). Strong alignment across HTF, VWAP structure, and volume flow."
            )

        return {
            "confluence_score": positive_confluence,
            "contradiction_penalty": contradiction_penalty,
            "net_confluence": net_confluence,
            "verdict": verdict,
            "approved": approved,
            "direction_bias": direction_bias,
            "confluence_factors": confluence_factors,
            "contradiction_factors": contradiction_factors,
            "htf_score": round(htf_score, 1),
            "structure_score": round(struct_score, 1),
            "flow_score": round(flow_score, 1),
            "volatility_score": round(vol_score, 1),
            "options_score": round(opt_score, 1),
            "explanation": explanation
        }
