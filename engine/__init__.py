"""
Metobot Quantitative Engine - Single source of truth for all trading logic.

This module exports the complete quant pipeline:
- DataGuard: OHLCV validation
- FeatureEngine: Technical indicator calculation
- RegimeEngine & StrategyEngine: Market regime and signal generation
- RiskEngine: Risk assessment and position sizing
- PositionStateMachine: State management with hysteresis
- QuantEngine: Orchestrator tying everything together

CRITICAL: All trading logic imports should come from this module.
Never duplicate quant logic in api.py, web/api.py, or elsewhere.
"""

from engine.data_guard import DataGuard
from engine.features import FeatureEngine
from engine.regime import RegimeEngine, StrategyEngine
from engine.risk_engine import RiskEngine
from engine.position_state import PositionStateMachine
from engine.quant_core import QuantEngine
from engine.market_hours import (
    MarketSchedule,
    is_market_open,
    is_market_closed,
    MARKET_TIMEZONE,
    MARKET_OPEN_TIME,
    MARKET_CLOSE_TIME,
    PRE_MARKET_OPEN_TIME,
    PRE_MARKET_CLOSE_TIME,
    NORMAL_MARKET_OPEN_TIME,
    NORMAL_MARKET_CLOSE_TIME,
    AFTER_MARKET_OPEN_TIME,
    AFTER_MARKET_CLOSE_TIME,
    MARKET_CLOSED_OPEN_TIME,
    MARKET_CLOSED_CLOSE_TIME,
    SESSION_TZ
)

__all__ = [
    "DataGuard",
    "FeatureEngine",
    "RegimeEngine",
    "StrategyEngine",
    "RiskEngine",
    "PositionStateMachine",
    "QuantEngine",
    "MarketSchedule",
    "is_market_open",
    "is_market_closed",
    "MARKET_TIMEZONE",
    "MARKET_OPEN_TIME",
    "MARKET_CLOSE_TIME",
    "PRE_MARKET_OPEN_TIME",
    "PRE_MARKET_CLOSE_TIME",
    "NORMAL_MARKET_OPEN_TIME",
    "NORMAL_MARKET_CLOSE_TIME",
    "AFTER_MARKET_OPEN_TIME",
    "AFTER_MARKET_CLOSE_TIME",
    "MARKET_CLOSED_OPEN_TIME",
    "MARKET_CLOSED_CLOSE_TIME",
    "SESSION_TZ",
]

