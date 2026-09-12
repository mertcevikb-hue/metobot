import pytest
import os
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.api import app, global_portfolio, global_config


@pytest.fixture
def client():
    return TestClient(app)


def test_settings_get_returns_normalized_percentages(client):
    """Verify GET /api/v1/settings returns normalized percentage units (0-100 scale) for UI form inputs."""
    global_config.paper_starting_balance = 100000.0
    global_config.risk_per_trade_pct = 0.015  # 1.5%
    global_config.max_portfolio_exposure_pct = 0.30  # 30%
    global_config.daily_loss_limit_pct = 0.05  # 5%

    res = client.get("/api/v1/settings")
    assert res.status_code == 200
    data = res.json()

    assert data["starting_balance"] == 100000.0
    assert data["risk_per_trade_pct"] == 1.5
    assert data["max_capital_allocation_pct"] == 30.0
    assert data["daily_loss_limit_pct"] == 5.0
    assert data["cooldown_minutes"] == global_config.cooldown_minutes


def test_settings_post_updates_and_syncs_portfolio(client):
    """Verify POST /api/v1/settings properly converts percentages and immediately synchronizes portfolio."""
    payload = {
        "starting_balance": 250000.0,
        "risk_per_trade_pct": 2.5,  # 2.5%
        "max_capital_allocation_pct": 50.0,  # 50%
        "daily_loss_limit_pct": 8.0,  # 8%
        "cooldown_minutes": 25
    }

    res = client.post("/api/v1/settings", json=payload)
    assert res.status_code == 200
    resp_data = res.json()
    assert resp_data["success"] is True
    assert "portfolio" in resp_data

    # Check global_config state
    assert global_config.paper_starting_balance == 250000.0
    assert abs(global_config.risk_per_trade_pct - 0.025) < 1e-6
    assert abs(global_config.max_portfolio_exposure_pct - 0.50) < 1e-6
    assert abs(global_config.daily_loss_limit_pct - 0.08) < 1e-6
    assert global_config.cooldown_minutes == 25

    # Check portfolio state via GET /api/v1/portfolio
    port_res = client.get("/api/v1/portfolio")
    assert port_res.status_code == 200
    port_data = port_res.json()

    assert port_data["starting_balance"] == 250000.0
    assert port_data["available_cash"] == 250000.0
    assert port_data["total_equity"] == 250000.0
    assert port_data["daily_loss_limit_pct"] == 8.0
    assert port_data["daily_loss_limit_amount"] == 20000.0  # 250,000 * 8% = $20,000
    assert port_data["max_capital_allocation_pct"] == 50.0
    assert port_data["max_capital_allocation_amount"] == 125000.0  # 250,000 * 50% = $125,000
    assert port_data["risk_per_trade_pct"] == 2.5
    assert port_data["risk_per_trade_amount"] == 6250.0  # 250,000 * 2.5% = $6,250
    assert port_data["cooldown_minutes"] == 25


def test_database_persistence_across_portfolio_calls(client):
    """Verify that updating starting balance persists in database and is not overwritten on subsequent fetches."""
    payload = {"starting_balance": 180000.0}
    res = client.post("/api/v1/settings", json=payload)
    assert res.status_code == 200

    # Call /api/v1/portfolio multiple times
    for _ in range(3):
        port_res = client.get("/api/v1/portfolio")
        assert port_res.status_code == 200
        port_data = port_res.json()
        assert port_data["starting_balance"] == 180000.0


def test_daily_loss_breach_calculation_uses_updated_setting(client):
    """Verify that changing daily_loss_limit_pct in settings dynamically updates the breach check."""
    # Set starting balance to 100k and limit to 4% ($4,000)
    client.post("/api/v1/settings", json={"starting_balance": 100000.0, "daily_loss_limit_pct": 4.0})

    global_portfolio.daily_pnl = -3500.0
    port_res = client.get("/api/v1/portfolio")
    assert port_res.json()["is_daily_loss_breached"] is False

    global_portfolio.daily_pnl = -4200.0  # Exceeds $4,000 limit
    port_res = client.get("/api/v1/portfolio")
    assert port_res.json()["is_daily_loss_breached"] is True

    # Now relax the limit to 6% ($6,000)
    client.post("/api/v1/settings", json={"daily_loss_limit_pct": 6.0})
    port_res = client.get("/api/v1/portfolio")
    # Loss of -$4200 is now safe under the new $6,000 limit!
    assert port_res.json()["daily_loss_limit_amount"] == 6000.0
    assert port_res.json()["is_daily_loss_breached"] is False

    # Cleanup daily_pnl
    global_portfolio.daily_pnl = 0.0


def test_reset_portfolio_with_custom_starting_balance(client):
    """Verify reset portfolio uses the configured starting balance."""
    client.post("/api/v1/settings", json={"starting_balance": 120000.0})

    # Simulate some realized P&L and trades
    global_portfolio.realized_pnl = -5000.0
    global_portfolio.available_cash = 95000.0

    reset_res = client.post("/api/v1/portfolio/reset", json={})
    assert reset_res.status_code == 200
    reset_data = reset_res.json()
    assert reset_data["success"] is True
    assert reset_data["portfolio"]["starting_balance"] == 120000.0
    assert reset_data["portfolio"]["available_cash"] == 120000.0
    assert reset_data["portfolio"]["realized_pnl"] == 0.0
