"""
Option Engine - Specialized quantitative analysis and scoring for Options contracts.
Evaluates Delta, Gamma, Theta, Vega, IV, DTE, 0DTE risk, Bid/Ask spread friction,
and Expected Move. Treats option premium dynamics as financially distinct from spot equity.
"""
from typing import Dict, Any, Optional
from datetime import datetime, date, timezone
import math


class OptionEngine:
    """Evaluates option-specific metrics and generates a pure Option Score (0 - 100)."""

    @staticmethod
    def calculate_dte(expiration_date_str: str) -> int:
        """Calculates Days to Expiration (DTE) from YYYY-MM-DD string."""
        if not expiration_date_str or expiration_date_str in ("N/A", "Data unavailable"):
            return 7  # default standard weekly
        try:
            exp_date = datetime.strptime(expiration_date_str[:10], "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
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
        r: float = 0.045
    ) -> Dict[str, float]:
        """
        Analytical Black-Scholes approximations for Delta, Gamma, Theta, Vega.
        Used when exchange quote does not return direct Greeks.
        """
        if spot <= 0 or strike <= 0 or iv <= 0:
            return {"delta": 0.50 if is_call else -0.50, "gamma": 0.0, "theta": -0.05, "vega": 0.10}

        t = max(dte / 365.0, 1.0 / 365.0)  # at least 1 day for 0DTE in annual terms
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

            gamma = pdf_d1 / (spot * vol_sqrt_t)
            vega = (spot * math.sqrt(t) * pdf_d1) / 100.0  # per 1% change in IV
            theta = theta_ann / 365.0  # daily dollar decay per share

            return {
                "delta": round(delta, 3),
                "gamma": round(gamma, 4),
                "theta": round(theta, 3),
                "vega": round(vega, 3)
            }
        except Exception:
            return {"delta": 0.50 if is_call else -0.50, "gamma": 0.0, "theta": -0.05, "vega": 0.10}

    @classmethod
    def evaluate(
        cls,
        spot_price: float,
        spot_atr: float,
        direction_bias: str,
        option_quote: Optional[Dict[str, Any]] = None,
        contract_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Evaluates option-specific quality, liquidity, Greeks, and 0DTE risks."""
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
        iv = float(raw_iv) if raw_iv is not None else 0.30

        # Volume & Open Interest
        vol = int(m.get("volume") or q.get("day", {}).get("volume") or q.get("volume") or 0)
        oi = int(m.get("open_interest") or q.get("open_interest") or 1)

        # Premium calculation
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (last if last > 0 else 1.0)
        spread_abs = max(ask - bid, 0.0) if (bid > 0 and ask > 0) else 0.0
        spread_friction_pct = (spread_abs / mid * 100.0) if mid > 0 else 100.0

        # DTE & 0DTE Evaluation
        dte = cls.calculate_dte(exp_date)
        is_0dte = (dte == 0)

        is_call = direction_bias != "BEARISH"
        greeks = cls.estimate_greeks(spot_price, strike, dte, iv, is_call)

        # Expected Move in underlying = Spot * IV * sqrt(DTE / 365)
        eff_dte = max(dte, 0.5)
        expected_move = round(spot_price * iv * math.sqrt(eff_dte / 365.0), 2)

        # Moneyness & Distance from Spot
        strike_dist_pct = ((strike - spot_price) / spot_price) * 100.0 if spot_price > 0 else 0.0
        strike_dist_atr = round(abs(strike - spot_price) / max(spot_atr, 1e-4), 2)

        # -------------------------------------------------------------
        # 1. Liquidity & Spread Friction Score (40% Weight)
        # -------------------------------------------------------------
        # Options with > 12% spread suffer severe slippage drag
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
        flow_score = min(vol_oi_ratio * 40.0 + 30.0, 100.0)

        # -------------------------------------------------------------
        # 3. Greeks & Delta Appropriateness (20% Weight)
        # -------------------------------------------------------------
        # Target sweet spot: 0.40 - 0.65 delta (At-The-Money / Near-The-Money)
        abs_delta = abs(greeks["delta"])
        if 0.40 <= abs_delta <= 0.65:
            delta_score = 95.0
        elif 0.30 <= abs_delta < 0.40 or 0.65 < abs_delta <= 0.75:
            delta_score = 75.0
        elif abs_delta < 0.20:
            delta_score = 30.0  # Far OTM lottery ticket
        else:
            delta_score = 65.0  # Deep ITM

        # -------------------------------------------------------------
        # 4. Volatility & IV Pricing (10% Weight)
        # -------------------------------------------------------------
        if iv > 0.85:
            iv_score = 40.0  # Extreme IV crush risk
        elif iv < 0.15:
            iv_score = 65.0
        else:
            iv_score = 90.0

        # -------------------------------------------------------------
        # 5. 0DTE Risk Adjustment (10% Weight)
        # -------------------------------------------------------------
        if is_0dte:
            # 0DTE contracts face severe rapid decay and gamma risk
            if spread_friction_pct > 8.0:
                zero_dte_score = 30.0  # Wide spread on 0DTE is lethal
            else:
                zero_dte_score = 70.0
        else:
            zero_dte_score = 90.0

        composite_score = round(
            (liq_score * 0.40) +
            (flow_score * 0.20) +
            (delta_score * 0.20) +
            (iv_score * 0.10) +
            (zero_dte_score * 0.10),
            1
        )

        is_liquid = spread_friction_pct < 15.0 and (bid > 0 or last > 0)

        return {
            "instrument": "OPTION",
            "contract_ticker": contract_ticker,
            "contract_type": "CALL" if is_call else "PUT",
            "strike": strike,
            "expiration": exp_date,
            "dte": dte,
            "is_0dte": is_0dte,
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
            "strike_dist_atr": strike_dist_atr,
            "greeks": greeks,
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "vega": greeks["vega"],
            "option_score": composite_score,
            "status": "Active" if is_liquid else "Illiquid",
            "valid": is_liquid and composite_score >= 50.0
        }
