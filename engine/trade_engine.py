"""
Trade Engine - Institutional Trade Gatekeeper with VETO Authority.
Enforces the core architectural principle: SIGNAL != TRADE.
Every trade candidate must pass 9 validation gates before execution is permitted.
"""
from typing import Dict, Any, Tuple, Optional, List
from datetime import datetime, timezone, timedelta
from loguru import logger
from config.trading_config import global_config
from engine.portfolio import PortfolioManager, global_portfolio


class TradeEngine:
    """
    Evaluates candidate signals from QuantEngine against portfolio, exposure,
    risk, and market conditions. Holds absolute VETO / REJECT authority.
    """

    def __init__(self, portfolio: Optional[PortfolioManager] = None):
        self.portfolio = portfolio or global_portfolio
        # Cooldown registry: underlying_symbol -> last_exit_datetime_utc
        self._cooldown_registry: Dict[str, datetime] = {}

    def record_exit(self, underlying: str, exit_time: Optional[datetime] = None) -> None:
        """Registers a position exit to initiate the cooldown timer."""
        clean = underlying.strip().upper()
        now_dt = exit_time or datetime.now(timezone.utc)
        self._cooldown_registry[clean] = now_dt
        logger.info(f"⏳ Cooldown initiated for {clean} ({global_config.cooldown_minutes} minutes).")

    def check_cooldown(self, underlying: str) -> Tuple[bool, int]:
        """
        Checks if the underlying is within the post-exit cooldown window.
        Returns: (is_cooldown_active, remaining_seconds)
        """
        clean = underlying.strip().upper()
        last_exit = self._cooldown_registry.get(clean)
        if not last_exit:
            return False, 0

        cooldown_delta = timedelta(minutes=global_config.cooldown_minutes)
        now_dt = datetime.now(timezone.utc)
        elapsed = (now_dt - last_exit)

        if elapsed < cooldown_delta:
            remaining_secs = int((cooldown_delta - elapsed).total_seconds())
            return True, remaining_secs

        # Cooldown has expired
        self._cooldown_registry.pop(clean, None)
        return False, 0

    def evaluate_candidate(
        self,
        candidate_signal: Dict[str, Any],
        is_market_open: bool = True,
        market_reason: str = "Market Open"
    ) -> Dict[str, Any]:
        """
        Evaluates a candidate signal through the 9 Institutional Gates.
        Returns approval verdict with complete decision explanation.
        """
        symbol = candidate_signal.get("symbol", "UNKNOWN")
        underlying = candidate_signal.get("underlying") or symbol
        clean_underlying = underlying.strip().upper()
        direction = candidate_signal.get("decision", "NO_TRADE")

        rejection_reasons = []
        gate_verdicts = {}

        # -------------------------------------------------------------
        # GATE 1: Market Hours & Session Validity
        # -------------------------------------------------------------
        if not is_market_open and not candidate_signal.get("allow_outside_hours"):
            rejection_reasons.append(f"MARKET_CLOSED: {market_reason}")
            gate_verdicts["market_hours"] = "FAILED"
        else:
            gate_verdicts["market_hours"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 2: Directional Edge (No Chop / Conflict)
        # -------------------------------------------------------------
        dir_res = candidate_signal.get("direction_evaluation")
        if not dir_res:
            is_bull = direction in ["LONG", "BUY", "CALL"]
            score_val = float(candidate_signal.get("score", 75.0))
            edge_val = (score_val - 30.0) if score_val > 50.0 else 0.0
            dir_res = {
                "bias": "BULLISH" if is_bull else ("BEARISH" if direction in ["SHORT", "SELL", "PUT"] else "NEUTRAL"),
                "direction_edge": edge_val if is_bull else -edge_val,
                "is_conflict": False
            }

        dir_bias = dir_res.get("bias", "NEUTRAL")
        edge = float(dir_res.get("direction_edge", 0.0))
        is_conflict = dir_res.get("is_conflict", False)

        if is_conflict:
            rejection_reasons.append("DIRECTIONAL_CONFLICT: High simultaneous Bullish and Bearish pressure.")
            gate_verdicts["directional_edge"] = "FAILED"
        elif dir_bias == "NEUTRAL" or abs(edge) < global_config.min_direction_edge:
            rejection_reasons.append(f"LOW_DIRECTIONAL_EDGE: Edge {edge:+.1f} below ±{global_config.min_direction_edge:.1f} threshold.")
            gate_verdicts["directional_edge"] = "FAILED"
        else:
            gate_verdicts["directional_edge"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 3: Confidence Threshold
        # -------------------------------------------------------------
        conf_res = candidate_signal.get("confidence_evaluation", {})
        conf_score = float(conf_res.get("model_confidence", candidate_signal.get("score", 0.0)))
        min_conf = float(conf_res.get("threshold", global_config.minimum_confidence))

        if conf_score < min_conf:
            rejection_reasons.append(f"LOW_CONFIDENCE: Model confidence {conf_score:.1f}% below minimum {min_conf:.1f}%.")
            gate_verdicts["confidence_threshold"] = "FAILED"
        else:
            gate_verdicts["confidence_threshold"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 4: Strategy Preconditions & Trigger Validity
        # -------------------------------------------------------------
        strat = candidate_signal.get("strategy")
        if not strat or strat == "NONE":
            if candidate_signal.get("entry_valid") and float(candidate_signal.get("score", 0)) >= global_config.minimum_confidence:
                strat = "QUANT_SETUP"
            else:
                strat = "NONE"

        entry_valid = candidate_signal.get("entry_valid", False)
        if strat in ["NONE", "NO_STRATEGY_MATCH", "NO_SETUP"] or not entry_valid:
            rejection_reasons.append(f"STRATEGY_NOT_VALID: Strategy '{strat}' criteria or trigger incomplete.")
            gate_verdicts["strategy_valid"] = "FAILED"
        else:
            gate_verdicts["strategy_valid"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 5: Risk / Reward Ratio Benchmark
        # -------------------------------------------------------------
        risk_m = candidate_signal.get("risk_metrics", {})
        rr_ratio = float(risk_m.get("reward_risk_ratio", 2.0))
        if rr_ratio < global_config.min_reward_risk_ratio:
            rejection_reasons.append(f"POOR_RISK_REWARD: R:R of {rr_ratio:.2f} is below required {global_config.min_reward_risk_ratio:.2f} benchmark.")
            gate_verdicts["risk_reward"] = "FAILED"
        else:
            gate_verdicts["risk_reward"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 6: Cooldown Active
        # -------------------------------------------------------------
        in_cooldown, rem_secs = self.check_cooldown(clean_underlying)
        if in_cooldown:
            rejection_reasons.append(f"COOLDOWN_ACTIVE: Underlying '{clean_underlying}' in post-exit cooldown ({rem_secs}s remaining).")
            gate_verdicts["cooldown"] = "FAILED"
        else:
            gate_verdicts["cooldown"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 7: Daily Loss Limit
        # -------------------------------------------------------------
        if self.portfolio.is_daily_loss_breached():
            rejection_reasons.append("DAILY_LOSS_LIMIT_REACHED: Trading suspended for current session.")
            gate_verdicts["daily_loss"] = "FAILED"
        else:
            gate_verdicts["daily_loss"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 8: Instrument Specific Liquidity / 0DTE Risk
        # -------------------------------------------------------------
        opt_res = candidate_signal.get("option_data") or candidate_signal.get("option_evaluation")
        if opt_res:
            if not opt_res.get("is_liquid", True):
                spread_pct = opt_res.get("spread_friction_pct", 100.0)
                rejection_reasons.append(f"LOW_LIQUIDITY: Option spread friction ({spread_pct:.1f}%) exceeds safety threshold.")
                gate_verdicts["option_liquidity"] = "FAILED"
            elif candidate_signal.get("is_vetoed"):
                vr = candidate_signal.get("veto_reasons", [])
                rejection_reasons.append(f"OPTION_QUALITY_FAILED: {'; '.join(vr)}")
                gate_verdicts["option_liquidity"] = "FAILED"
            else:
                gate_verdicts["option_liquidity"] = "PASSED"
        else:
            gate_verdicts["option_liquidity"] = "PASSED"

        # -------------------------------------------------------------
        # GATE 9: Capital & Underlying Exposure Limit (No Duplicates)
        # -------------------------------------------------------------
        price = float(candidate_signal.get("price", 0.0))
        inst_type = "OPTION" if opt_res else "STOCK"
        sl = risk_m.get("stop_loss", 0.0)
        is_0dte = bool(opt_res.get("is_0dte", False)) if opt_res else False

        planned_qty, planned_notional, size_expl = self.portfolio.calculate_position_size(
            symbol=symbol,
            instrument=inst_type,
            entry_price=price,
            stop_loss=sl,
            confidence=conf_score,
            is_0dte=is_0dte
        )

        exposure_ok, exposure_err = self.portfolio.check_exposure_limit(
            underlying=clean_underlying,
            direction=direction,
            new_notional=planned_notional
        )
        if not exposure_ok:
            rejection_reasons.append(exposure_err)
            gate_verdicts["exposure_limit"] = "FAILED"
        else:
            gate_verdicts["exposure_limit"] = "PASSED"

        # -------------------------------------------------------------
        # Final Verdict Synthesis
        # -------------------------------------------------------------
        approved = len(rejection_reasons) == 0

        # Construct Clear Decision Explanation
        if approved:
            decision_verdict = "ACCEPTED"
            primary_reason = f"Approved {direction} setup on {clean_underlying} via {strat}."
            explanation_lines = [
                f"✓ Direction Bias: {dir_bias} (Edge: {edge:+.1f})",
                f"✓ Strategy: {strat} (Conditions fully met)",
                f"✓ Confidence: {conf_score:.1f}% (Threshold: {min_conf:.1f}%)",
                f"✓ Risk: R:R {rr_ratio:.2f} (SL: ${risk_m.get('stop_loss')}, TP1: ${risk_m.get('take_profit')})",
                f"✓ Sizing: {size_expl}",
                f"✓ Portfolio: Clean underlying exposure and sufficient capital"
            ]
        else:
            decision_verdict = "REJECTED"
            primary_reason = rejection_reasons[0]
            explanation_lines = [f"⛔ {r}" for r in rejection_reasons]

        decision_explanation = "\n".join(explanation_lines)

        return {
            "verdict": decision_verdict,
            "approved": approved,
            "symbol": symbol,
            "underlying": clean_underlying,
            "direction": direction,
            "primary_reason": primary_reason,
            "rejection_reasons": rejection_reasons,
            "gate_verdicts": gate_verdicts,
            "decision_explanation": decision_explanation,
            "planned_quantity": planned_qty,
            "planned_notional": planned_notional,
            "confidence": conf_score
        }


# Global singleton TradeEngine
global_trade_engine = TradeEngine()
