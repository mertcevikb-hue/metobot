"""
Position State Machine - Hysteresis-based state management for trading positions.

States:
- WATCH: Monitoring market, no setup forming
- SETUP_FORMING: Setup criteria partially met, waiting for confirmation
- ENTRY_READY: All conditions met, ready to enter
- ENTERED: Position entered, waiting for exit signal
- HOLDING: Position held, monitoring for exit
- EXIT_WARNING: Exit signal weak, exit not yet confirmed
- EXIT_READY: Exit signal confirmed, closing position
- INVALIDATED: Data or risk check failed, abort entry
"""

from typing import Dict, Any


class PositionStateMachine:
    """
    Manages position state transitions using hysteresis (memory).

    This prevents rapid flip-flopping between entry and exit signals
    by requiring scores to cross specific thresholds in each direction.
    """

    @staticmethod
    def transition(
        current_state: str,
        regime: str,
        score: float,
        trigger_valid: bool,
        risk_approved: bool
    ) -> str:
        """
        Transition to next state based on current conditions.

        Args:
            current_state: Current position state (WATCH, SETUP_FORMING, etc.)
            regime: Market regime (STRONG_BULL, WEAK_BEAR, RANGE, etc.)
            score: Signal score (0-100)
            trigger_valid: Whether additional confirmation triggers are met
            risk_approved: Whether risk checks passed

        Returns:
            Next state to transition to
        """

        # If already holding, check exit conditions with hysteresis
        if current_state in ("ENTERED", "HOLDING"):
            # Hard exit if risk rejected
            if not risk_approved:
                return "EXIT_READY"
            # Exit warning if score drops moderately
            if score < 35.0:
                return "EXIT_WARNING"
            # Stay holding if score still valid
            return "HOLDING"

        # If in exit warning, check if we should fully exit or recover
        if current_state == "EXIT_WARNING":
            # Full exit if score drops further
            if score < 25.0:
                return "EXIT_READY"
            # Recover to holding if score rebounds
            if score > 55.0:
                return "HOLDING"
            # Stay in warning zone
            return "EXIT_WARNING"

        # Entry pipeline - more restrictive than holding
        if not risk_approved:
            return "INVALIDATED"

        # High confidence: ready to enter
        if score >= 70.0 and trigger_valid:
            return "ENTRY_READY"

        # Medium confidence: setup forming
        elif score >= 55.0:
            return "SETUP_FORMING"

        # Low confidence: just watch
        else:
            return "WATCH"

    @staticmethod
    def get_state_description(state: str) -> str:
        """Get human-readable description of a state."""
        descriptions = {
            "WATCH": "Monitoring market, no setup detected",
            "SETUP_FORMING": "Setup criteria partially met, awaiting confirmation",
            "ENTRY_READY": "All conditions met, ready to enter position",
            "ENTERED": "Position entered, monitoring for exit",
            "HOLDING": "Position held, exit signal not yet generated",
            "EXIT_WARNING": "Exit signal weak, exit not yet confirmed",
            "EXIT_READY": "Exit signal confirmed, closing position",
            "INVALIDATED": "Data or risk check failed, entry aborted"
        }
        return descriptions.get(state, "UNKNOWN")

    @staticmethod
    def is_trading_state(state: str) -> bool:
        """Check if this state represents an active trade."""
        return state in ("ENTERED", "HOLDING", "EXIT_WARNING", "EXIT_READY")

    @staticmethod
    def is_entry_state(state: str) -> bool:
        """Check if this state represents readiness to enter."""
        return state in ("ENTRY_READY",)

    @staticmethod
    def is_exit_state(state: str) -> bool:
        """Check if this state represents readiness to exit."""
        return state in ("EXIT_WARNING", "EXIT_READY")
