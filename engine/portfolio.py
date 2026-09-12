"""
Portfolio & Capital Manager - Artificial Paper Trading Account.
Manages balance, buying power, exposure per underlying, position sizing,
and daily loss limits. Prevents portfolio over-concentration.
"""
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, date
from config.trading_config import global_config


class PortfolioManager:
    """Manages paper-trading portfolio state and position-sizing gates."""

    def __init__(
        self,
        starting_balance: float = None,
        initial_balance: float = None,
        risk_per_trade_pct: float = None,
        max_exposure_pct: float = None,
        daily_loss_limit_pct: float = None
    ):
        self.starting_balance = starting_balance or initial_balance or global_config.paper_starting_balance
        self.initial_balance = self.starting_balance
        self.available_cash = self.starting_balance
        self.realized_pnl = 0.0
        self.daily_pnl = 0.0
        self.daily_date = datetime.now(timezone.utc).date()
        self.risk_per_trade_pct = risk_per_trade_pct or global_config.risk_per_trade_pct
        self.max_exposure_pct = max_exposure_pct or global_config.max_portfolio_exposure_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct or global_config.daily_loss_limit_pct

        # Active in-memory cache of open positions: trade_id -> trade_dict
        self._open_positions: Dict[str, Dict[str, Any]] = {}

    def _check_daily_rollover(self):
        """Resets daily P&L when crossing UTC day boundary."""
        today = datetime.now(timezone.utc).date()
        if today != self.daily_date:
            self.daily_date = today
            self.daily_pnl = 0.0

    def get_portfolio_summary(self) -> Dict[str, Any]:
        self._check_daily_rollover()
        total_open_notional = sum(
            float(pos.get("notional", 0.0) or (float(pos.get("entry_price", 0.0)) * float(pos.get("quantity", 1.0)) * (100.0 if pos.get("option_type") in ["CALL", "PUT"] else 1.0)))
            for pos in self._open_positions.values()
        )
        total_unrealized_pnl = sum(
            float(pos.get("unrealized_pnl", 0.0))
            for pos in self._open_positions.values()
        )
        total_equity = round(self.available_cash + total_open_notional + total_unrealized_pnl, 2)
        open_exposure_pct = round((total_open_notional / max(self.starting_balance, 1.0)) * 100.0, 2)

        daily_loss_ratio = self.daily_loss_limit_pct if self.daily_loss_limit_pct <= 1.0 else self.daily_loss_limit_pct / 100.0
        max_exposure_ratio = self.max_exposure_pct if self.max_exposure_pct <= 1.0 else self.max_exposure_pct / 100.0
        risk_trade_ratio = self.risk_per_trade_pct if self.risk_per_trade_pct <= 1.0 else self.risk_per_trade_pct / 100.0
        daily_loss_limit_amt = round(self.starting_balance * daily_loss_ratio, 2)
        max_exposure_amt = round(self.starting_balance * max_exposure_ratio, 2)
        risk_trade_amt = round(self.starting_balance * risk_trade_ratio, 2)
        cooldown_mins = getattr(global_config, 'cooldown_minutes', 15)

        return {
            "starting_balance": round(self.starting_balance, 2),
            "available_cash": round(self.available_cash, 2),
            "total_equity": total_equity,
            "open_exposure": round(total_open_notional, 2),
            "open_exposure_pct": open_exposure_pct,
            "exposure_pct": open_exposure_pct,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "daily_pnl": round(self.daily_pnl + total_unrealized_pnl, 2),
            "open_positions_count": len(self._open_positions),
            "open_position_count": len(self._open_positions),
            "is_daily_loss_breached": self.is_daily_loss_breached(),
            "daily_loss_limit_pct": round(daily_loss_ratio * 100.0, 2),
            "daily_loss_limit_amount": daily_loss_limit_amt,
            "max_capital_allocation_pct": round(max_exposure_ratio * 100.0, 2),
            "max_exposure_pct": round(max_exposure_ratio * 100.0, 2),
            "max_capital_allocation_amount": max_exposure_amt,
            "max_exposure_amount": max_exposure_amt,
            "risk_per_trade_pct": round(risk_trade_ratio * 100.0, 2),
            "risk_per_trade_amount": risk_trade_amt,
            "cooldown_minutes": cooldown_mins
        }

    def is_daily_loss_breached(self) -> bool:
        self._check_daily_rollover()
        ratio = self.daily_loss_limit_pct if self.daily_loss_limit_pct <= 1.0 else self.daily_loss_limit_pct / 100.0
        max_loss = self.starting_balance * ratio
        current_loss = -(self.daily_pnl)
        return current_loss >= max_loss

    def calculate_position_size(
        self,
        symbol: str,
        instrument: str,
        entry_price: float,
        stop_loss: float,
        confidence: float = 75.0,
        is_0dte: bool = False
    ) -> Tuple[float, float, str]:
        """
        Calculates safe position size (contracts or shares) and total notional risk.
        Returns: (quantity, notional_value, explanation)
        """
        if entry_price <= 0:
            return 0.0, 0.0, "Invalid non-positive entry price."

        ratio = self.risk_per_trade_pct if self.risk_per_trade_pct <= 1.0 else self.risk_per_trade_pct / 100.0
        risk_budget = self.starting_balance * ratio

        # Scale risk slightly with high confidence: 75 -> 1.0x, 90+ -> 1.3x
        conf_multiplier = max(0.8, min(1.3, (confidence - 70.0) / 18.0 + 0.8))
        adjusted_risk = risk_budget * conf_multiplier

        # 0DTE risk dampener: reduce max dollar risk by 30% due to explosive gamma
        if is_0dte:
            adjusted_risk *= 0.70

        if instrument == "OPTION":
            # Options: 1 contract = 100 shares. Risk per contract = premium at risk
            stop_dist = abs(entry_price - stop_loss) if stop_loss and stop_loss > 0 else (entry_price * 0.50)
            dollar_risk_per_contract = max(stop_dist * 100.0, 50.0)
            
            raw_qty = int(adjusted_risk / dollar_risk_per_contract)
            qty = max(1, min(raw_qty, 10))  # standard 1 to 10 contracts
            notional = round(qty * entry_price * 100.0, 2)
            expl = f"Option sizing: {qty} contract(s) @ ${entry_price:.2f} (Risk: ${qty * dollar_risk_per_contract:.2f})"
            return float(qty), notional, expl
        else:
            # Equity / Stock: shares sizing
            stop_dist = abs(entry_price - stop_loss) if stop_loss and stop_loss > 0 else (entry_price * 0.03)
            shares = int(adjusted_risk / max(stop_dist, 0.01))
            shares = max(1, min(shares, 500))
            notional = round(shares * entry_price, 2)
            expl = f"Stock sizing: {shares} share(s) @ ${entry_price:.2f} (Risk: ${shares * stop_dist:.2f})"
            return float(shares), notional, expl

    def check_exposure_limit(
        self,
        underlying: str,
        direction: str,
        new_notional: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Enforces:
        1. Maximum CALL / PUT positions per underlying (default 1)
        2. Total portfolio exposure cap (default 30%)
        3. Sufficient available cash
        """
        clean_underlying = underlying.strip().upper()

        # Check total cash availability
        if new_notional > self.available_cash:
            return False, f"INSUFFICIENT_CAPITAL: Required ${new_notional:.2f} exceeds available cash ${self.available_cash:.2f}."

        # Check total portfolio exposure cap
        total_open = sum(
            float(pos.get("notional", 0.0)) for pos in self._open_positions.values()
        )
        max_exp_ratio = self.max_exposure_pct if self.max_exposure_pct <= 1.0 else self.max_exposure_pct / 100.0
        if (total_open + new_notional) > (self.starting_balance * max_exp_ratio):
            return False, f"MAX_EXPOSURE: New exposure would breach portfolio cap of {max_exp_ratio*100:.0f}% (${self.starting_balance * max_exp_ratio:.2f})."

        # Check underlying exposure count
        call_count = 0
        put_count = 0
        for pos in self._open_positions.values():
            pos_underlying = (pos.get("underlying") or pos.get("symbol", "")).strip().upper()
            if pos_underlying == clean_underlying:
                pos_type = pos.get("option_type", "CALL" if pos.get("side") == "LONG" else "PUT")
                if pos_type in ["CALL", "LONG"]:
                    call_count += 1
                elif pos_type in ["PUT", "SHORT"]:
                    put_count += 1

        is_call_request = direction in ["CALL", "LONG", "BUY"]
        is_put_request = direction in ["PUT", "SHORT", "SELL"]

        if is_call_request and call_count >= global_config.max_call_positions_per_underlying:
            return False, f"EXISTING_POSITION: Underlying '{clean_underlying}' already has active CALL exposure ({call_count}/{global_config.max_call_positions_per_underlying}). Duplicate trade rejected."

        if is_put_request and put_count >= global_config.max_put_positions_per_underlying:
            return False, f"EXISTING_POSITION: Underlying '{clean_underlying}' already has active PUT exposure ({put_count}/{global_config.max_put_positions_per_underlying}). Duplicate trade rejected."

        if not global_config.allow_simultaneous_hedges:
            if is_call_request and put_count > 0:
                return False, f"DIRECTIONAL_CONFLICT: Active opposite PUT position exists for '{clean_underlying}'. Simultaneous hedging is disabled by policy."
            if is_put_request and call_count > 0:
                return False, f"DIRECTIONAL_CONFLICT: Active opposite CALL position exists for '{clean_underlying}'. Simultaneous hedging is disabled by policy."

        return True, None

    def register_position_opened(self, trade: Dict[str, Any]) -> None:
        """Deducts capital and registers new position in portfolio."""
        trade_id = str(trade.get("trade_id"))
        entry_p = float(trade.get("entry_price", 0.0))
        qty = float(trade.get("quantity", 1.0))
        mult = 100.0 if trade.get("option_type") in ["CALL", "PUT"] else 1.0
        notional = round(entry_p * qty * mult, 2)

        trade["notional"] = notional
        trade["unrealized_pnl"] = 0.0
        self._open_positions[trade_id] = trade
        self.available_cash = max(0.0, round(self.available_cash - notional, 2))

    def register_position_closed(self, trade_id: str, exit_price: float, realized_pnl: float) -> None:
        """Returns capital to cash and logs realized P&L."""
        self._check_daily_rollover()
        pos = self._open_positions.pop(str(trade_id), None)
        if pos:
            notional = float(pos.get("notional", 0.0))
            self.available_cash = round(self.available_cash + notional + realized_pnl, 2)
            self.realized_pnl = round(self.realized_pnl + realized_pnl, 2)
            self.daily_pnl = round(self.daily_pnl + realized_pnl, 2)

    def hydrate_from_db(self, db_trades: List[Dict[str, Any]], portfolio_state: Optional[Dict[str, Any]] = None) -> None:
        """Hydrates open positions and portfolio balances from database on startup."""
        if portfolio_state:
            self.starting_balance = float(portfolio_state.get("starting_balance", self.starting_balance))
            self.available_cash = float(portfolio_state.get("available_cash", self.available_cash))
            self.realized_pnl = float(portfolio_state.get("realized_pnl", 0.0))
            self.daily_pnl = float(portfolio_state.get("daily_pnl", 0.0))

        self._open_positions.clear()
        for t in db_trades:
            if t.get("status") == "OPEN":
                tid = str(t.get("trade_id"))
                ep = float(t.get("entry_price", 0.0))
                qty = float(t.get("quantity", 1.0))
                mult = 100.0 if t.get("option_type") in ["CALL", "PUT"] else 1.0
                t["notional"] = round(ep * qty * mult, 2)
                t["unrealized_pnl"] = float(t.get("pnl", 0.0) or 0.0)
                self._open_positions[tid] = t

    @property
    def cash(self) -> float:
        return self.available_cash

    @cash.setter
    def cash(self, value: float) -> None:
        self.available_cash = float(value)

    @property
    def open_positions(self) -> Dict[str, Dict[str, Any]]:
        return self._open_positions

    @open_positions.setter
    def open_positions(self, value: Dict[str, Dict[str, Any]]) -> None:
        self._open_positions = value

    def update_parameters(
        self,
        starting_balance: Optional[float] = None,
        risk_per_trade_pct: Optional[float] = None,
        max_exposure_pct: Optional[float] = None,
        daily_loss_limit_pct: Optional[float] = None,
    ) -> None:
        """Update risk and portfolio parameters dynamically."""
        if starting_balance is not None and starting_balance > 0:
            diff = float(starting_balance) - self.starting_balance
            self.starting_balance = float(starting_balance)
            self.initial_balance = float(starting_balance)
            self.available_cash = max(0.0, round(self.available_cash + diff, 2))
        if risk_per_trade_pct is not None:
            r = float(risk_per_trade_pct)
            self.risk_per_trade_pct = r if r <= 1.0 else r / 100.0
        if max_exposure_pct is not None:
            m = float(max_exposure_pct)
            self.max_exposure_pct = m if m <= 1.0 else m / 100.0
        if daily_loss_limit_pct is not None:
            d = float(daily_loss_limit_pct)
            self.daily_loss_limit_pct = d if d <= 1.0 else d / 100.0

    def reset(self, starting_balance: float = None, initial_balance: float = None) -> None:
        bal = starting_balance or initial_balance or self.starting_balance
        self.starting_balance = float(bal)
        self.initial_balance = float(bal)
        self.available_cash = float(bal)
        self.realized_pnl = 0.0
        self.daily_pnl = 0.0
        self._open_positions.clear()
        r = global_config.risk_per_trade_pct
        self.risk_per_trade_pct = r if r <= 1.0 else r / 100.0
        m = global_config.max_portfolio_exposure_pct
        self.max_exposure_pct = m if m <= 1.0 else m / 100.0
        d = global_config.daily_loss_limit_pct
        self.daily_loss_limit_pct = d if d <= 1.0 else d / 100.0

    def get_summary(self) -> Dict[str, Any]:
        return self.get_portfolio_summary()


# Global singleton portfolio manager
global_portfolio = PortfolioManager()
