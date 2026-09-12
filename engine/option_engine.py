"""
Option Engine - Specialized quantitative analysis and scoring for Options contracts.
Evaluates Delta, Gamma, Theta, Vega, IV, DTE, 0DTE risk, Bid/Ask spread friction,
and Expected Move. Treats option premium dynamics as financially distinct from spot equity.
"""
from typing import Dict, Any, Optional
from datetime import datetime, date, timezone
import math
from engine.market_hours import MarketSchedule, TRADING_MINUTES_PER_DAY


class OptionEngine:
    """Evaluates option-specific metrics and generates a pure Option Score (0 - 100)."""

    @staticmethod
    def calculate_dte(expiration_date_str: str, dt: Optional[datetime] = None) -> int:
        """Calculates Days to Expiration (DTE) from YYYY-MM-DD string using US/Eastern calendar."""
        if not expiration_date_str or expiration_date_str in ("N/A", "Data unavailable"):
            return 7  # default standard weekly
        try:
            exp_date = datetime.strptime(str(expiration_date_str)[:10], "%Y-%m-%d").date()
            today = MarketSchedule.get_us_market_time(dt).date()
            dte = (exp_date - today).days
            return max(0, dte)
        except Exception:
            return 7

    @classmethod
    def estimate_greeks(
        cls,
        spot: float,
        strike: float,
        dte: int,
        iv: float,
        is_call: bool,
        r: float = 0.045,
        t_years: Optional[float] = None,
        minutes_to_expiry: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Analytical Black-Scholes approximations for Delta, Gamma, Theta, Vega.
        For 0DTE contracts, utilizes true intraday continuous time fraction (t_years)
        capturing accelerating gamma spikes and rapid theta decay into market close.
        """
        if spot <= 0 or strike <= 0 or iv <= 0:
            return {
                "delta": 0.50 if is_call else -0.50,
                "gamma": 0.0,
                "theta": -0.05,
                "vega": 0.10,
                "theta_hourly": -0.008,
                "t_years": 0.001
            }

        # Determine exact continuous time fraction t (in years)
        if t_years is not None and t_years > 0:
            t = max(t_years, 1e-5)
        elif dte == 0:
            eff_mins = max(minutes_to_expiry if minutes_to_expiry is not None else 60.0, 1.0)
            t = eff_mins / (252.0 * TRADING_MINUTES_PER_DAY)
        else:
            t = max(dte / 365.0, 1.0 / 365.0)

        vol_sqrt_t = iv * math.sqrt(t)

        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / vol_sqrt_t
            d2 = d1 - vol_sqrt_t

            # Standard Normal Cumulative Distribution Function approximation
            def norm_cdf(x):
                return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

            def norm_pdf(x):
                return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

            pdf_d1 = norm_pdf(d1)

            if is_call:
                delta = norm_cdf(d1)
                theta_ann = -(spot * pdf_d1 * iv) / (2.0 * math.sqrt(t)) - r * strike * math.exp(-r * t) * norm_cdf(d2)
            else:
                delta = norm_cdf(d1) - 1.0
                theta_ann = -(spot * pdf_d1 * iv) / (2.0 * math.sqrt(t)) + r * strike * math.exp(-r * t) * norm_cdf(-d2)

            # Gamma surges for ATM options as t -> 0
            gamma = pdf_d1 / (spot * vol_sqrt_t)

            # Vega drops for 0DTE options as t -> 0
            vega = (spot * math.sqrt(t) * pdf_d1) / 100.0  # per 1% change in IV

            # Daily and hourly theta decay per share
            theta_daily = theta_ann / 252.0
            theta_hourly = theta_daily / 6.5

            return {
                "delta": round(delta, 3),
                "gamma": round(gamma, 4),
                "theta": round(theta_daily, 3),
                "theta_hourly": round(theta_hourly, 4),
                "vega": round(vega, 3),
                "t_years": round(t, 6)
            }
        except Exception:
            return {
                "delta": 0.50 if is_call else -0.50,
                "gamma": 0.0,
                "theta": -0.05,
                "theta_hourly": -0.008,
                "vega": 0.10,
                "t_years": round(t, 6)
            }

    @classmethod
    def calculate_expected_move(
        cls,
        spot_price: float,
        iv: float,
        is_0dte: bool,
        minutes_to_expiry: float,
        dte: int
    ) -> Dict[str, float]:
        """
        Calculates dedicated 0DTE-aware Expected Move.
        10:00 ET (360 mins remaining) vs 15:00 ET (60 mins remaining) produce fundamentally different moves.
        """
        safe_iv = max(iv, 0.05)
        if is_0dte:
            eff_mins = max(minutes_to_expiry, 1.0)
            fraction_of_year = eff_mins / (252.0 * TRADING_MINUTES_PER_DAY)
            em = spot_price * safe_iv * math.sqrt(fraction_of_year)
        else:
            eff_days = max(dte, 1.0)
            fraction_of_year = eff_days / 365.0
            em = spot_price * safe_iv * math.sqrt(fraction_of_year)

        expected_move = round(em, 2)
        return {
            "expected_move": expected_move,
            "expected_move_pct": round((expected_move / spot_price) * 100.0, 2) if spot_price > 0 else 0.0,
            "expected_move_high": round(spot_price + expected_move, 2),
            "expected_move_low": round(max(0.01, spot_price - expected_move), 2)
        }

    @classmethod
    def evaluate(
        cls,
        spot_price: float,
        spot_atr: float,
        direction_bias: str,
        option_quote: Optional[Dict[str, Any]] = None,
        contract_meta: Optional[Dict[str, Any]] = None,
        dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Evaluates option-specific quality, liquidity, Greeks, and 0DTE risks.
        Applies rigorous intraday liquidity and gamma/theta gating.
        """
        if not option_quote and not contract_meta:
            return {
                "instrument": "OPTION",
                "status": "DATA_UNAVAILABLE",
                "option_score": 0.0,
                "valid": False,
                "reason": "Option chain or quote data unavailable."
            }

        q = option_quote or {}
        m = contract_meta or {}

        contract_ticker = m.get("ticker") or q.get("ticker", "N/A")
        raw_strike = m.get("strike_price") or q.get("strike_price")
        strike = float(raw_strike) if raw_strike else spot_price
        exp_date = m.get("expiration_date") or q.get("expiration_date", "N/A")

        bid = float(q.get("bid_price", 0.0) or 0.0)
        ask = float(q.get("ask_price", 0.0) or 0.0)
        last = float(q.get("last_trade_price", 0.0) or 0.0)

        # Implied Volatility
        raw_iv = q.get("implied_volatility") or m.get("implied_volatility")
        iv = float(raw_iv) if (raw_iv is not None and float(raw_iv) > 0) else 0.30

        # Volume & Open Interest
        vol = int(m.get("volume") or q.get("day", {}).get("volume") or q.get("volume") or 0)
        oi = int(m.get("open_interest") or q.get("open_interest") or 1)

        # Premium calculation
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (last if last > 0 else 1.0)
        spread_abs = max(ask - bid, 0.0) if (bid > 0 and ask > 0) else 0.0
        spread_friction_pct = (spread_abs / mid * 100.0) if mid > 0 else 100.0

        # Intraday 0DTE Time Model Calculations
        dte_metrics = MarketSchedule.calculate_0dte_metrics(exp_date, dt)
        dte = dte_metrics["dte"]
        is_0dte = dte_metrics["is_0dte"]
        minutes_to_expiry = dte_metrics["minutes_to_expiry"]
        t_years = dte_metrics["time_to_expiry_years"]
        time_bucket = dte_metrics["time_of_day_bucket"]

        is_call = direction_bias != "BEARISH"
        greeks = cls.estimate_greeks(
            spot=spot_price,
            strike=strike,
            dte=dte,
            iv=iv,
            is_call=is_call,
            t_years=t_years,
            minutes_to_expiry=minutes_to_expiry
        )

        # 0DTE-aware Intraday Expected Move
        em_data = cls.calculate_expected_move(
            spot_price=spot_price,
            iv=iv,
            is_0dte=is_0dte,
            minutes_to_expiry=minutes_to_expiry,
            dte=dte
        )
        expected_move = em_data["expected_move"]

        # Moneyness & Distance from Spot
        strike_dist_pct = ((strike - spot_price) / spot_price) * 100.0 if spot_price > 0 else 0.0
        strike_dist_atr = round(abs(strike - spot_price) / max(spot_atr, 1e-4), 2)
        strike_dist_em_ratio = round(abs(strike - spot_price) / max(expected_move, 0.01), 2)

        # -------------------------------------------------------------
        # 1. Liquidity & Spread Friction Score (35% Weight)
        # -------------------------------------------------------------
        # 0DTE requires tighter spreads because time decay does not forgive execution drag
        if is_0dte:
            if spread_friction_pct <= 2.5:
                liq_score = 100.0
            elif spread_friction_pct <= 5.0:
                liq_score = 85.0
            elif spread_friction_pct <= 8.0:
                liq_score = 65.0
            elif spread_friction_pct <= 12.0:
                liq_score = 35.0
            else:
                liq_score = 10.0
        else:
            if spread_friction_pct <= 3.0:
                liq_score = 100.0
            elif spread_friction_pct <= 6.0:
                liq_score = 85.0
            elif spread_friction_pct <= 10.0:
                liq_score = 70.0
            elif spread_friction_pct <= 15.0:
                liq_score = 45.0
            else:
                liq_score = 15.0

        # -------------------------------------------------------------
        # 2. Institutional Flow Metric (Volume / OI) (20% Weight)
        # -------------------------------------------------------------
        vol_oi_ratio = vol / max(oi, 1)
        if is_0dte:
            # On 0DTE, daily volume often exceeds OI significantly
            flow_score = min(vol_oi_ratio * 30.0 + (min(vol, 5000) / 100.0), 100.0)
        else:
            flow_score = min(vol_oi_ratio * 40.0 + 30.0, 100.0)

        # -------------------------------------------------------------
        # 3. Greeks & Delta Appropriateness (20% Weight)
        # -------------------------------------------------------------
        abs_delta = abs(greeks["delta"])
        if 0.40 <= abs_delta <= 0.65:
            delta_score = 95.0
        elif 0.30 <= abs_delta < 0.40 or 0.65 < abs_delta <= 0.75:
            delta_score = 75.0
        elif abs_delta < 0.20:
            delta_score = 25.0  # Far OTM lottery ticket
        else:
            delta_score = 60.0  # Deep ITM

        # -------------------------------------------------------------
        # 4. Volatility & IV Pricing (10% Weight)
        # -------------------------------------------------------------
        if iv > 0.85:
            iv_score = 40.0  # Extreme IV crush risk
        elif iv < 0.15:
            iv_score = 70.0
        else:
            iv_score = 90.0

        # -------------------------------------------------------------
        # 5. 0DTE Risk & Time Remaining Score (15% Weight)
        # -------------------------------------------------------------
        if is_0dte:
            # Grade risk based on intraday time remaining and gamma hazard
            if time_bucket == "EXPIRATION_WINDOW":
                zero_dte_score = 10.0  # Last 30 minutes: extreme danger zone
            elif time_bucket == "AFTERNOON":
                zero_dte_score = 45.0 if spread_friction_pct <= 5.0 else 20.0
            elif time_bucket == "MIDDAY":
                zero_dte_score = 70.0 if spread_friction_pct <= 6.0 else 40.0
            elif time_bucket == "MORNING":
                zero_dte_score = 85.0 if spread_friction_pct <= 7.0 else 55.0
            else:  # OPEN
                zero_dte_score = 75.0
        else:
            zero_dte_score = 90.0

        composite_score = round(
            (liq_score * 0.35) +
            (flow_score * 0.20) +
            (delta_score * 0.20) +
            (iv_score * 0.10) +
            (zero_dte_score * 0.15),
            1
        )

        # Liquidity threshold: for 0DTE, require spread < 10% and active market presence
        max_allowed_spread = 10.0 if is_0dte else 15.0
        is_liquid = (spread_friction_pct <= max_allowed_spread) and (bid > 0 or last > 0) and (vol > 0 or oi > 10)

        # Gamma risk warning
        gamma_risk = "HIGH" if (is_0dte and greeks["gamma"] > 0.05) else ("MEDIUM" if is_0dte else "LOW")

        # Multi-leg Strategy Recommendation and Pricing
        from engine.option_strategy_library import OptionStrategyLibrary
        rec_strat = OptionStrategyLibrary.select_optimal_strategy(
            spot_price=spot_price,
            direction_bias=direction_bias,
            regime="TRENDING_BULL" if is_call else "TRENDING_BEAR",
            iv=iv,
            expected_move=expected_move,
            dte=dte,
            is_0dte=is_0dte,
            time_bucket=time_bucket,
            t_years=t_years
        )
        multi_leg_structure = OptionStrategyLibrary.evaluate_strategy(
            strategy_name=rec_strat,
            spot_price=spot_price,
            iv=iv,
            expected_move=expected_move,
            dte=dte,
            is_0dte=is_0dte,
            primary_strike=strike,
            primary_mid=mid,
            t_years=t_years
        )

        return {
            "instrument": "OPTION",
            "contract_ticker": contract_ticker,
            "contract_type": "CALL" if is_call else "PUT",
            "strike": strike,
            "expiration": exp_date,
            "dte": dte,
            "is_0dte": is_0dte,
            "time_to_expiry_years": t_years,
            "minutes_to_expiry": minutes_to_expiry,
            "hours_to_expiry": dte_metrics["hours_to_expiry"],
            "session_progress": dte_metrics["session_progress"],
            "fraction_of_trading_day_remaining": dte_metrics["fraction_of_trading_day_remaining"],
            "time_of_day_bucket": time_bucket,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "last": round(last, 2),
            "mid": round(mid, 2),
            "premium": round(mid, 2),
            "spread_abs": round(spread_abs, 2),
            "spread_friction_pct": round(spread_friction_pct, 2),
            "is_liquid": is_liquid,
            "iv": round(iv, 4),
            "iv_pct_str": f"{iv * 100:.1f}%",
            "volume": vol,
            "open_interest": oi,
            "vol_oi_ratio": round(vol_oi_ratio, 2),
            "expected_move": expected_move,
            "expected_move_pct": em_data["expected_move_pct"],
            "expected_move_high": em_data["expected_move_high"],
            "expected_move_low": em_data["expected_move_low"],
            "strike_dist_atr": strike_dist_atr,
            "strike_dist_em_ratio": strike_dist_em_ratio,
            "greeks": greeks,
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "gamma_risk": gamma_risk,
            "theta": greeks["theta"],
            "theta_hourly": greeks["theta_hourly"],
            "vega": greeks["vega"],
            "preferred_strategy": rec_strat,
            "multi_leg_structure": multi_leg_structure,
            "option_score": composite_score,
            "status": "Active" if is_liquid else "Illiquid",
            "valid": is_liquid and composite_score >= 50.0 and (time_bucket != "EXPIRATION_WINDOW" if is_0dte else True)
        }
