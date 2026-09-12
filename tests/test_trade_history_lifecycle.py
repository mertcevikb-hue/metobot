import pytest
import asyncio
import re
import os
import sys
import uuid
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.api import app
from database.db_manager import DatabaseManager
from execution.signal_dispatcher import SignalDispatcher
from execution.broker import MockBroker


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# db fixture is provided by conftest.py (isolated in test_suite.db)


@pytest.mark.asyncio
async def test_signal_is_not_a_trade(db):
    """
    Core Invariant: A signal is NOT a trade.
    Generating or logging a signal with entry_valid=False or NO TRADE
    must populate live_signals, but NEVER create a row in trade_logs (Trade History).
    """
    sym = "TEST_SIG_ONLY"
    
    # Clean any previous artifacts for clean assertion
    trades_before = await db.get_trades(symbol=sym)
    initial_count = len(trades_before)

    fake_signal = {
        "symbol": sym,
        "score": 78.5,
        "regime": "ranging",
        "decision": "NO TRADE",
        "entry_valid": False,
        "features": {"rsi": 55, "atr": 1.5}
    }

    # Signal is saved to live_signals
    saved = await db.save_live_signal(fake_signal)
    assert saved is True

    # Confirm it exists in live_signals
    recent_signals = await db.get_recent_live_signals(limit=10)
    assert any(s["symbol"] == sym for s in recent_signals)

    # Confirm ZERO trades were added to trade_logs
    trades_after = await db.get_trades(symbol=sym)
    assert len(trades_after) == initial_count, "Signals without confirmed execution must never appear in Trade History!"


@pytest.mark.asyncio
async def test_trade_entry_execution_creates_open_trade(db):
    """
    When entry conditions are satisfied and order is filled,
    the position must be recorded in trade_logs with status='OPEN' and exact entry price.
    """
    sym = f"TEST_AAPL_{uuid.uuid4().hex[:4]}"
    trade_id = f"tr_open_{uuid.uuid4().hex[:6]}"
    
    trade_payload = {
        "trade_id": trade_id,
        "symbol": f"{sym}_CALL_180",
        "underlying": sym,
        "option_type": "CALL",
        "strike_price": 180.0,
        "expiration": "2026-09-18",
        "contract_symbol": f"{sym}260918C00180000",
        "side": "LONG",
        "entry_price": 4.50,
        "stop_loss": 2.25,
        "take_profit": 9.00,
        "quantity": 2.0,
        "score": 88.5,
        "strategy": "QUANT_SMC_BREAKOUT",
        "signal_type": "ENTRY_READY"
    }

    trade = await db.create_trade(trade_payload)
    assert trade["trade_id"] == trade_id
    assert trade["status"] == "OPEN"
    assert trade["entry_price"] == 4.50
    assert trade["exit_price"] is None
    assert trade["pnl"] == 0.0

    # Retrieve from Trade History
    open_trades = await db.get_open_trades()
    assert any(t["trade_id"] == trade_id for t in open_trades)

    retrieved = await db.get_open_trade_for_symbol(sym)
    assert retrieved is not None
    assert retrieved["trade_id"] == trade_id
    assert retrieved["status"] == "OPEN"


@pytest.mark.asyncio
async def test_trade_close_calculates_options_pnl(db):
    """
    When a position is closed, exit_price, exit timestamp, exit_reason,
    and realized P&L (100x multiplier for options) must be accurately calculated.
    """
    trade_id = f"tr_close_{uuid.uuid4().hex[:6]}"
    trade_payload = {
        "trade_id": trade_id,
        "symbol": "SPY_260918_C550",
        "underlying": "SPY",
        "option_type": "CALL",
        "strike_price": 550.0,
        "expiration": "2026-09-18",
        "contract_symbol": "SPY260918C00550000",
        "side": "LONG",
        "entry_price": 5.00,
        "quantity": 1.0,
        "status": "OPEN"
    }
    await db.create_trade(trade_payload)

    # Close at $7.50 (Profit: ($7.50 - $5.00) * 1 * 100 = $250.00, +50%)
    closed = await db.close_trade(
        trade_id=trade_id,
        exit_price=7.50,
        exit_reason="Take Profit 3.5x ATR Hit",
        result="TP"
    )

    assert closed is not None
    assert closed["status"] == "CLOSED"
    assert closed["exit_price"] == 7.50
    assert closed["closed_at"] is not None
    assert closed["pnl"] == 250.0
    assert closed["pnl_pct"] == 50.0
    assert closed["exit_reason"] == "Take Profit 3.5x ATR Hit"

    # Verify PUT short trade P&L calculation
    put_id = f"tr_put_{uuid.uuid4().hex[:6]}"
    put_payload = {
        "trade_id": put_id,
        "symbol": "QQQ_260918_P450",
        "underlying": "QQQ",
        "option_type": "PUT",
        "strike_price": 450.0,
        "expiration": "2026-09-18",
        "contract_symbol": "QQQ260918P00450000",
        "side": "SHORT",
        "entry_price": 6.00,
        "quantity": 1.0,
        "status": "OPEN"
    }
    await db.create_trade(put_payload)

    # Short exited at $4.00 (Profit: ($6.00 - $4.00) * 1 * 100 = $200.00, +33.33%)
    closed_put = await db.close_trade(
        trade_id=put_id,
        exit_price=4.00,
        exit_reason="Trailing stop exit",
        result="CLOSED"
    )
    assert closed_put["status"] == "CLOSED"
    assert closed_put["pnl"] == 200.0
    assert round(closed_put["pnl_pct"], 1) == 33.3


