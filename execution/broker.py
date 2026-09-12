import asyncio
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from engine.portfolio import global_portfolio


class MockBroker:
    """Mock execution broker for paper trading and live simulation."""

    def __init__(self, balance: float = None, initial_balance: float = None):
        self.portfolio = global_portfolio
        if balance is not None:
            self.balance = balance
        elif initial_balance is not None:
            self.balance = initial_balance
        else:
            self.balance = self.portfolio.available_cash
        self.orders = {}

    async def get_balance(self) -> float:
        """Return current simulated account balance / buying power."""
        if hasattr(self, "portfolio") and self.portfolio:
            return self.portfolio.available_cash
        return self.balance

    async def place_order(self, signal: dict) -> dict:
        """Execute and fill an order for the given signal."""
        await asyncio.sleep(0.05)  # Simulate network/exchange latency
        order_id = f"ord_{uuid.uuid4().hex[:8]}"
        
        # Determine fill price: use option premium if option contract, else asset price
        opt = signal.get("option_data", {})
        raw_premium = opt.get("premium")
        fill_price = signal.get("price", 0.0)
        if raw_premium and isinstance(raw_premium, str) and raw_premium != "N/A":
            try:
                fill_price = float(raw_premium.replace("$", "").strip())
            except ValueError:
                pass
        elif isinstance(raw_premium, (int, float)) and raw_premium > 0:
            fill_price = float(raw_premium)

        order = {
            "id": order_id,
            "status": "filled",
            "symbol": signal.get("symbol"),
            "side": signal.get("decision", "LONG"),
            "fill_price": fill_price,
            "quantity": signal.get("quantity", 1.0),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.orders[order_id] = order
        return order

    async def close_order(self, order_id: str, exit_price: float) -> dict:
        """Close an existing order position."""
        await asyncio.sleep(0.05)
        if order_id in self.orders:
            self.orders[order_id]["status"] = "closed"
            self.orders[order_id]["exit_price"] = exit_price
            self.orders[order_id]["closed_at"] = datetime.now(timezone.utc).isoformat()
            return self.orders[order_id]
        return {
            "id": order_id,
            "status": "closed",
            "exit_price": exit_price,
            "closed_at": datetime.now(timezone.utc).isoformat()
        }