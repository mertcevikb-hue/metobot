"""
Walk-Forward Validation - Prevent overfitting with out-of-sample testing.

Methodology:
1. Split data into windows (e.g., 6-month train, 1-month test)
2. Train model on training window
3. Test on out-of-sample test window
4. Roll forward and repeat
5. Aggregate results

This prevents:
- Overfitting to historical data
- Curve-fitting to specific market conditions
- False confidence in backtest results
- Black swan events

Standard practice for legitimate quant trading.
"""

from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import statistics
from loguru import logger


@dataclass
class WalkForwardWindow:
    """A single walk-forward test window."""
    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    train_profit: float = 0.0
    test_profit: float = 0.0
    fitness_degradation: float = 0.0  # How much worse test performs vs train

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "window_id": self.window_id,
            "train_period": f"{self.train_start.date()} to {self.train_end.date()}",
            "test_period": f"{self.test_start.date()} to {self.test_end.date()}",
            "train_sharpe": round(self.train_sharpe, 2),
            "test_sharpe": round(self.test_sharpe, 2),
            "train_profit": round(self.train_profit, 2),
            "test_profit": round(self.test_profit, 2),
            "fitness_degradation": round(self.fitness_degradation * 100, 2)
        }


class WalkForwardValidator:
    """
    Validate trading models with walk-forward analysis.

    Example:
        validator = WalkForwardValidator(
            train_window_days=180,  # 6 months
            test_window_days=30,    # 1 month
            step_days=30            # Roll forward 1 month each time
        )

        results = validator.validate(
            all_candles,
            strategy=my_strategy_function
        )
    """

    def __init__(
        self,
        train_window_days: int = 180,
        test_window_days: int = 30,
        step_days: int = 30
    ):
        """
        Initialize walk-forward validator.

        Args:
            train_window_days: Days of data to use for training (e.g., 180 = 6 months)
            test_window_days: Days of data to use for out-of-sample testing (e.g., 30 = 1 month)
            step_days: How many days to roll forward for next window (e.g., 30 = monthly)
        """
        self.train_window = timedelta(days=train_window_days)
        self.test_window = timedelta(days=test_window_days)
        self.step = timedelta(days=step_days)

    def validate(
        self,
        candles: List[Dict[str, Any]],
        strategy_func,
        backtest_engine_class
    ) -> Dict[str, Any]:
        """
        Run walk-forward validation.

        Args:
            candles: Complete list of OHLCV candles with timestamps
            strategy_func: Function that generates signals (receives candles, returns signal)
            backtest_engine_class: BacktestEngine class to use for simulation

        Returns:
            Validation results with window-by-window performance
        """
        if not candles or len(candles) < 100:
            logger.error("Insufficient data for walk-forward validation")
            return {"error": "Insufficient data"}

        # Extract timestamps
        start_time = candles[0]["timestamp"]
        end_time = candles[-1]["timestamp"]

        logger.info(f"Walk-forward validation from {start_time} to {end_time}")

        windows = []
        window_id = 0
        current_time = start_time + self.train_window

        # Generate walk-forward windows
        while current_time + self.test_window <= end_time:
            window = WalkForwardWindow(
                window_id=window_id,
                train_start=current_time - self.train_window,
                train_end=current_time,
                test_start=current_time,
                test_end=current_time + self.test_window
            )

            # Run backtest on this window
            self._run_window_backtest(window, candles, strategy_func, backtest_engine_class)

            windows.append(window)
            logger.info(f"✓ Window {window_id}: "
                       f"Train Sharpe {window.train_sharpe:.2f} → "
                       f"Test Sharpe {window.test_sharpe:.2f} "
                       f"(Degradation: {window.fitness_degradation*100:.1f}%)")

            current_time += self.step
            window_id += 1

        # Aggregate results
        return self._aggregate_results(windows)

    def _run_window_backtest(
        self,
        window: WalkForwardWindow,
        all_candles: List[Dict[str, Any]],
        strategy_func,
        backtest_engine_class
    ):
        """Run backtest for a single walk-forward window."""
        # Filter candles for this window
        train_candles = [
            c for c in all_candles
            if window.train_start <= c["timestamp"] < window.train_end
        ]

        test_candles = [
            c for c in all_candles
            if window.test_start <= c["timestamp"] < window.test_end
        ]

        if not train_candles or not test_candles:
            logger.warning(f"Insufficient data for window {window.window_id}")
            return

        # Run backtest on training data
        engine_train = backtest_engine_class()
        for candle in train_candles:
            signal = strategy_func(candle)
            engine_train.process_candle(
                candle["timestamp"],
                "BTC/USDT",  # TODO: Make symbol configurable
                candle,
                signal
            )

        train_results = engine_train.get_results()
        window.train_sharpe = train_results.get("stats", {}).get("sharpe_ratio", 0.0)
        window.train_profit = train_results.get("summary", {}).get("total_profit", 0.0)

        # Run backtest on testing data (out-of-sample)
        engine_test = backtest_engine_class()
        for candle in test_candles:
            signal = strategy_func(candle)
            engine_test.process_candle(
                candle["timestamp"],
                "BTC/USDT",
                candle,
                signal
            )

        test_results = engine_test.get_results()
        window.test_sharpe = test_results.get("stats", {}).get("sharpe_ratio", 0.0)
        window.test_profit = test_results.get("summary", {}).get("total_profit", 0.0)

        # Calculate fitness degradation
        # (How much worse does the model perform on unseen data?)
        if window.train_sharpe > 0:
            window.fitness_degradation = 1.0 - (window.test_sharpe / window.train_sharpe)
        else:
            window.fitness_degradation = 0.0

    def _aggregate_results(self, windows: List[WalkForwardWindow]) -> Dict[str, Any]:
        """Aggregate results across all windows."""
        if not windows:
            return {"error": "No valid windows"}

        train_sharpes = [w.train_sharpe for w in windows]
        test_sharpes = [w.test_sharpe for w in windows]
        degradations = [w.fitness_degradation for w in windows]
        test_profits = [w.test_profit for w in windows]

        return {
            "windows": len(windows),
            "overall_performance": {
                "avg_train_sharpe": round(statistics.mean(train_sharpes), 2),
                "avg_test_sharpe": round(statistics.mean(test_sharpes), 2),
                "avg_fitness_degradation": round(statistics.mean(degradations) * 100, 2),
                "total_test_profit": round(sum(test_profits), 2),
                "win_rate": round(sum(1 for p in test_profits if p > 0) / len(test_profits) * 100, 2) if test_profits else 0,
            },
            "overfitting_risk": self._assess_overfitting_risk(degradations),
            "windows": [w.to_dict() for w in windows]
        }

    @staticmethod
    def _assess_overfitting_risk(degradations: List[float]) -> str:
        """Assess if model is likely overfitted."""
        if not degradations:
            return "UNKNOWN"

        avg_degradation = statistics.mean(degradations)

        if avg_degradation > 0.3:
            return "HIGH - Model performance degrades >30% on new data"
        elif avg_degradation > 0.15:
            return "MODERATE - Performance degradation 15-30%"
        elif avg_degradation > 0.0:
            return "LOW - Minimal degradation <15%"
        else:
            return "EXCELLENT - Model performs better on test data (rare)"


# Example usage documentation
EXAMPLE_USAGE = """
from training.walk_forward_validator import WalkForwardValidator
from training.backtest_engine import BacktestEngine
from data.live_feed import LiveDataFeed

# Initialize validator
validator = WalkForwardValidator(
    train_window_days=180,   # 6-month training window
    test_window_days=30,     # 1-month testing window
    step_days=30             # Roll forward monthly
)

# Define strategy function
def my_strategy(candle):
    # Your signal generation logic
    return {
        "decision": "LONG" or "SHORT" or "NO_TRADE",
        "stop_loss": ...,
        "take_profit": ...
    }

# Load historical data
feed = LiveDataFeed([" BTC/USDT"])
candles = await feed.get_candles("BTC/USDT", limit=1000)

# Run walk-forward validation
results = validator.validate(candles, my_strategy, BacktestEngine)

# Check results
print(f"Overfitting Risk: {results['overfitting_risk']}")
print(f"Avg Test Sharpe: {results['overall_performance']['avg_test_sharpe']}")
"""
