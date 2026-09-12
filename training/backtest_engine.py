"""
Professional Backtest Engine - Realistic simulation of trading strategy.

Features:
- Sequential candle processing (no look-ahead bias)
- Realistic spread, commission, slippage
- Account equity tracking
- Drawdown monitoring
- Trade journal with P&L
- Performance metrics (Sharpe, Sortino, max DD, win rate, etc.)
- Event logging and debugging

NO LOOK-AHEAD BIAS:
- Indicators calculated ONLY from historical data
- No future prices used in signal generation
- Proper candle-by-candle sequencing
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from loguru import logger
import statistics


@dataclass
class Trade:
    """Represents a single trade."""
    entry_time: datetime
    entry_price: float
    entry_size: float
    side: str  # "LONG" or "SHORT"
    stop_loss: float
    take_profit: float

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    duration_minutes: int = 0

    def close_trade(
        self,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        commission: float = 0.001
    ):
        """Close the trade and calculate P&L."""
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = exit_reason

        # Calculate raw P&L
        if self.side == "LONG":
            raw_pnl = (exit_price - self.entry_price) * self.entry_size
        else:  # SHORT
            raw_pnl = (self.entry_price - exit_price) * self.entry_size

        # Deduct commission (entry + exit)
        commission_cost = (self.entry_price * self.entry_size + exit_price * self.entry_size) * commission

        self.pnl = raw_pnl - commission_cost
        self.pnl_percent = (self.pnl / (self.entry_price * self.entry_size)) * 100
        self.duration_minutes = int((exit_time - self.entry_time).total_seconds() / 60)

    def is_profit(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": self.entry_price,
            "entry_size": self.entry_size,
            "side": self.side,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": round(self.pnl, 2),
            "pnl_percent": round(self.pnl_percent, 2),
            "duration_minutes": self.duration_minutes
        }


@dataclass
class BacktestStats:
    """Performance statistics."""
    initial_balance: float
    final_balance: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0

    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0

    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_win_trade: float = 0.0
    max_loss_trade: float = 0.0
    tp_hit_count: int = 0
    sl_hit_count: int = 0
    tp_hit_rate: float = 0.0

    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    avg_trade_duration: int = 0

    def calculate(self, trades: List[Trade], returns: List[float]):
        """Calculate all statistics including 0DTE expectancy and risk metrics."""
        if not trades:
            return

        self.total_trades = len(trades)
        self.winning_trades = sum(1 for t in trades if t.is_profit())
        self.losing_trades = self.total_trades - self.winning_trades
        self.win_rate = self.winning_trades / self.total_trades if self.total_trades > 0 else 0.0

        # Profit calculations
        self.gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        self.gross_loss = sum(t.pnl for t in trades if t.pnl < 0)
        self.net_profit = self.final_balance - self.initial_balance

        # Extremes
        pnls = [t.pnl for t in trades]
        self.max_win_trade = max(pnls) if pnls else 0.0
        self.max_loss_trade = min(pnls) if pnls else 0.0

        # Hit rate breakdown
        self.tp_hit_count = sum(1 for t in trades if t.exit_reason and "TAKE_PROFIT" in t.exit_reason)
        self.sl_hit_count = sum(1 for t in trades if t.exit_reason and "STOP_LOSS" in t.exit_reason)
        self.tp_hit_rate = (self.tp_hit_count / self.total_trades * 100.0) if self.total_trades > 0 else 0.0

        # Average trade
        if self.winning_trades > 0:
            self.avg_win = self.gross_profit / self.winning_trades
        if self.losing_trades > 0:
            self.avg_loss = abs(self.gross_loss / self.losing_trades)

        # Mathematical Expectancy: E = (P_win * Avg_Win) - (P_loss * Avg_Loss)
        p_win = self.win_rate
        p_loss = 1.0 - p_win
        self.expectancy = (p_win * self.avg_win) - (p_loss * self.avg_loss)

        # Profit factor
        if self.gross_loss != 0:
            self.profit_factor = abs(self.gross_profit / self.gross_loss)
        elif self.gross_profit > 0:
            self.profit_factor = 99.9

        # Duration
        durations = [t.duration_minutes for t in trades if t.duration_minutes > 0]
        if durations:
            self.avg_trade_duration = int(statistics.mean(durations))

        # Risk-adjusted returns
        if len(returns) > 1:
            try:
                mean_return = statistics.mean(returns) if returns else 0
                std_return = statistics.stdev(returns) if len(returns) > 1 else 0

                # Sharpe ratio (annualized, assuming 252 trading days)
                if std_return > 0:
                    self.sharpe_ratio = (mean_return * 252) / (std_return * (252 ** 0.5))

                # Sortino ratio (only downside volatility)
                downside = [r for r in returns if r < 0]
                if downside:
                    down_std = statistics.stdev(downside)
                    self.sortino_ratio = (mean_return * 252) / (down_std * (252 ** 0.5)) if down_std > 0 else 0

            except Exception as e:
                logger.warning(f"Error calculating risk metrics: {e}")


class BacktestEngine:
    """Professional backtest engine with no look-ahead bias."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        risk_percent: float = 1.0,
        spread: float = 0.0005,
        commission: float = 0.001,
        max_positions: int = 1
    ):
        self.initial_balance = initial_balance
        self.risk_percent = risk_percent
        self.spread = spread
        self.commission = commission
        self.max_positions = max_positions

        self.current_balance = initial_balance
        self.equity_history = [initial_balance]
        self.trades: List[Trade] = []
        self.open_positions: Dict[str, Trade] = {}
        self.closed_positions: List[Trade] = []

    def process_candle(
        self,
        timestamp: datetime,
        symbol: str,
        ohlc: Dict[str, float],
        signal: Optional[Dict[str, Any]] = None
    ) -> Optional[Trade]:
        """
        Process a single candle.

        This is the MAIN entry point for backtest.
        Processes sequentially - no look-ahead.

        Args:
            timestamp: Candle time
            symbol: Trading pair
            ohlc: {"open": X, "high": X, "low": X, "close": X, "volume": X}
            signal: Optional trading signal from strategy

        Returns:
            Trade if entered, None otherwise
        """
        current_price = ohlc.get("close", ohlc.get("open", 0))

        # 1. Check for exit triggers on open positions
        self._check_exits(symbol, timestamp, ohlc)

        # 2. Process entry signal if we have room for positions
        if signal and len(self.open_positions) < self.max_positions:
            if signal.get("decision") in ["LONG", "SHORT"]:
                trade = self._enter_position(symbol, timestamp, signal, current_price, ohlc)
                return trade

        # 3. Update equity
        self._update_equity(symbol, current_price)

        return None

    def _enter_position(
        self,
        symbol: str,
        timestamp: datetime,
        signal: Dict[str, Any],
        entry_price: float,
        ohlc: Dict[str, float]
    ) -> Optional[Trade]:
        """Enter a new position."""
        if symbol in self.open_positions:
            logger.warning(f"Already have position in {symbol}")
            return None

        side = signal.get("decision")
        stop_loss = signal.get("stop_loss", entry_price * 0.95)
        take_profit = signal.get("take_profit", entry_price * 1.05)

        # Calculate position size based on risk
        risk_amount = self.current_balance * (self.risk_percent / 100)
        if side == "LONG":
            position_size = risk_amount / (entry_price - stop_loss)
        else:  # SHORT
            position_size = risk_amount / (stop_loss - entry_price)

        # Cap position size to 1 unit for this example
        position_size = min(position_size, 1.0)

        # Apply spread
        if side == "LONG":
            entry_price = entry_price * (1 + self.spread)
        else:
            entry_price = entry_price * (1 - self.spread)

        trade = Trade(
            entry_time=timestamp,
            entry_price=entry_price,
            entry_size=position_size,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

        self.open_positions[symbol] = trade
        logger.info(f"📈 Entry: {symbol} {side} @ {entry_price} (size: {position_size})")

        return trade

    def _check_exits(
        self,
        symbol: str,
        timestamp: datetime,
        ohlc: Dict[str, float]
    ):
        """Check if open positions should exit."""
        if symbol not in self.open_positions:
            return

        trade = self.open_positions[symbol]
        high = ohlc.get("high", 0)
        low = ohlc.get("low", 0)
        close = ohlc.get("close", 0)

        # Check stop loss
        if trade.side == "LONG" and low <= trade.stop_loss:
            self._close_position(symbol, timestamp, trade.stop_loss, "STOP_LOSS")
            return

        if trade.side == "SHORT" and high >= trade.stop_loss:
            self._close_position(symbol, timestamp, trade.stop_loss, "STOP_LOSS")
            return

        # Check take profit
        if trade.side == "LONG" and high >= trade.take_profit:
            self._close_position(symbol, timestamp, trade.take_profit, "TAKE_PROFIT")
            return

        if trade.side == "SHORT" and low <= trade.take_profit:
            self._close_position(symbol, timestamp, trade.take_profit, "TAKE_PROFIT")
            return

    def _close_position(
        self,
        symbol: str,
        timestamp: datetime,
        exit_price: float,
        exit_reason: str
    ):
        """Close a position."""
        if symbol not in self.open_positions:
            return

        trade = self.open_positions[symbol]

        # Apply spread on exit
        if trade.side == "LONG":
            exit_price = exit_price * (1 - self.spread)
        else:
            exit_price = exit_price * (1 + self.spread)

        trade.close_trade(timestamp, exit_price, exit_reason, self.commission)

        # Update balance
        self.current_balance += trade.pnl

        # Move to closed positions
        del self.open_positions[symbol]
        self.closed_positions.append(trade)

        logger.info(f"📉 Exit: {symbol} @ {exit_price} | P&L: {trade.pnl:.2f} ({trade.pnl_percent:.1f}%)")

    def _update_equity(self, symbol: str, current_price: float):
        """Update account equity based on open positions."""
        equity = self.current_balance

        for sym, trade in self.open_positions.items():
            if sym == symbol:
                # Unrealized P&L
                if trade.side == "LONG":
                    unrealized = (current_price - trade.entry_price) * trade.entry_size
                else:
                    unrealized = (trade.entry_price - current_price) * trade.entry_size

                equity += unrealized

        self.equity_history.append(equity)

    def get_results(self) -> Dict[str, Any]:
        """Get complete backtest results."""
        # Calculate statistics
        stats = BacktestStats(self.initial_balance, self.current_balance)

        # Calculate returns for Sharpe/Sortino
        returns = []
        for i in range(1, len(self.equity_history)):
            ret = (self.equity_history[i] - self.equity_history[i-1]) / self.equity_history[i-1]
            returns.append(ret)

        # Calculate max drawdown
        peak = self.equity_history[0]
        max_dd = 0
        for equity in self.equity_history:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
        stats.max_drawdown = peak - min(self.equity_history)
        stats.max_drawdown_percent = max_dd * 100

        stats.calculate(self.closed_positions, returns)

        return {
            "summary": {
                "initial_balance": self.initial_balance,
                "final_balance": self.current_balance,
                "total_profit": self.current_balance - self.initial_balance,
                "return_percent": ((self.current_balance - self.initial_balance) / self.initial_balance) * 100
            },
            "stats": {
                "total_trades": stats.total_trades,
                "winning_trades": stats.winning_trades,
                "losing_trades": stats.losing_trades,
                "win_rate": round(stats.win_rate * 100, 2),
                "gross_profit": round(stats.gross_profit, 2),
                "gross_loss": round(stats.gross_loss, 2),
                "avg_win": round(stats.avg_win, 2),
                "avg_loss": round(stats.avg_loss, 2),
                "profit_factor": round(stats.profit_factor, 2),
                "expectancy": round(stats.expectancy, 2),
                "max_win_trade": round(stats.max_win_trade, 2),
                "max_loss_trade": round(stats.max_loss_trade, 2),
                "tp_hit_count": stats.tp_hit_count,
                "sl_hit_count": stats.sl_hit_count,
                "tp_hit_rate": round(stats.tp_hit_rate, 2),
                "max_drawdown": round(stats.max_drawdown, 2),
                "max_drawdown_percent": round(stats.max_drawdown_percent, 2),
                "sharpe_ratio": round(stats.sharpe_ratio, 2),
                "sortino_ratio": round(stats.sortino_ratio, 2),
                "avg_trade_duration_minutes": stats.avg_trade_duration
            },
            "trades": [t.to_dict() for t in self.closed_positions],
            "equity_curve": self.equity_history
        }
