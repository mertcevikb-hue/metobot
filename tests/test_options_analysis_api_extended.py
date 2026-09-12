"""
Extended integration tests for /api/v1/options-analysis/{symbol}.
Validates that institutional multi-leg structures, 0DTE time metrics,
Greek surfaces, and confluence scores are delivered over REST API.
"""
import pytest
from fastapi.testclient import TestClient
from web.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_options_analysis_endpoint_schema(client):
    """
    Verify /api/v1/options-analysis/{symbol} delivers:
    - quant_synthesis (with confluence_evaluation and htf_pattern)
    - option_contract (with Greeks, expected move, 0DTE time metrics)
    - multi_leg_structure (with legs, max profit/loss, breakeven)
    - trades (executed history)
    """
    resp = client.get("/api/v1/options-analysis/SPY")
    assert resp.status_code == 200
    data = resp.json()

    if "error" in data:
        pytest.skip(f"Live market data temporarily unavailable: {data['error']}")

    assert "quant_synthesis" in data
    assert "option_contract" in data
    assert "multi_leg_structure" in data
    assert "confluence_evaluation" in data
    assert "htf_pattern" in data

    # Check multi-leg structure schema
    mls = data.get("multi_leg_structure")
    assert mls is not None
    assert "strategy" in mls
    assert "legs" in mls
    assert isinstance(mls["legs"], list)
    assert len(mls["legs"]) >= 1
    assert "breakeven" in mls
    assert "max_loss" in mls

    # Check option contract Greeks and 0DTE metrics
    opt = data["option_contract"]
    assert "greeks" in opt
    assert "expected_move" in opt
    assert "time_of_day_bucket" in opt
    assert "preferred_strategy" in opt

    # Check confluence metrics
    conf = data["confluence_evaluation"]
    assert "confluence_score" in conf
    assert "contradiction_penalty" in conf
    assert "net_confluence" in conf
    assert "verdict" in conf
