"""
Dynamic Exit Engine - Three-Layer Exit Architecture.
Layer 1: Hard Risk (SL, Emergency Stop)
Layer 2: Dynamic Profit Taking (TP1, TP2, Quantity-Dependent Scaling, Profit Protection Ratchet, Trailing Stop)
Layer 3: Thesis & Rotation Exit (Bull/Bear Rotation Score, Thesis Invalidation, Time Exit, 0DTE Cutoff)
"""
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from config.trading_config import global_config


class RotationEngine:
    """
    Measures counter-trend directional pressure (Bull/Bear Rotation Score: 0 - 100).
    Differentiates normal pullbacks (0-30) from strong rotations (70-85) and thesis reversals (85-100).
    """

    @staticmethod
    def calculate_rotation(
        position_side: str,
        current_features: Dict[str, Any],
        current_price: Optional[float] = None
    ) -> Dict[str, Any]:
        is_long = position_side in ["LONG", "BUY", "CALL"]
        f = current_features or {}

        price = float(f.get("price") or current_price or 0.0)
        ema9 = f.get("ema9", price)
        ema21 = f.get("ema21", price)
        vwap = f.get("vwap", price)
        slope = f.get("ema_slope", 0.0)
        rsi = f.get("rsi", 50.0)
        z_mom = f.get("z_momentum", 0.0)
        climax = f.get("climax", {})
        div = f.get("divergence", {})

        score = 10.0  # baseline calm

        if is_long:
            # Adverse Bearish Rotation indicators
            if price < ema9:
                score += 15.0
            if price < ema21:
                score += 20.0
            if price < vwap:
                score += 20.0
            if slope < -0.05:
                score += 15.0
            if rsi < 45.0:
                score += 12.0
            if z_mom < -0.2:
                score += 10.0
            if climax.get("is_bearish_sweep"):
                score += 15.0
            if div.get("bearish_divergence"):
                score += 15.0
        else:
            # Adverse Bullish Rotation indicators
            if price > ema9:
                score += 15.0
            if price > ema21:
                score += 20.0
            if price > vwap:
                score += 20.0
            if slope > 0.05:
                score += 15.0
            if rsi > 55.0:
                score += 12.0
            if z_mom > 0.2:
                score += 10.0
            if climax.get("is_bullish_sweep"):
                score += 15.0
            if div.get("bullish_divergence"):
                score += 15.0

        rotation_score = max(0.0, min(100.0, round(score, 1)))

        if rotation_score < 30.0:
            stage = "NORMAL_PULLBACK"
            desc = "Normal market fluctuation; position thesis fully intact."
        elif rotation_score < 50.0:
            stage = "MOMENTUM_WEAKENING"
            desc = "Momentum decelerating; watch levels closely."
        elif rotation_score < 70.0:
            stage = "ROTATION_WARNING"
            desc = "Significant opposite pressure forming; prepare defensive exit."
        elif rotation_score < 85.0:
            stage = "STRONG_ROTATION"
            desc = "Strong opposite rotation confirmed; triggers runner exit."
        else:
            stage = "THESIS_REVERSAL"
            desc = "Complete directional thesis invalidation; immediate exit."

        return {
            "rotation_score": rotation_score,
            "stage": stage,
            "description": desc,
            "is_strong": rotation_score >= global_config.rotation_strong_threshold,
            "is_reversal": rotation_score >= global_config.rotation_reversal_threshold
        }


