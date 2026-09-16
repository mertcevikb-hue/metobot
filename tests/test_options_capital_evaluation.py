import pytest
from engine.portfolio import PortfolioManager
from engine.trade_engine import TradeEngine
from httpx import AsyncClient, ASGITransport
from web.api import app


@pytest.fixture
def custom_portfolio():
    # Portfolio with $300.00 starting balance and cash, exactly matching the user's dashboard
    pm = PortfolioManager(starting_balance=300.0, risk_per_trade_pct=0.03, max_exposure_pct=0.90)
    return pm


@pytest.fixture
def custom_trade_engine(custom_portfolio):
    return TradeEngine(portfolio=custom_portfolio)


def test_option_sizing_uses_premium_not_stock_price(custom_portfolio, custom_trade_engine):
    """
    Verifies that when evaluating an option trade candidate, the capital requirement
    and exposure check use the contract premium price ($2.58 -> $258.00 for 1 contract)
    instead of the stock spot price ($759.31).
    With $300.00 available cash, $258.00 MUST NOT trigger INSUFFICIENT_CAPITAL.
    """
    candidate = {
        "symbol": "NVDA_260918P00759000",
        "underlying": "NVDA",
        "instrument": "OPTION",
        "decision": "SHORT",
        "price": 759.31,  # Stock price in candidate payload
        "premium": 2.58,  # Option contract premium ($2.58 / share)
        "score": 85.0,
        "strategy": "BEAR_PUT_SPREAD",
        "entry_valid": True,
        "direction_evaluation": {
            "bias": "BEARISH",
            "direction_edge": -25.0,
            "is_conflict": False
        },
        "confidence_evaluation": {
            "model_confidence": 85.0,
            "threshold": 75.0
        },
        "risk_metrics": {
            "entry_price": 759.31,
            "stop_loss": 765.0,  # Stock stop loss
            "opt_sl": 1.95,      # Option stop loss
            "reward_risk_ratio": 2.5
        },
        "option_data": {
            "status": "Active",
            "is_liquid": True,
            "premium": 2.58,
            "strike_price": "$759.00",
            "multi_leg_structure": {
                "strategy": "LONG_PUT",
                "net_debit": 2.58,
                "max_loss": 257.50
            }
        }
    }

    result = custom_trade_engine.evaluate_candidate(candidate, is_market_open=True)

    # Planned notional must be 1 contract * 2.58 * 100 = 258.0, NOT 759.31 or 75,931
    assert result["planned_quantity"] == 1.0
    assert result["planned_notional"] == 258.0
    assert result["gate_verdicts"]["exposure_limit"] == "PASSED"

    # INSUFFICIENT_CAPITAL must NOT be present
    for reason in result["rejection_reasons"]:
        assert "INSUFFICIENT_CAPITAL" not in reason


def test_option_correctly_triggers_insufficient_capital_when_premium_exceeds_cash(custom_portfolio, custom_trade_engine):
    """
    If the option premium requires $350.00 ($3.50/share) and cash is $300.00,
    it must report required capital based on premium ($350.00), not stock price.
    """
    candidate = {
        "symbol": "NVDA_OPT",
        "underlying": "NVDA",
        "instrument": "OPTION",
        "decision": "LONG",
        "price": 759.31,
        "premium": 3.50,  # $3.50 * 100 = $350.00 > $300.00 cash
        "score": 88.0,
        "strategy": "LONG_CALL",
        "entry_valid": True,
        "direction_evaluation": {"bias": "BULLISH", "direction_edge": 30.0, "is_conflict": False},
        "confidence_evaluation": {"model_confidence": 88.0, "threshold": 75.0},
        "option_data": {
            "status": "Active",
            "is_liquid": True,
            "premium": 3.50
        }
    }

    result = custom_trade_engine.evaluate_candidate(candidate, is_market_open=True)
    assert result["approved"] is False
    assert any("INSUFFICIENT_CAPITAL: Required $350.00 exceeds available cash $300.00." in r for r in result["rejection_reasons"])


def test_option_portfolio_stop_loss_safeguard(custom_portfolio):
    """
    If stock stop loss ($750) is mistakenly passed to calculate_position_size
    alongside option entry_price ($2.58), it must not compute abs(2.58 - 750.0).
    """
    qty, notional, expl = custom_portfolio.calculate_position_size(
        symbol="SPY_OPT",
        instrument="OPTION",
        entry_price=2.58,
        stop_loss=750.0,  # Stock stop loss passed by mistake
        confidence=80.0,
        is_0dte=False
    )
    assert qty >= 1
    assert notional == round(qty * 2.58 * 100.0, 2)
    # The risk per contract must be a realistic option risk, not 747 * 100 ($74,700)
    assert "Risk: $" in expl
    assert float(expl.split("Risk: $")[1].replace(")", "")) < 300.0


@pytest.mark.asyncio
async def test_options_analysis_api_decision_log_no_false_insufficient_capital():
    """
    Verify /api/v1/options-analysis endpoint evaluates trade_evaluation using option economics.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/options-analysis/SPY")
        assert res.status_code == 200
        data = res.json()
        assert "quant_synthesis" in data
        q = data["quant_synthesis"]
        trade_eval = q.get("trade_evaluation", {})
        rejections = trade_eval.get("rejection_reasons", [])
        
        # Ensure that if INSUFFICIENT_CAPITAL appears, it does not claim the underlying stock price ($500+)
        # exceeds cash when cash is sufficient for the option contract
        for r in rejections:
            if "INSUFFICIENT_CAPITAL" in r:
                assert q.get("price", 0) not in r
