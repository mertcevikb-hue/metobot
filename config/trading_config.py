"""
Trading Configuration - Centralized parameters for Metobot 0.5.
Eliminates hard-coded constants across engines.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, Any


@dataclass
class TradingConfig:
    # Portfolio & Balance
    paper_starting_balance: float = 100000.0
    risk_per_trade_pct: float = 0.015  # 1.5% risk per trade
    daily_loss_limit_pct: float = 0.05  # 5% max daily account loss
    max_portfolio_exposure_pct: float = 0.30  # 30% max total capital deployed

    # Direction & Confidence Thresholds
    minimum_confidence: float = 75.0  # Min final model confidence (0 - 100)
    min_direction_edge: float = 15.0  # Min abs(Bullish Score - Bearish Score)
    direction_conflict_threshold: float = 55.0  # If both Bull & Bear > 55, mark chop/conflict

    # Exposure & Position Limits
    max_positions_per_underlying: int = 1
    max_call_positions_per_underlying: int = 1
    max_put_positions_per_underlying: int = 1
    allow_simultaneous_hedges: bool = False  # If true, allows 1 CALL + 1 PUT simultaneously
    cooldown_minutes: int = 15  # Cooldown after position exit before re-entry

    # Dynamic TP / SL & Exit Parameters
    tp1_atr_multiple: float = 1.75  # Target 1: ~1.75 ATR
    tp2_atr_multiple: float = 3.50  # Target 2: ~3.50 ATR
    sl_atr_multiple: float = 1.75   # Stop loss: ~1.75 ATR
    min_reward_risk_ratio: float = 1.80

    # Quantity-Dependent Scaling Allocations
    tp1_allocation_2contract: float = 0.50  # 1 of 2 contracts
    tp1_allocation_multi: float = 0.50      # 50% of multi-contracts at TP1
    tp2_allocation_multi: float = 0.30      # 30% of multi-contracts at TP2
    runner_allocation_multi: float = 0.20   # 20% trailing runner

    # Profit Protection
    profit_protection_threshold_atr: float = 1.20  # Ratchet stop to breakeven once MFE >= 1.2 ATR
    profit_protection_buffer_atr: float = 0.25     # Stop moved to Entry + 0.25 ATR in protection mode
    trailing_stop_activation_atr: float = 2.0      # Activate trailing stop after +2.0 ATR

    # Rotation & Thesis Monitoring
    rotation_warning_threshold: float = 50.0       # Rotation score 50: warning
    rotation_strong_threshold: float = 70.0        # Rotation score 70: strong rotation, exit remaining runner
    rotation_reversal_threshold: float = 85.0      # Rotation score 85: full thesis invalidation

    # Time-Based Exits
    max_holding_bars_equity: int = 60              # 60 1-min bars (~1 hr) before stagnant check
    max_stagnant_bars: int = 20                    # 20 bars without progress triggers stagnant time exit
    stagnant_threshold_atr: float = 0.40           # If price moved < 0.4 ATR in 20 bars, mark stagnant

    # 0DTE Parameters
    zero_dte_cutoff_time_et: str = "15:15"         # Exit 0DTE trades before 15:15 ET (theta crush)
    zero_dte_max_spread_pct: float = 8.0           # Stricter spread friction tolerance for 0DTE (%8)
    zero_dte_tp1_pct: float = 25.0                 # +25% option premium TP1
    zero_dte_tp2_pct: float = 60.0                 # +60% option premium TP2
    zero_dte_sl_pct: float = 25.0                  # -25% option premium SL

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def update_from_dict(self, updates: Dict[str, Any]) -> None:
        for k, v in updates.items():
            if hasattr(self, k):
                t = type(getattr(self, k))
                try:
                    setattr(self, k, t(v))
                except (ValueError, TypeError):
                    pass


# Global singleton configuration instance
global_config = TradingConfig()