class DynamicExitEngine:
    """
    Coordinates Layer 1 (Hard Risk), Layer 2 (Dynamic Exit & Quantity Scaling),
    and Layer 3 (Thesis & Rotation) to make rational, profit-protecting exit decisions.
    """

    @classmethod
    def evaluate_exit(
        cls,
        position: Dict[str, Any],
        current_price: float,
        candle_high: float,
        candle_low: float,
        current_features: Dict[str, Any],
        is_0dte: bool = False
    ) -> Dict[str, Any]:
        """
        Evaluates active position against the 3 exit layers.
        Returns: {
            "should_exit": bool,
            "exit_type": "FULL" | "PARTIAL" | "NONE",
            "quantity_to_exit": float,
            "exit_price": float,
            "exit_reason": str,
            "result_code": "TP1" | "TP2" | "PROFIT_PROTECTION" | "SL" | "ROTATION_EXIT" | "THESIS_EXIT" | "TIME_EXIT" | "NONE",
            "updated_position": Dict[str, Any]
        }
        """
        trade_id = position.get("trade_id")
        side = position.get("side", "LONG")
        is_long = side in ["LONG", "BUY", "CALL"]
        entry_price = float(position.get("entry_price", current_price))
        total_qty = float(position.get("initial_quantity", position.get("quantity", 1.0)))
        current_qty = float(position.get("quantity", 1.0))
        state = position.get("state", "OPEN")
        tp1 = position.get("tp1") or position.get("take_profit")
        tp2 = position.get("tp2")
        sl = position.get("stop_loss")
        trailing_sl = position.get("trailing_stop", sl)
        atr = float(current_features.get("atr", 1.0))

        # Track Maximum Favorable Excursion (MFE) & Maximum Adverse Excursion (MAE)
        high_mark = max(candle_high, current_price)
        low_mark = min(candle_low, current_price)
        if is_long:
            fav_diff = high_mark - entry_price
            adv_diff = entry_price - low_mark
        else:
            fav_diff = entry_price - low_mark
            adv_diff = high_mark - entry_price

        mfe = max(float(position.get("mfe", 0.0)), fav_diff)
        mae = max(float(position.get("mae", 0.0)), adv_diff)
        position["mfe"] = round(mfe, 2)
        position["mae"] = round(mae, 2)

        # -------------------------------------------------------------
        # LAYER 1: HARD RISK CHECKS (Hard SL)
        # -------------------------------------------------------------
        active_sl = trailing_sl if (trailing_sl is not None) else sl
        if active_sl is not None and active_sl > 0:
            sl_breached = (is_long and low_mark <= active_sl) or (not is_long and high_mark >= active_sl)
            if sl_breached:
                is_trailing = (trailing_sl is not None and trailing_sl != sl and (
                    (is_long and trailing_sl > entry_price) or (not is_long and trailing_sl < entry_price)
                ))
                code = "PROFIT_PROTECTION" if is_trailing else "SL"
                reason = f"Trailing Stop hit at ${active_sl:.2f}" if is_trailing else f"Hard Stop-Loss hit at ${active_sl:.2f}"
                return {
                    "should_exit": True,
                    "exit_type": "FULL",
                    "quantity_to_exit": current_qty,
                    "exit_price": active_sl,
                    "exit_reason": reason,
                    "result_code": code,
                    "updated_position": position
                }

        # -------------------------------------------------------------
        # LAYER 2: DYNAMIC EXIT & QUANTITY-DEPENDENT SCALING
        # -------------------------------------------------------------
        # Check TP1
        tp1_reached = False
        if tp1 is not None and tp1 > 0:
            if (is_long and high_mark >= tp1) or (not is_long and low_mark <= tp1):
                tp1_reached = True

        if tp1_reached and state in ["OPEN", "HOLDING"]:
            position["tp1_hit"] = True

            # If no distinct TP2 target is configured, this is a single target trade -> Full TP exit
            has_tp2 = tp2 is not None and tp2 > 0 and ((is_long and tp2 > tp1) or (not is_long and tp2 < tp1))
            if not has_tp2:
                return {
                    "should_exit": True,
                    "exit_type": "FULL",
                    "quantity_to_exit": current_qty,
                    "exit_price": tp1,
                    "exit_reason": f"Take-Profit Target Hit at ${tp1:.2f}",
                    "result_code": "TP",
                    "updated_position": position
                }

            # Quantity-Dependent Logic for Multi-Tier TP1/TP2 setups:
            if total_qty == 1.0:
                # 1 CONTRACT: No partial exit. Activate Profit Protection Mode!
                # Move stop to breakeven + buffer (avoid giving profits back)
                buffer = global_config.profit_protection_buffer_atr * atr
                new_stop = round(entry_price + buffer if is_long else entry_price - buffer, 2)
                position["trailing_stop"] = new_stop
                position["state"] = "PROFIT_PROTECTION"
                position["profit_protection_active"] = True

                # Do NOT exit yet; aim for TP2 with protected profit
                # Unless this is 0DTE (where 1 contract takes profit aggressively)
                if is_0dte:
                    return {
                        "should_exit": True,
                        "exit_type": "FULL",
                        "quantity_to_exit": 1.0,
                        "exit_price": tp1,
                        "exit_reason": f"0DTE Profit Take at TP1 (${tp1:.2f})",
                        "result_code": "TP1",
                        "updated_position": position
                    }

            elif total_qty == 2.0:
                # 2 CONTRACTS: Close 1 contract (50%), protect remaining 1 contract
                position["quantity"] = 1.0
                position["state"] = "RUNNER"
                position["trailing_stop"] = round(entry_price + 0.10 * atr if is_long else entry_price - 0.10 * atr, 2)
                position["profit_protection_active"] = True

                return {
                    "should_exit": True,
                    "exit_type": "PARTIAL",
                    "quantity_to_exit": 1.0,
                    "exit_price": tp1,
                    "exit_reason": f"TP1 Hit (${tp1:.2f}): Partial close 1 contract (50%), runner protected at breakeven.",
                    "result_code": "TP1",
                    "updated_position": position
                }

            elif total_qty >= 3.0:
                # 3+ CONTRACTS: Close 50% at TP1
                qty_to_close = float(int(total_qty * global_config.tp1_allocation_multi))
                qty_to_close = max(1.0, min(qty_to_close, current_qty - 1.0))
                position["quantity"] = current_qty - qty_to_close
                position["state"] = "RUNNER"
                position["trailing_stop"] = round(entry_price + 0.10 * atr if is_long else entry_price - 0.10 * atr, 2)
                position["profit_protection_active"] = True

                return {
                    "should_exit": True,
                    "exit_type": "PARTIAL",
                    "quantity_to_exit": qty_to_close,
                    "exit_price": tp1,
                    "exit_reason": f"TP1 Hit (${tp1:.2f}): Partial close {qty_to_close:.0f} contracts ({qty_to_close/total_qty*100:.0f}%).",
                    "result_code": "TP1",
                    "updated_position": position
                }

        # Check TP2
        if tp2 is not None and tp2 > 0:
            tp2_reached = (is_long and high_mark >= tp2) or (not is_long and low_mark <= tp2)
            if tp2_reached:
                if total_qty >= 5.0 and current_qty > 1.0:
                    # Scaled exit: close 30% of total, leave 20% runner
                    qty_to_close = float(int(total_qty * global_config.tp2_allocation_multi))
                    qty_to_close = min(qty_to_close, current_qty - 1.0)
                    if qty_to_close > 0:
                        position["quantity"] = current_qty - qty_to_close
                        position["trailing_stop"] = tp1  # lock in TP1 on remaining runner
                        return {
                            "should_exit": True,
                            "exit_type": "PARTIAL",
                            "quantity_to_exit": qty_to_close,
                            "exit_price": tp2,
                            "exit_reason": f"TP2 Hit (${tp2:.2f}): Scaled close {qty_to_close:.0f} contracts.",
                            "result_code": "TP2",
                            "updated_position": position
                        }

                # Otherwise, full closure at TP2
                return {
                    "should_exit": True,
                    "exit_type": "FULL",
                    "quantity_to_exit": current_qty,
                    "exit_price": tp2,
                    "exit_reason": f"Take-Profit 2 Target Hit at ${tp2:.2f}",
                    "result_code": "TP2",
                    "updated_position": position
                }

        # Profit Protection Ratchet (even before TP1 if MFE is strong >= 1.2 ATR)
        if mfe >= (global_config.profit_protection_threshold_atr * atr):
            if not position.get("profit_protection_active"):
                buffer = global_config.profit_protection_buffer_atr * atr
                new_stop = round(entry_price + buffer if is_long else entry_price - buffer, 2)
                position["trailing_stop"] = new_stop
                position["profit_protection_active"] = True
                position["state"] = "PROFIT_PROTECTION"

        # Trailing stop advancement if price advances further
        if position.get("profit_protection_active") and mfe >= (global_config.trailing_stop_activation_atr * atr):
            trail_dist = 1.0 * atr
            dynamic_trail = round(high_mark - trail_dist if is_long else low_mark + trail_dist, 2)
            curr_trail = position.get("trailing_stop")
            if curr_trail is not None:
                if (is_long and dynamic_trail > curr_trail) or (not is_long and dynamic_trail < curr_trail):
                    position["trailing_stop"] = dynamic_trail

        # -------------------------------------------------------------
        # LAYER 3: THESIS & ROTATION EXIT
        # -------------------------------------------------------------
        rotation = RotationEngine.calculate_rotation(side, current_features, current_price=current_price)
        position["rotation_score"] = rotation["rotation_score"]
        position["rotation_stage"] = rotation["stage"]

        # If in profit protection or runner mode, and strong opposite rotation occurs (>= 70)
        is_protected_or_runner = position.get("profit_protection_active") or state in ["PROFIT_PROTECTION", "RUNNER"]
        if is_protected_or_runner and rotation["is_strong"]:
            return {
                "should_exit": True,
                "exit_type": "FULL",
                "quantity_to_exit": current_qty,
                "exit_price": current_price,
                "exit_reason": f"Strong Adverse Rotation ({rotation['rotation_score']:.1f}/100) after profit lock-in. Protected exit.",
                "result_code": "ROTATION_EXIT",
                "updated_position": position,
                "rotation": rotation
            }

        # Thesis Invalidation (Complete opposite reversal >= 85)
        if rotation["is_reversal"]:
            return {
                "should_exit": True,
                "exit_type": "FULL",
                "quantity_to_exit": current_qty,
                "exit_price": current_price,
                "exit_reason": f"Original trade thesis invalidated by full market reversal ({rotation['rotation_score']:.1f}/100).",
                "result_code": "THESIS_EXIT",
                "updated_position": position,
                "rotation": rotation
            }

        # Time-Based Exit (Stagnant position check)
        bars_held = position.get("bars_held", 0) + 1
        position["bars_held"] = bars_held
        max_stag = getattr(global_config, "max_stagnant_bars", 20)
        if bars_held >= max_stag:
            if mfe < (global_config.stagnant_threshold_atr * atr):
                return {
                    "should_exit": True,
                    "exit_type": "FULL",
                    "quantity_to_exit": current_qty,
                    "exit_price": current_price,
                    "exit_reason": f"Time Exit: Trade stagnant after {bars_held} bars without directional expansion.",
                    "result_code": "TIME_EXIT",
                    "updated_position": position,
                    "rotation": rotation
                }

        # 0DTE Afternoon Cutoff
        if is_0dte:
            now_et = datetime.now(timezone.utc)  # approximate or parsed ET
            # If 0DTE trade is past cutoff, close position
            if position.get("force_0dte_exit"):
                return {
                    "should_exit": True,
                    "exit_type": "FULL",
                    "quantity_to_exit": current_qty,
                    "exit_price": current_price,
                    "exit_reason": "0DTE Session Cutoff: Exited before end-of-day theta collapse.",
                    "result_code": "TIME_EXIT",
                    "updated_position": position,
                    "rotation": rotation
                }

        return {
            "should_exit": False,
            "exit_type": "NONE",
            "quantity_to_exit": 0.0,
            "exit_price": current_price,
            "exit_reason": "Position healthy; holding.",
            "result_code": "NONE",
            "updated_position": position,
            "rotation": rotation
        }
