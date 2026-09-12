from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Float, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class TradeLog(Base):
    """Real and simulated positions table for executed trades."""
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), unique=True, index=True, nullable=False)
    symbol = Column(String(32), index=True, nullable=False)
    underlying = Column(String(32), nullable=True)
    option_type = Column(String(10), nullable=True)  # "CALL" | "PUT" | "STOCK" | "EQUITY"
    strike_price = Column(Float, nullable=True)
    expiration = Column(String(32), nullable=True)
    contract_symbol = Column(String(64), nullable=True)
    side = Column(String(10), nullable=False)  # "LONG" | "SHORT"
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    tp1 = Column(Float, nullable=True)
    tp2 = Column(Float, nullable=True)
    trailing_stop = Column(Float, nullable=True)
    quantity = Column(Float, default=1.0)
    score = Column(Float, nullable=True)
    direction_score = Column(Float, nullable=True)
    direction_edge = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    strategy = Column(String(64), nullable=True)
    signal_type = Column(String(32), nullable=True)  # "BOS", "FVG_ENTRY", "BREAKOUT", etc.
    entry_thesis = Column(Text, nullable=True)
    status = Column(String(20), default="OPEN")  # "OPEN" | "CLOSED" | "FAILED"
    result = Column(String(20), nullable=True)  # "TP" | "TP1" | "TP2" | "SL" | "PROFIT_PROTECTION" | "ROTATION_EXIT" | "TIMEOUT" | "EXIT_SIGNAL" | "MANUAL"
    exit_reason = Column(String(128), nullable=True)
    reject_reason = Column(String(128), nullable=True)
    instrument = Column(String(16), default="STOCK")
    is_0dte = Column(Integer, default=0)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)


class PortfolioState(Base):
    """Paper trading portfolio and artificial balance state."""
    __tablename__ = "portfolio_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    starting_balance = Column(Float, default=100000.0, nullable=False)
    available_cash = Column(Float, default=100000.0, nullable=False)
    realized_pnl = Column(Float, default=0.0, nullable=False)
    daily_pnl = Column(Float, default=0.0, nullable=False)
    last_reset_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RejectedSignal(Base):
    """Audit log of signals vetoed or rejected by Trade Engine."""
    __tablename__ = "rejected_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), index=True, nullable=False)
    underlying = Column(String(32), nullable=True)
    direction = Column(String(16), nullable=False)
    strategy = Column(String(64), nullable=True)
    score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    market_regime = Column(String(32), nullable=True)
    reason_code = Column(String(64), index=True, nullable=False)
    reason_detail = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AttributionLog(Base):
    """Bot 1'in Post-Mortem (Neden SL/TP oldu?) sebeplendirme raporları."""
    __tablename__ = "attribution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), index=True, nullable=False)
    symbol = Column(String(32), nullable=False)
    result = Column(String(20), nullable=False)
    primary_reason = Column(String(64), index=True, nullable=False)  # "FAKE_BOS", "LIQUIDITY_SWEEP_TRAP"
    tags = Column(JSON, default=[])
    weight_adjustment_hint = Column(JSON, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LiveSignal(Base):
    """Bot 2'nin canlı ürettiği skorlar ve sinyal kayıtları."""
    __tablename__ = "live_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), index=True, nullable=False)
    score = Column(Float, nullable=False)  # 0 - 100
    regime = Column(String(32), nullable=False)
    features = Column(JSON, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ModelWeight(Base):
    """Bot 1'in optimize edip Bot 2'nin okuduğu model ağırlık matrisi."""
    __tablename__ = "model_weights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, unique=True, index=True, nullable=False)
    weights = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WatchlistSymbol(Base):
    __tablename__ = "watchlist_symbols"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))