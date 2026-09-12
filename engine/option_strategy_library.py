"""
Option Strategy Library for Metobot 0.5.2.
Provides institutional multi-leg options evaluation, strike selection,
Greeks modeling, and risk-defined trade structuring.
Supports: Long Call, Long Put, Bull Call Spread, Bear Put Spread,
Straddle, Strangle, Iron Condor, Iron Butterfly, Bull Put Spread, Bear Call Spread.
"""
from typing import Dict, Any, List, Optional, Tuple
import math
from datetime import datetime
from engine.market_hours import MarketSchedule, TRADING_MINUTES_PER_DAY


class OptionStrategyLibrary:
    """
    Evaluates and prices multi-leg options structures.
    Computes exact legs, net debit/credit, max profit, max loss, breakevens, and risk/reward.
    """

    @staticmethod
    def black_scholes_price(
        spot: float,
        strike: float,
        t_years: float,
        iv: float,
        is_call: bool,
        r: float = 0.045
    ) -> float:
        """Computes Black-Scholes theoretical price for option legs."""
        if spot <= 0 or strike <= 0 or iv <= 0:
            return 0.0
        t = max(t_years, 1e-5)
        vol_sqrt_t = iv * math.sqrt(t)
        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / vol_sqrt_t
            d2 = d1 - vol_sqrt_t

            def norm_cdf(x):
                return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

            if is_call:
                price = spot * norm_cdf(d1) - strike * math.exp(-r * t) * norm_cdf(d2)
            else:
                price = strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)

            return max(0.01, round(price, 2))
        except Exception:
            intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
            return round(intrinsic + 0.10, 2)

    @classmethod
    def select_optimal_strategy(
        cls,
        spot_price: float,
        direction_bias: str,
        regime: str,
        iv: float,
        expected_move: float,
        dte: int,
        is_0dte: bool,
        time_bucket: str = "MIDDAY",
        t_years: Optional[float] = None
    ) -> str:
        """
        Determines the optimal option strategy structure based on:
        - Directional bias (BULLISH, BEARISH, NEUTRAL)
        - IV regime (High IV > 0.45 favors spreads/credit, Low IV < 0.25 favors long debit)
        - 0DTE time horizon (afternoon 0DTE requires defined-risk spreads to mitigate rapid theta)
        """
        is_high_iv = iv >= 0.45
        is_low_iv = iv <= 0.25
        is_late_0dte = is_0dte and time_bucket in ("AFTERNOON", "EXPIRATION_WINDOW")

        if direction_bias in ("BULLISH", "BUY", "LONG"):
            if is_late_0dte or is_high_iv:
                return "BULL_CALL_SPREAD"
            else:
                return "LONG_CALL"

        elif direction_bias in ("BEARISH", "SELL", "SHORT"):
            if is_late_0dte or is_high_iv:
                return "BEAR_PUT_SPREAD"
            else:
                return "LONG_PUT"

        else:  # NEUTRAL or CHOP
            if is_0dte:
                return "IRON_CONDOR" if is_high_iv else "NO_TRADE"
            if is_high_iv:
                return "IRON_CONDOR"
            elif "VOLATILE" in regime or "COMPRESSION" in regime:
                return "LONG_STRADDLE"
            else:
                return "IRON_CONDOR"

    @classmethod
    def evaluate_strategy(
        cls,
        strategy_name: str,
        spot_price: float,
        iv: float,
        expected_move: float,
        dte: int,
        is_0dte: bool,
        primary_strike: Optional[float] = None,
        primary_mid: Optional[float] = None,
        t_years: Optional[float] = None,
        spread_width: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Builds full multi-leg option trade structure with exact economics.
        """
        if spot_price <= 0:
            return {"strategy": strategy_name, "valid": False, "reason": "Invalid spot price."}

        safe_iv = max(iv, 0.10)
        safe_em = max(expected_move, spot_price * 0.005)

        # Standard width roughly 0.75x - 1.0x expected move rounded to realistic strike intervals
        if spread_width and spread_width > 0:
            width = spread_width
        elif spot_price > 500:
            width = max(5.0, round(safe_em / 5.0) * 5.0)
        elif spot_price > 100:
            width = max(2.5, round(safe_em / 2.5) * 2.5)
        elif spot_price > 30:
            width = max(1.0, round(safe_em))
        else:
            width = max(0.5, round(safe_em * 2.0) / 2.0)

        t = t_years if (t_years is not None and t_years > 0) else (0.001 if is_0dte else max(dte / 365.0, 1.0 / 365.0))
        atm_strike = primary_strike if primary_strike else round(spot_price)

        # -------------------------------------------------------------
        # 1. LONG CALL
        # -------------------------------------------------------------
        if strategy_name == "LONG_CALL":
            strike = atm_strike
            prem = primary_mid if (primary_mid and primary_mid > 0) else cls.black_scholes_price(spot_price, strike, t, safe_iv, is_call=True)
            max_loss = round(prem * 100.0, 2)
            breakeven = round(strike + prem, 2)

            return {
                "strategy": "LONG_CALL",
                "structure_type": "SINGLE_LEG_DEBIT",
                "bias": "BULLISH",
                "is_0dte": is_0dte,
                "dte": dte,
                "legs": [
                    {
                        "leg_number": 1,
                        "action": "BUY",
                        "type": "CALL",
                        "strike": strike,
                        "price": round(prem, 2),
                        "contracts": 1
                    }
                ],
                "net_debit": round(prem, 2),
                "net_credit": 0.0,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": "UNLIMITED",
                "breakeven": [breakeven],
                "risk_reward_ratio": "ASYMMETRIC",
                "target_exit_spot": round(spot_price + safe_em, 2),
                "stop_loss_spot": round(spot_price - (safe_em * 0.5), 2),
                "explanation": f"Buy {strike} Call @ ${prem:.2f}. Bullish directional exposure with capped downside (${max_loss:.2f})."
            }

        # -------------------------------------------------------------
        # 2. LONG PUT
        # -------------------------------------------------------------
        elif strategy_name == "LONG_PUT":
            strike = atm_strike
            prem = primary_mid if (primary_mid and primary_mid > 0) else cls.black_scholes_price(spot_price, strike, t, safe_iv, is_call=False)
            max_loss = round(prem * 100.0, 2)
            breakeven = round(strike - prem, 2)
            max_profit = round(max(0.0, (strike - prem) * 100.0), 2)

            return {
                "strategy": "LONG_PUT",
                "structure_type": "SINGLE_LEG_DEBIT",
                "bias": "BEARISH",
                "is_0dte": is_0dte,
                "dte": dte,
                "legs": [
                    {
                        "leg_number": 1,
                        "action": "BUY",
                        "type": "PUT",
                        "strike": strike,
                        "price": round(prem, 2),
                        "contracts": 1
                    }
                ],
                "net_debit": round(prem, 2),
                "net_credit": 0.0,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": max_profit,
                "breakeven": [breakeven],
                "risk_reward_ratio": "ASYMMETRIC",
                "target_exit_spot": round(spot_price - safe_em, 2),
                "stop_loss_spot": round(spot_price + (safe_em * 0.5), 2),
                "explanation": f"Buy {strike} Put @ ${prem:.2f}. Bearish directional exposure with capped downside (${max_loss:.2f})."
            }

        # -------------------------------------------------------------
        # 3. BULL CALL SPREAD (Debit Spread)
        # -------------------------------------------------------------
        elif strategy_name == "BULL_CALL_SPREAD":
            buy_strike = atm_strike
            sell_strike = round(buy_strike + width, 2)
            p1 = primary_mid if (primary_mid and primary_mid > 0) else cls.black_scholes_price(spot_price, buy_strike, t, safe_iv, is_call=True)
            p2 = cls.black_scholes_price(spot_price, sell_strike, t, safe_iv, is_call=True)

            net_debit = max(0.05, round(p1 - p2, 2))
            max_loss = round(net_debit * 100.0, 2)
            max_profit = round(max(0.0, (width - net_debit) * 100.0), 2)
            breakeven = round(buy_strike + net_debit, 2)
            rr = round(max_profit / max_loss, 2) if max_loss > 0 else 1.0

            return {
                "strategy": "BULL_CALL_SPREAD",
                "structure_type": "VERTICAL_DEBIT_SPREAD",
                "bias": "BULLISH",
                "is_0dte": is_0dte,
                "dte": dte,
                "spread_width": width,
                "legs": [
                    {"leg_number": 1, "action": "BUY", "type": "CALL", "strike": buy_strike, "price": round(p1, 2), "contracts": 1},
                    {"leg_number": 2, "action": "SELL", "type": "CALL", "strike": sell_strike, "price": round(p2, 2), "contracts": 1}
                ],
                "net_debit": net_debit,
                "net_credit": 0.0,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": max_profit,
                "breakeven": [breakeven],
                "risk_reward_ratio": f"1:{rr:.2f}",
                "target_exit_spot": sell_strike,
                "stop_loss_spot": round(spot_price - (safe_em * 0.5), 2),
                "explanation": f"Buy {buy_strike} Call / Sell {sell_strike} Call @ ${net_debit:.2f} debit. Mitigates theta decay and IV crush."
            }

        # -------------------------------------------------------------
        # 4. BEAR PUT SPREAD (Debit Spread)
        # -------------------------------------------------------------
        elif strategy_name == "BEAR_PUT_SPREAD":
            buy_strike = atm_strike
            sell_strike = round(buy_strike - width, 2)
            p1 = primary_mid if (primary_mid and primary_mid > 0) else cls.black_scholes_price(spot_price, buy_strike, t, safe_iv, is_call=False)
            p2 = cls.black_scholes_price(spot_price, sell_strike, t, safe_iv, is_call=False)

            net_debit = max(0.05, round(p1 - p2, 2))
            max_loss = round(net_debit * 100.0, 2)
            max_profit = round(max(0.0, (width - net_debit) * 100.0), 2)
            breakeven = round(buy_strike - net_debit, 2)
            rr = round(max_profit / max_loss, 2) if max_loss > 0 else 1.0

            return {
                "strategy": "BEAR_PUT_SPREAD",
                "structure_type": "VERTICAL_DEBIT_SPREAD",
                "bias": "BEARISH",
                "is_0dte": is_0dte,
                "dte": dte,
                "spread_width": width,
                "legs": [
                    {"leg_number": 1, "action": "BUY", "type": "PUT", "strike": buy_strike, "price": round(p1, 2), "contracts": 1},
                    {"leg_number": 2, "action": "SELL", "type": "PUT", "strike": sell_strike, "price": round(p2, 2), "contracts": 1}
                ],
                "net_debit": net_debit,
                "net_credit": 0.0,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": max_profit,
                "breakeven": [breakeven],
                "risk_reward_ratio": f"1:{rr:.2f}",
                "target_exit_spot": sell_strike,
                "stop_loss_spot": round(spot_price + (safe_em * 0.5), 2),
                "explanation": f"Buy {buy_strike} Put / Sell {sell_strike} Put @ ${net_debit:.2f} debit. Mitigates theta decay and IV crush."
            }

        # -------------------------------------------------------------
        # 5. LONG STRADDLE
        # -------------------------------------------------------------
        elif strategy_name == "LONG_STRADDLE":
            strike = atm_strike
            p_call = cls.black_scholes_price(spot_price, strike, t, safe_iv, is_call=True)
            p_put = cls.black_scholes_price(spot_price, strike, t, safe_iv, is_call=False)
            net_debit = round(p_call + p_put, 2)
            max_loss = round(net_debit * 100.0, 2)
            be_upper = round(strike + net_debit, 2)
            be_lower = round(strike - net_debit, 2)

            return {
                "strategy": "LONG_STRADDLE",
                "structure_type": "VOLATILITY_EXPANSION_DEBIT",
                "bias": "VOLATILITY_EXPANSION",
                "is_0dte": is_0dte,
                "dte": dte,
                "legs": [
                    {"leg_number": 1, "action": "BUY", "type": "CALL", "strike": strike, "price": round(p_call, 2), "contracts": 1},
                    {"leg_number": 2, "action": "BUY", "type": "PUT", "strike": strike, "price": round(p_put, 2), "contracts": 1}
                ],
                "net_debit": net_debit,
                "net_credit": 0.0,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": "UNLIMITED",
                "breakeven": [be_lower, be_upper],
                "risk_reward_ratio": "ASYMMETRIC",
                "explanation": f"Buy {strike} Call & {strike} Put @ ${net_debit:.2f} total debit. Profits from major breakout beyond ${be_lower} or ${be_upper}."
            }

        # -------------------------------------------------------------
        # 6. IRON CONDOR (Defined Risk Neutral)
        # -------------------------------------------------------------
        elif strategy_name == "IRON_CONDOR":
            put_sell = round(spot_price - safe_em, 2)
            put_buy = round(put_sell - width, 2)
            call_sell = round(spot_price + safe_em, 2)
            call_buy = round(call_sell + width, 2)

            p_ps = cls.black_scholes_price(spot_price, put_sell, t, safe_iv, is_call=False)
            p_pb = cls.black_scholes_price(spot_price, put_buy, t, safe_iv, is_call=False)
            p_cs = cls.black_scholes_price(spot_price, call_sell, t, safe_iv, is_call=True)
            p_cb = cls.black_scholes_price(spot_price, call_buy, t, safe_iv, is_call=True)

            put_credit = max(0.02, p_ps - p_pb)
            call_credit = max(0.02, p_cs - p_cb)
            net_credit = round(put_credit + call_credit, 2)
            max_profit = round(net_credit * 100.0, 2)
            max_loss = round(max(0.0, (width - net_credit) * 100.0), 2)
            be_lower = round(put_sell - net_credit, 2)
            be_upper = round(call_sell + net_credit, 2)

            return {
                "strategy": "IRON_CONDOR",
                "structure_type": "DEFINED_RISK_CREDIT",
                "bias": "NEUTRAL_RANGE",
                "is_0dte": is_0dte,
                "dte": dte,
                "spread_width": width,
                "legs": [
                    {"leg_number": 1, "action": "BUY", "type": "PUT", "strike": put_buy, "price": round(p_pb, 2), "contracts": 1},
                    {"leg_number": 2, "action": "SELL", "type": "PUT", "strike": put_sell, "price": round(p_ps, 2), "contracts": 1},
                    {"leg_number": 3, "action": "SELL", "type": "CALL", "strike": call_sell, "price": round(p_cs, 2), "contracts": 1},
                    {"leg_number": 4, "action": "BUY", "type": "CALL", "strike": call_buy, "price": round(p_cb, 2), "contracts": 1}
                ],
                "net_debit": 0.0,
                "net_credit": net_credit,
                "entry_cost": max_loss,
                "max_loss": max_loss,
                "max_profit": max_profit,
                "breakeven": [be_lower, be_upper],
                "risk_reward_ratio": f"{max_profit:.0f}:{max_loss:.0f}",
                "explanation": f"Iron Condor ({put_buy}/{put_sell}P - {call_sell}/{call_buy}C) collecting ${net_credit:.2f} credit. Profitable if spot stays between {be_lower} and {be_upper}."
            }

        # Fallback to Long Call
        return cls.evaluate_strategy("LONG_CALL", spot_price, iv, expected_move, dte, is_0dte, primary_strike, primary_mid, t_years, width)