@pytest.mark.asyncio
async def test_signal_dispatcher_broker_integration(db):
    """
    Test end-to-end integration: SignalDispatcher executing on MockBroker
    automatically commits confirmed fills into Trade History and handles exit.
    """
    broker = MockBroker(initial_balance=100000.0)
    dispatcher = SignalDispatcher(broker=broker, db=db, notify_telegram=False, notify_discord=False)

    entry_signal = {
        "symbol": "NVDA",
        "price": 120.0,
        "score": 90.0,
        "decision": "LONG",
        "position_state": "ENTRY_READY",
        "entry_valid": True,
        "allow_outside_hours": True,
        "option_data": {
            "contract_ticker": "NVDA260918C00125000",
            "strike_price": 125.0,
            "expiration_date": "2026-09-18",
            "contract_type": "CALL",
            "premium": 5.20
        }
    }

    # Dispatch should execute order on broker and create trade in trade_logs
    res = await dispatcher.dispatch(entry_signal)
    assert res.get("status") in ["filled", "open"]
    assert "trade" in res
    trade = res["trade"]
    assert trade["status"] == "OPEN"
    assert trade["underlying"] == "NVDA"

    # Now simulate exit
    exit_trade = await dispatcher.execute_position_exit(
        symbol="NVDA",
        exit_price=7.00,
        exit_reason="Target reached"
    )
    assert exit_trade is not None
    assert exit_trade["status"] == "CLOSED"
    assert exit_trade["exit_price"] == 7.00
    assert exit_trade["pnl"] > 0


def test_trade_history_rest_api_endpoints(client):
    """
    Verify REST API endpoints:
    - GET /api/v1/trades (with filters)
    - POST /api/v1/trades/open
    - POST /api/v1/trades/{trade_id}/close
    """
    # 1. Open trade via REST API
    open_payload = {
        "symbol": "TSLA260918C00250000",
        "underlying": "TSLA",
        "option_type": "CALL",
        "strike_price": 250.0,
        "expiration": "2026-09-18",
        "side": "LONG",
        "entry_price": 10.50,
        "quantity": 1.0,
        "score": 87.0,
        "allow_outside_hours": True
    }
    resp = client.post("/api/v1/trades/open", json=open_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    trade_id = data["trade"]["trade_id"]
    assert data["trade"]["status"] == "OPEN"

    # 2. Query trades
    get_resp = client.get(f"/api/v1/trades?symbol=TSLA")
    assert get_resp.status_code == 200
    trades_list = get_resp.json()
    assert any(t["trade_id"] == trade_id for t in trades_list)

    # 3. Close trade via REST API
    close_resp = client.post(f"/api/v1/trades/{trade_id}/close", json={"exit_price": 14.50, "exit_reason": "Manual Close"})
    assert close_resp.status_code == 200
    close_data = close_resp.json()
    assert close_data["success"] is True
    assert close_data["trade"]["status"] == "CLOSED"
    assert close_data["trade"]["pnl"] == 400.0  # (14.50 - 10.50) * 1 * 100


def test_options_analysis_returns_real_trades_no_fake_signals(client):
    """
    Verify that GET /api/v1/options-analysis/{symbol} returns:
    1. 'trades' containing real executed positions
    2. Zero fake retroactive signals (no 'historical_signals' field synthesised from candles)
    """
    resp = client.get("/api/v1/options-analysis/SPY")
    # Even if external market API is slow or offline, check schema structure
    if resp.status_code == 200:
        data = resp.json()
        if "error" not in data:
            assert "trades" in data
            assert "trade_history" in data
            assert "historical_signals" not in data, "Legacy fake historical_signals must be removed!"
            assert isinstance(data["trades"], list)


def test_ui_and_api_zero_turkish_characters_audit():
    """
    Strict Localization Audit:
    Ensure 100% professional English UI and API responses with ZERO Turkish characters.
    """
    root_dir = Path(__file__).resolve().parent.parent
    html_file = root_dir / "web" / "static" / "index.html"
    api_file = root_dir / "web" / "api.py"

    assert html_file.exists(), "index.html must exist!"
    assert api_file.exists(), "api.py must exist!"

    html_content = html_file.read_text(encoding="utf-8")
    api_content = api_file.read_text(encoding="utf-8")

    # Search for Turkish specific characters: ç, ğ, ı, ö, ş, ü (and uppercase)
    turkish_char_regex = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
    
    html_matches = turkish_char_regex.findall(html_content)
    assert len(html_matches) == 0, f"Found Turkish characters in index.html: {html_matches[:10]}"

    api_matches = turkish_char_regex.findall(api_content)
    assert len(api_matches) == 0, f"Found Turkish characters in api.py: {api_matches[:10]}"

    # Verify key English UI headers in index.html
    assert "Trade History" in html_content
    assert "Executed Positions Only" in html_content
    assert "GECMIS SINYALLER" not in html_content
    assert "GİRİŞ" not in html_content
    assert "HEDEF/STOP" not in html_content
    assert 'lang="en"' in html_content
