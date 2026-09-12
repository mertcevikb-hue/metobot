"""
Centralized Risk Controller with volatility-based position sizing.

For position state management, see engine/position_state.py
"""
from typing import Dict, Any, List
from engine.position_state import PositionStateMachine

class RiskEngine:
    @staticmethod
    def evaluate_risk(
        price: float,
        atr: float,
        side: str,
        account_size: float = 100000.0,
        risk_per_trade_pct: float = 0.015
    ) -> Dict[str, Any]:
        if price <= 0 or atr <= 0:
            return {"approved": False, "reason": "Invalid non-positive price or zero volatility."}
            
        is_long = side != "SHORT"
        stop_distance = 1.75 * atr
        target_distance = 3.50 * atr
        
        stop_loss = round(price - stop_distance if is_long else price + stop_distance, 2)
        take_profit = round(price + target_distance if is_long else price - target_distance, 2)
        reward_risk_ratio = round(target_distance / stop_distance, 2)
        
        # Volatility Parity Sizing
        dollar_risk = account_size * risk_per_trade_pct
        shares_to_trade = int(dollar_risk / max(stop_distance, 1e-4))
        position_notional = round(shares_to_trade * price, 2)
        risk_level = "LOW" if atr / price < 0.02 else ("MEDIUM" if atr / price < 0.045 else "HIGH")

        if side not in ("LONG", "SHORT"):
            return {
                "approved": False,
                "reason": "No directional bias.",
                "entry_price": round(price, 2),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reward_risk_ratio": reward_risk_ratio,
                "position_units": shares_to_trade,
                "position_notional": position_notional,
                "risk_level": risk_level
            }

        # Risk Gate Checks
        if (stop_distance / price) > 0.08:
            return {
                "approved": False,
                "reason": f"Excessive volatility: Stop distance exceeds 8.0% of underlying ({round((stop_distance/price)*100, 2)}%).",
                "entry_price": round(price, 2),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reward_risk_ratio": reward_risk_ratio,
                "position_units": shares_to_trade,
                "position_notional": position_notional,
                "risk_level": risk_level
            }
        if reward_risk_ratio < 1.8:
            return {
                "approved": False,
                "reason": f"Insufficient reward-to-risk ratio ({reward_risk_ratio} < 1.80 benchmark).",
                "entry_price": round(price, 2),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reward_risk_ratio": reward_risk_ratio,
                "position_units": shares_to_trade,
                "position_notional": position_notional,
                "risk_level": risk_level
            }

        return {
            "approved": True,
            "entry_price": round(price, 2),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reward_risk_ratio": reward_risk_ratio,
            "position_units": shares_to_trade,
            "position_notional": position_notional,
            "risk_level": risk_level
        }
