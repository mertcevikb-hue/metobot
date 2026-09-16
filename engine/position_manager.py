"""
Position Manager - Dedicated Position Lifecycle and State Machine Orchestrator.
Manages entries, real-time candle evaluations, dynamic TP/SL execution,
partial quantity scaling, trailing profit protection, and cooldown initiation.
"""
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from loguru import logger
from engine.dynamic_exit import DynamicExitEngine
from engine.trade_engine import TradeEngine, global_trade_engine
from engine.portfolio import PortfolioManager, global_portfolio


class PositionManager:
    """Manages open position lifecycles, exits, and state transitions."""

    def __init__(
        self,
        trade_engine: Optional[TradeEngine] = None,
        portfolio: Optional[PortfolioManager] = None
    ):
        self.trade_engine = trade_engine or global_trade_engine
        self.portfolio = portfolio or global_portfolio
        # In-memory position storage: underlying_symbol -> position_dict
        self._positions: Dict[str, Dict[str, Any]] = {}

    def get_position(self, underlying: str) -> Optional[Dict[str, Any]]:
        clean = underlying.strip().upper()
        return self._positions.get(clean)

    def get_all_positions(self) -> List[Dict[str, Any]]:
        return list(self._positions.values())

    def open_position(self, trade_data: Dict[str, Any]) -> Dict[str, Any]:
        """Registers a newly opened paper position."""
        underlying = (trade_data.get("underlying") or trade_data.get("symbol", "UNKNOWN")).strip().upper()
        trade_id = str(trade_data.get("trade_id"))
        qty = float(trade_data.get("quantity", 1.0))
        entry_p = float(trade_data.get("entry_price", 0.0))

        # Ensure option positions consider premium prices, not stock price
        is_opt = trade_data.get("option_type") in ["CALL", "PUT"] or trade_data.get("instrument") == "OPTION"
        if is_opt:
            prem = trade_data.get("premium") or trade_data.get("entry_premium")
            if prem:
                try:
                    entry_p = float(str(prem).replace("$", "").strip())
                except (ValueError, TypeError):
                    pass
            elif entry_p > 50.0:
                fill = trade_data.get("fill_price")
                if fill and float(fill) < 50.0:
                    entry_p = float(fill)

        pos = {
            "trade_id": trade_id,
            "symbol": trade_data.get("symbol", underlying),
            "underlying": underlying,
            "contract_symbol": trade_data.get("contract_symbol"),
            "option_type": trade_data.get("option_type", "STOCK"),
            "strike_price": trade_data.get("strike_price"),
            "expiration": trade_data.get("expiration"),
            "side": trade_data.get("side", "LONG"),
            "entry_price": entry_p,
            "current_price": entry_p,
            "initial_quantity": qty,
            "quantity": qty,
            "stop_loss": trade_data.get("stop_loss"),
            "trailing_stop": trade_data.get("stop_loss"),
            "tp1": trade_data.get("tp1") or trade_data.get("take_profit"),
            "tp2": trade_data.get("tp2"),
            "score": trade_data.get("score"),
            "strategy": trade_data.get("strategy"),
            "entry_thesis": trade_data.get("entry_thesis", "Technical setup confirmed"),
            "state": "OPEN",
            "profit_protection_active": False,
            "tp1_hit": False,
            "tp2_hit": False,
            "mfe": 0.0,
            "mae": 0.0,
            "bars_held": 0,
            "is_0dte": bool(trade_data.get("is_0dte", False)),
            "created_at": trade_data.get("created_at") or datetime.now(timezone.utc).isoformat()
        }

        self._positions[underlying] = pos
        self.portfolio.register_position_opened(pos)
        logger.info(f"📂 PositionManager: Position OPENED for {underlying} ({pos['side']} {qty:.0f} units @ ${entry_p:.2f})")
        return pos

    def evaluate_candle(
        self,
        underlying: str,
        candle: Dict[str, Any],
        features: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates an active position against the current live candle.
        Calls DynamicExitEngine to determine whether TP1, TP2, trailing stop,
        profit protection, rotation exit, or thesis invalidation has triggered.
        """
        clean = underlying.strip().upper()
        pos = self._positions.get(clean)
        if not pos:
            return {"has_position": False, "should_exit": False}

        current_price = float(candle.get("close", pos.get("entry_price", 0.0)))
        candle_high = float(candle.get("high", current_price))
        candle_low = float(candle.get("low", current_price))
        pos["current_price"] = current_price

        # Evaluate exit logic
        exit_eval = DynamicExitEngine.evaluate_exit(
            position=pos,
            current_price=current_price,
            candle_high=candle_high,
            candle_low=candle_low,
            current_features=features,
            is_0dte=pos.get("is_0dte", False)
        )

        pos.update(exit_eval.get("updated_position", {}))

        if exit_eval["should_exit"]:
            if exit_eval["exit_type"] == "FULL":
                logger.info(f"🛑 PositionManager: FULL EXIT triggered for {clean} -> {exit_eval['exit_reason']}")
                self.close_position(clean, exit_eval["exit_price"], exit_eval["exit_reason"], exit_eval["result_code"])
            elif exit_eval["exit_type"] == "PARTIAL":
                logger.info(f"✂️ PositionManager: PARTIAL EXIT triggered for {clean} -> {exit_eval['exit_reason']}")

        return {
            "has_position": True,
            "position": pos,
            **exit_eval
        }

    def close_position(
        self,
        underlying: str,
        exit_price: float,
        exit_reason: str = "Exit signal confirmed",
        result_code: str = "CLOSED"
    ) -> Optional[Dict[str, Any]]:
        """Removes position from active monitoring and records cooldown."""
        clean = underlying.strip().upper()
        pos = self._positions.pop(clean, None)
        if not pos:
            return None

        pos["state"] = "CLOSED"
        pos["exit_price"] = exit_price
        pos["exit_reason"] = exit_reason
        pos["result"] = result_code
        pos["closed_at"] = datetime.now(timezone.utc).isoformat()

        # Calculate P&L
        trade_data = pos.get("trade_data", {})
        entry_p = float(trade_data.get("entry_price", pos.get("entry_price", 0.0)))
        qty = float(trade_data.get("quantity", pos.get("quantity", 1.0)))

        # Ensure option positions consider premium prices, not stock price
        is_opt = trade_data.get("option_type") in ["CALL", "PUT"] or trade_data.get("instrument") == "OPTION"
        if is_opt:
            prem = trade_data.get("premium") or trade_data.get("entry_premium")
            if prem:
                try:
                    entry_p = float(str(prem).replace("$", "").strip())
                except (ValueError, TypeError):
                    pass
            elif entry_p > 50.0:
                fill = trade_data.get("fill_price")
                if fill and float(fill) < 50.0:
                    entry_p = float(fill)
        multiplier = 100.0 if pos.get("option_type") in ["CALL", "PUT"] else 1.0
        side = pos.get("side", "LONG")

        if side in ["LONG", "BUY", "CALL"]:
            pnl = (exit_price - entry_p) * qty * multiplier
            pnl_pct = ((exit_price - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
        else:
            pnl = (entry_p - exit_price) * qty * multiplier
            pnl_pct = ((entry_p - exit_price) / entry_p) * 100.0 if entry_p > 0 else 0.0

        pos["pnl"] = round(pnl, 2)
        pos["pnl_pct"] = round(pnl_pct, 2)

        # Notify portfolio & cooldown registry
        self.portfolio.register_position_closed(pos["trade_id"], exit_price, pnl)
        self.trade_engine.record_exit(clean)

        logger.info(f"✅ PositionManager: Position CLOSED for {clean} | PnL: ${pnl:.2f} ({pnl_pct:+.2f}%) [{result_code}]")
        return pos

    def hydrate_from_db(self, open_trades: List[Dict[str, Any]]) -> None:
        """Hydrates active open positions from database on startup."""
        self._positions.clear()
        for t in open_trades:
            if t.get("status") == "OPEN":
                underlying = (t.get("underlying") or t.get("symbol", "")).strip().upper()
                if underlying:
                    self.open_position(t)


# Global singleton PositionManager
global_position_manager = PositionManager()
