import pytest
import math
from fastapi.testclient import TestClient
from web.api import app
from engine.data_guard import DataGuard
from engine.features import FeatureEngine
from engine.risk_engine import RiskEngine
from scoring.calculator import ScoreCalculator
from database.db_manager import DatabaseManager


@pytest.fixture
def client():
    return TestClient(app)


def test_watchlist_injection_prevention(client):
    """XSS, SQL Injection ve zararlı sembol girişlerinin engellendiğini doğrular."""
    malicious_inputs = [
        "<script>alert('xss')</script>",
        "'; DROP TABLE watchlist_symbols; --",
        "../../etc/passwd",
        "SELECT * FROM users",
        "A" * 50,  # Aşırı uzun payload
        "",  # Boşluk
        "SPY$!#@%"
    ]
    for bad_sym in malicious_inputs:
        resp = client.post("/api/v1/watchlist", json={"symbol": bad_sym})
        assert resp.status_code in [400, 422], f"Zararlı girdi engellenmedi: {bad_sym}"


def test_ai_chat_payload_length_limits(client):
    """AI chat uç noktasına aşırı uzun veya boş payload gönderiminin engellendiğini doğrular."""
    # Boş mesaj
    resp_empty = client.post("/api/v1/ai/chat", json={"message": ""})
    assert resp_empty.status_code in [400, 422]

    # 2500 karakterden uzun mesaj
    resp_long = client.post("/api/v1/ai/chat", json={"message": "X" * 3000})
    assert resp_long.status_code in [400, 422]


def test_data_guard_price_invariants():
    """DataGuard'ın bozuk, negatif veya invariantları ihlal eden mumları reddettiğini doğrular."""
    valid_c = [100.0 + i for i in range(40)]
    valid_h = [c + 1.0 for c in valid_c]
    valid_l = [c - 1.0 for c in valid_c]
    valid_v = [1000.0 for _ in valid_c]

    # 1. Normal veri geçerli olmalı
    ok, err = DataGuard.validate_ohlcv(valid_c, valid_h, valid_l, valid_v)
    assert ok is True, f"Geçerli veri reddedildi: {err}"

    # 2. NaN değeri reddedilmeli
    nan_c = list(valid_c)
    nan_c[10] = float("nan")
    ok, err = DataGuard.validate_ohlcv(nan_c, valid_h, valid_l, valid_v)
    assert ok is False
    assert "NaN/Inf" in err or "Corrupted" in err

    # 3. Negatif veya sıfır fiyat reddedilmeli
    zero_c = list(valid_c)
    zero_c[5] = 0.0
    ok, err = DataGuard.validate_ohlcv(zero_c, valid_h, valid_l, valid_v)
    assert ok is False

    # 4. Invariant: High < Low reddedilmeli
    bad_h = list(valid_h)
    bad_h[5] = valid_l[5] - 1.0
    ok, err = DataGuard.validate_ohlcv(valid_c, bad_h, valid_l, valid_v)
    assert ok is False

    # 5. Invariant: High < Close reddedilmeli
    bad_h2 = list(valid_h)
    bad_h2[5] = valid_c[5] - 0.5
    ok, err = DataGuard.validate_ohlcv(valid_c, bad_h2, valid_l, valid_v)
    assert ok is False


def test_feature_engine_zero_atr_resilience():
    """Hareketsiz / tahtası durmuş piyasalarda ATR=0 durumunda ZeroDivisionError oluşmadığını doğrular."""
    flat_closes = [100.0 for _ in range(40)]
    flat_highs = [100.0 for _ in range(40)]
    flat_lows = [100.0 for _ in range(40)]
    flat_volumes = [100.0 for _ in range(40)]

    features = FeatureEngine.extract_features(flat_highs, flat_lows, flat_closes, flat_volumes)
    assert features is not None
    assert isinstance(features.get("z_vwap_distance"), float)
    assert not math.isnan(features.get("z_vwap_distance"))


def test_score_calculator_nan_resilience():
    """ScoreCalculator'ın bozuk NaN/None girdilerde asla NaN üretmediğini doğrular."""
    calc = ScoreCalculator()
    corrupt_features = {
        "is_long_setup": 1.0,
        "liquidity_sweep": float("nan"),
        "BOS_confirmation": None,
        "OB_freshness": float("inf")
    }
    weights = {
        "liquidity_sweep": 25.0,
        "BOS_confirmation": 25.0,
        "OB_freshness": 25.0,
        "execution_threshold": 75.0
    }
    score = calc.compute(corrupt_features, regime="trending_bull", weights=weights)
    assert isinstance(score, float)
    assert not math.isnan(score)
    assert 0.0 <= score <= 100.0


def test_risk_engine_invalid_inputs():
    """RiskEngine'in geçersiz fiyat veya sıfır volatilitede hata vermeden güvenle reddettiğini doğrular."""
    res1 = RiskEngine.evaluate_risk(price=0.0, atr=1.5, side="LONG")
    assert res1["approved"] is False

    res2 = RiskEngine.evaluate_risk(price=100.0, atr=0.0, side="LONG")
    assert res2["approved"] is False


def test_database_manager_sanitization():
    """Veritabanı yöneticisinin kötü niyetli SQL/XSS girdilerini reddettiğini doğrular."""
    import asyncio
    async def _run():
        db = DatabaseManager()
        await db.connect()
        try:
            assert await db.add_symbol("'; DROP TABLE watchlist_symbols; --") is False
            assert await db.add_symbol("<img src=x onerror=alert() />") is False
        finally:
            await db.close()
    asyncio.run(_run())
