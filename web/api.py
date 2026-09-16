import os
import asyncio
import re
import json
import math
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import asdict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator
from loguru import logger
import urllib.request
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import google.generativeai as genai

from database.db_manager import DatabaseManager, DATABASE_URL
from database.schema import LiveSignal, ModelWeight, WatchlistSymbol
from web.routes.ws import router as ws_router, broadcast_trade_update, broadcast_portfolio_update, broadcast_rejected_signal
from config.trading_config import global_config
from engine.portfolio import global_portfolio
from engine.trade_engine import global_trade_engine
from engine.position_manager import global_position_manager

from contextlib import asynccontextmanager

db_manager = DatabaseManager(DATABASE_URL)

async def session_watchdog_loop():
    """
    Background watchdog loop enforcing the 16:30 - 23:00 GMT+3 hard session boundary.
    Runs every 1 second.
    At exactly 23:00:00 GMT+3 (or anytime outside 16:30 - 23:00):
    Forces immediate close of any open positions in database and broadcasts to WebSocket.
    Guarantees: IF current_time >= 23:00:00 GMT+3 or outside normal session, OPEN POSITIONS = 0.
    """
    logger.info("⏱️ Session Watchdog Loop started (1-second tick).")
    while True:
        try:
            await asyncio.sleep(1.0)
            from engine.market_hours import MarketSchedule
            if MarketSchedule.is_market_closed():
                open_trades = await db_manager.get_open_trades()
                if open_trades:
                    logger.warning(
                        f"🚨 Session Watchdog: Session is CLOSED ({len(open_trades)} open positions detected). Forcing immediate closure!"
                    )
                    closed = await db_manager.close_all_open_positions(
                        exit_reason="23:00 End-of-Session Forced Close"
                    )
                    try:
                        from web.routes.ws import broadcast_trade_update
                        for ct in closed:
                            await broadcast_trade_update(ct)
                    except Exception as ws_err:
                        logger.warning(f"Failed to broadcast watchdog forced close: {ws_err}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in session_watchdog_loop: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.connect()
    # 1. Startup reconciliation of stale open positions
    reconciled = await db_manager.reconcile_stale_positions()
    if reconciled > 0:
        logger.warning(f"🛡️ Startup Reconcile: Closed {reconciled} stale open positions outside session hours.")

    # 2. Launch background session watchdog loop
    watchdog_task = asyncio.create_task(session_watchdog_loop())

    yield

    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    await db_manager.close()

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Metobot AI Quantitative Terminal", version="0.9.2", docs_url="/docs", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ws_router)

TICKER_REGEX = re.compile(r"^[A-Za-z0-9.\-_/]{1,15}$")

def sanitize_ticker(sym: str) -> str:
    clean = (sym or "").strip().upper().replace("$", "")
    if not clean or not TICKER_REGEX.match(clean):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid symbol format: '{sym}'. Only letters, numbers, and standard ticker symbols (. - /) are accepted."
        )
    return clean

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2500)

class SymbolRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=15)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return sanitize_ticker(v)

class OpenTradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=64)
    underlying: Optional[str] = Field(None, max_length=32)
    option_type: Optional[str] = Field("CALL", max_length=10)
    strike_price: Optional[float] = None
    expiration: Optional[str] = Field(None, max_length=32)
    contract_symbol: Optional[str] = Field(None, max_length=64)
    side: str = Field("LONG", max_length=10)
    entry_price: float = Field(..., gt=0.0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    quantity: float = Field(1.0, gt=0.0)
    score: Optional[float] = None
    strategy: Optional[str] = Field("QUANT_SETUP", max_length=64)
    allow_outside_hours: bool = Field(False)

class CloseTradeRequest(BaseModel):
    exit_price: float = Field(..., gt=0.0)
    exit_reason: Optional[str] = Field("Manual / UI Execution", max_length=128)

# ==========================================
from engine import DataGuard, FeatureEngine, RegimeEngine, StrategyEngine, RiskEngine, QuantEngine, MarketSchedule
from engine.option_engine import OptionEngine
from engine.option_strategy_library import OptionStrategyLibrary

# ==========================================
# 2. FASTAPI & FRONTEND ROUTES
# ==========================================
@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools():
    return {}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def read_index():
    html_file = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))




@app.get("/api/v1/watchlist")
async def get_watchlist():
    return await db_manager.get_watchlist()

@app.get("/api/v1/market-status")
async def get_market_status(symbol: str = "SPY"):
    """Return real-time market schedule, session status, and countdown to open/close."""
    clean = sanitize_ticker(symbol)
    return MarketSchedule.get_market_status(clean)

@app.get("/api/v1/market-metrics")
async def get_market_metrics():
    # If session is closed, ensure stale positions are reconciled to 0
    if MarketSchedule.is_market_closed():
        await db_manager.reconcile_stale_positions()

    watchlist = await db_manager.get_watchlist()
    if not watchlist:
        return []

    # Cross-reference with open trades from trade_logs
    open_trades = await db_manager.get_open_trades()
    open_map = {}
    for ot in open_trades:
        keys = [
            (ot.get("underlying") or "").strip().upper(),
            (ot.get("symbol") or "").strip().upper()
        ]
        for k in keys:
            if k and k not in open_map:
                open_map[k] = ot

    def _sync_fetch_json(url: str) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None
        return None

    async def _fetch_metric(sym: str) -> Dict[str, Any]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"
        m_status = MarketSchedule.get_market_status(sym)
        clean_sym = sym.strip().upper()
        open_pos = open_map.get(clean_sym)
        pos_data = None
        if open_pos:
            c_dt = None
            if open_pos.get("created_at"):
                try:
                    c_dt = datetime.datetime.fromisoformat(open_pos["created_at"])
                except Exception:
                    pass
            pos_timing = MarketSchedule.format_position_timestamp(c_dt)
            pos_data = {
                "trade_id": open_pos.get("trade_id"),
                "symbol": open_pos.get("symbol"),
                "side": open_pos.get("side"),
                "option_type": open_pos.get("option_type"),
                "entry_price": open_pos.get("entry_price"),
                "quantity": open_pos.get("quantity", 1.0),
                "created_at": open_pos.get("created_at"),
                "time_et": pos_timing["time_et"],
                "time_utc": pos_timing["time_utc"],
                "date": pos_timing["date"],
                "display": pos_timing["display"],
                "full_display": pos_timing["full_display"],
                "duration_str": pos_timing["duration_str"],
                "duration_seconds": pos_timing["duration_seconds"]
            }

        try:
            data = await asyncio.to_thread(_sync_fetch_json, url)
            if not data:
                return {
                    "symbol": sym, "error": True,
                    "has_open_position": open_pos is not None,
                    "open_position": pos_data,
                    "market_status": m_status
                }
            result = data.get('chart', {}).get('result')
            if not result:
                return {
                    "symbol": sym, "error": True,
                    "has_open_position": open_pos is not None,
                    "open_position": pos_data,
                    "market_status": m_status
                }
            indicators = result[0]['indicators']['quote'][0]
            closes = [c for c in indicators.get('close', []) if c is not None]
            if len(closes) < 2:
                return {
                    "symbol": sym, "error": True,
                    "has_open_position": open_pos is not None,
                    "open_position": pos_data,
                    "market_status": m_status
                }
            curr, prev = closes[-1], closes[-2]
            h52, l52 = max(closes), min(closes)
            return {
                "symbol": sym,
                "price": curr,
                "daily_change": ((curr - prev) / prev) * 100,
                "dist_from_high": ((h52 - curr) / h52) * 100,
                "dist_from_low": ((curr - l52) / l52) * 100,
                "error": False,
                "has_open_position": open_pos is not None,
                "open_position": pos_data,
                "market_status": m_status
            }
        except Exception:
            return {
                "symbol": sym, "error": True,
                "has_open_position": open_pos is not None,
                "open_position": pos_data,
                "market_status": m_status
            }

    tasks = [_fetch_metric(item.symbol) for item in watchlist]
    metrics = await asyncio.gather(*tasks)
    return list(metrics)

@app.get("/api/v1/scanner")
async def get_scanner_data(q: str = ""):
    defs = ["THYAO.IS", "TUPRS.IS", "KCHOL.IS", "ISCTR.IS", "AKBNK.IS", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN"]
    if q.strip():
        clean_q = sanitize_ticker(q)
        symbols = [clean_q]
    else:
        symbols = defs

    def _sync_fetch_scanner(sym: str) -> Dict[str, Any]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    res = data.get('chart', {}).get('result')
                    if res:
                        closes = [c for c in res[0]['indicators']['quote'][0].get('close', []) if c is not None]
                        if len(closes) >= 2:
                            curr, prev = closes[-1], closes[-2]
                            h52, l52 = max(closes), min(closes)
                            return {
                                "symbol": sym,
                                "price": curr,
                                "daily_change": ((curr - prev) / prev) * 100,
                                "dist_from_high": ((h52 - curr) / h52) * 100,
                                "dist_from_low": ((curr - l52) / l52) * 100,
                                "error": False
                            }
        except Exception:
            pass
        return {"symbol": sym, "error": True}

    tasks = [asyncio.to_thread(_sync_fetch_scanner, sym) for sym in symbols]
    metrics = await asyncio.gather(*tasks)
    return list(metrics)

# ==========================================
# 3. OPTIONS ANALYSIS WITH CBOE LIVE CHAIN
# ==========================================
@app.get("/api/v1/options-analysis/{symbol}")
async def analyze_option(symbol: str):
    sym = sanitize_ticker(symbol)
    
    # 1. Fetch 6mo historical candles using yfinance
    def _fetch_hist():
        import yfinance as yf
        import pandas as pd
        t = yf.Ticker(sym)
        df = t.history(period="6mo", interval="1d")
        if df.empty:
            return [], [], [], [], []
        dates_list = [str(idx.strftime("%Y-%m-%d")) for idx in df.index]
        closes_list = [float(x) for x in df["Close"].tolist() if not pd.isna(x)]
        highs_list = [float(x) for x in df["High"].tolist() if not pd.isna(x)]
        lows_list = [float(x) for x in df["Low"].tolist() if not pd.isna(x)]
        volumes_list = [float(x) if (not pd.isna(x) and float(x) > 0) else 1.0 for x in df["Volume"].tolist()]
        return dates_list, closes_list, highs_list, lows_list, volumes_list

    try:
        dates, closes, highs, lows, volumes = await asyncio.to_thread(_fetch_hist)
    except Exception as e:
        return {"error": f"Failed to fetch price history for ({sym}): {str(e)}"}

    if len(closes) < 35:
        return {"error": f"Insufficient historical price data found for {sym}."}

    quant_res = QuantEngine.analyze_ticker(sym, highs, lows, closes, volumes)
    if quant_res.get("decision") == "NO TRADE" and any("Data" in w for w in quant_res.get("warnings", [])):
        return {"error": quant_res["warnings"][0]}

    # Real Executed Trade History from database (Only actual entered positions)
    trades = await db_manager.get_trades(symbol=sym, limit=50)

    # 2. Fetch 100% REAL CBOE Options Chain via PolygonOptionsClient
    c_type = "call" if quant_res["decision"] in ["LONG", "BUY"] else ("put" if quant_res["decision"] in ["SHORT", "SELL"] else "call")
    opt_data = {
        "direction": "CALL" if c_type == "call" else "PUT",
        "strike": None, "premium": None, "expiration": "Data unavailable",
        "volume": "Data unavailable", "open_interest": "Data unavailable",
        "implied_volatility": "Data unavailable", "bid_ask": "Data unavailable",
        "contract_symbol": "Data unavailable",
        "option_score": 0.0
    }

    try:
        from data.polygon_client import PolygonOptionsClient
        poc = PolygonOptionsClient()
        stock_price = quant_res.get("price", 0.0)
        contracts = await poc.get_options_chain(sym, contract_type=c_type, spot_price=stock_price)
        if contracts:
            best_c = min(contracts, key=lambda x: abs(x.get('strike_price', 0) - stock_price))
            opt_eval = OptionEngine.evaluate(
                spot_price=stock_price,
                spot_atr=atr if 'atr' in locals() else max(stock_price * 0.015, 0.50),
                direction_bias=quant_res.get("direction_bias", "NEUTRAL"),
                option_quote=best_c,
                contract_meta=best_c
            )
            opt_data.update({
                "strike": best_c.get("strike_price"),
                "premium": opt_eval.get("premium"),
                "expiration": best_c.get("expiration_date", "N/A"),
                "volume": opt_eval.get("volume"),
                "open_interest": opt_eval.get("open_interest"),
                "implied_volatility": opt_eval.get("iv_pct_str"),
                "bid_ask": f"${opt_eval.get('bid', 0.0):.2f} / ${opt_eval.get('ask', 0.0):.2f}",
                "contract_symbol": best_c.get("ticker", "N/A"),
                "option_score": opt_eval.get("option_score"),
                "is_0dte": opt_eval.get("is_0dte"),
                "dte": opt_eval.get("dte"),
                "minutes_to_expiry": opt_eval.get("minutes_to_expiry"),
                "time_of_day_bucket": opt_eval.get("time_of_day_bucket"),
                "greeks": opt_eval.get("greeks"),
                "delta": opt_eval.get("delta"),
                "gamma": opt_eval.get("gamma"),
                "gamma_risk": opt_eval.get("gamma_risk"),
                "theta": opt_eval.get("theta"),
                "theta_hourly": opt_eval.get("theta_hourly"),
                "vega": opt_eval.get("vega"),
                "expected_move": opt_eval.get("expected_move"),
                "expected_move_pct": opt_eval.get("expected_move_pct"),
                "preferred_strategy": opt_eval.get("preferred_strategy"),
                "multi_leg_structure": opt_eval.get("multi_leg_structure")
            })
        else:
            # Theoretical Option Fallback via OptionEngine
            opt_eval = OptionEngine.evaluate(
                spot_price=stock_price,
                spot_atr=atr if 'atr' in locals() else max(stock_price * 0.015, 0.50),
                direction_bias=quant_res.get("direction_bias", "NEUTRAL"),
                option_quote={"strike_price": round(stock_price), "bid_price": round(stock_price * 0.015, 2), "ask_price": round(stock_price * 0.017, 2)},
                contract_meta={"ticker": f"{sym}_ATM", "strike_price": round(stock_price), "expiration_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
            )
            opt_data.update({
                "strike": round(stock_price),
                "premium": opt_eval.get("premium"),
                "expiration": opt_eval.get("expiration"),
                "volume": 0,
                "open_interest": 0,
                "implied_volatility": opt_eval.get("iv_pct_str"),
                "bid_ask": f"${opt_eval.get('bid', 0.0):.2f} / ${opt_eval.get('ask', 0.0):.2f}",
                "contract_symbol": f"{sym}_ATM",
                "option_score": opt_eval.get("option_score"),
                "is_0dte": opt_eval.get("is_0dte"),
                "dte": opt_eval.get("dte"),
                "minutes_to_expiry": opt_eval.get("minutes_to_expiry"),
                "time_of_day_bucket": opt_eval.get("time_of_day_bucket"),
                "greeks": opt_eval.get("greeks"),
                "delta": opt_eval.get("delta"),
                "gamma": opt_eval.get("gamma"),
                "gamma_risk": opt_eval.get("gamma_risk"),
                "theta": opt_eval.get("theta"),
                "theta_hourly": opt_eval.get("theta_hourly"),
                "vega": opt_eval.get("vega"),
                "expected_move": opt_eval.get("expected_move"),
                "expected_move_pct": opt_eval.get("expected_move_pct"),
                "preferred_strategy": opt_eval.get("preferred_strategy"),
                "multi_leg_structure": opt_eval.get("multi_leg_structure")
            })
    except Exception as e:
        logger.error(f"Failed to fetch live options chain ({sym}): {e}")

    # Realistic Option Contract Risk & Tight Underlying Trigger Calculations
    stock_p = float(quant_res.get("price", 0.0))
    atr = float(quant_res.get("features", {}).get("atr", 1.0))
    if atr <= 0:
        atr = max(stock_p * 0.015, 0.50)

    opt_prem = float(opt_data.get("premium") or 1.50)
    is_0dte = bool(opt_data.get("is_0dte"))
    delta = 0.50
    try:
        raw_delta = opt_data.get("delta")
        if raw_delta is not None:
            delta = abs(float(raw_delta))
    except Exception:
        delta = 0.50
    delta = max(0.25, min(0.85, delta))

    # Realistic Option Contract Targets
    if is_0dte:
        opt_sl_pct = 20.0  # -20% stop loss on 0DTE (theta safety)
        opt_tp1_pct = 25.0 # +25% quick scalp TP1
        opt_tp2_pct = 60.0 # +60% extended runner TP2
    else:
        opt_sl_pct = 25.0  # -25% standard option stop loss
        opt_tp1_pct = 30.0 # +30% TP1
        opt_tp2_pct = 70.0 # +70% TP2

    opt_sl = round(max(0.05, opt_prem * (1.0 - opt_sl_pct / 100.0)), 2)
    opt_tp1 = round(opt_prem * (1.0 + opt_tp1_pct / 100.0), 2)
    opt_tp2 = round(opt_prem * (1.0 + opt_tp2_pct / 100.0), 2)

    # Tight Underlying Trigger Prices (calibrated to delta, max 0.35-0.50 ATR to prevent 0DTE/option wipeout!)
    sl_stock_delta = round(min(0.40 * atr, (opt_prem * (opt_sl_pct / 100.0)) / delta), 2)
    tp1_stock_delta = round(min(0.50 * atr, (opt_prem * (opt_tp1_pct / 100.0)) / delta), 2)
    tp2_stock_delta = round(min(1.10 * atr, (opt_prem * (opt_tp2_pct / 100.0)) / delta), 2)

    if c_type == "put":
        stock_sl = round(stock_p + sl_stock_delta, 2)
        stock_tp1 = round(stock_p - tp1_stock_delta, 2)
        stock_tp2 = round(stock_p - tp2_stock_delta, 2)
    else:
        stock_sl = round(stock_p - sl_stock_delta, 2)
        stock_tp1 = round(stock_p + tp1_stock_delta, 2)
        stock_tp2 = round(stock_p + tp2_stock_delta, 2)

    dynamic_zones = {
        "is_0dte": is_0dte,
        "entry_premium": opt_prem,
        "opt_tp1": opt_tp1,
        "opt_tp1_pct": opt_tp1_pct,
        "opt_tp2": opt_tp2,
        "opt_tp2_pct": opt_tp2_pct,
        "opt_sl": opt_sl,
        "opt_sl_pct": opt_sl_pct,
        "underlying_entry": round(stock_p, 2),
        "underlying_tp1": stock_tp1,
        "underlying_tp2": stock_tp2,
        "underlying_sl": stock_sl,
        "sl_atr_distance": round(sl_stock_delta / max(atr, 0.01), 2)
    }

    opt_data["dynamic_exit_zones"] = dynamic_zones
    quant_res["risk_metrics"] = {
        "entry_price": stock_p,
        "stop_loss": stock_sl,
        "take_profit": stock_tp1,
        "tp1": stock_tp1,
        "tp2": stock_tp2,
        "reward_risk_ratio": round(tp1_stock_delta / max(sl_stock_delta, 0.01), 2)
    }

    # Re-evaluate candidate trade with actual option contract pricing and structure
    from engine.trade_engine import global_trade_engine
    from engine.market_hours import MarketSchedule
    is_mkt_open, session_name, mkt_reason = MarketSchedule.is_market_open(sym)

    mls = opt_data.get("multi_leg_structure") or {}
    eff_strat = mls.get("strategy") or quant_res.get("strategy", "QUANT_SETUP")

    opt_candidate_payload = {
        "symbol": opt_data.get("contract_symbol") or f"{sym}_{opt_data.get('strike')}_{opt_data.get('direction')}",
        "underlying": sym,
        "instrument": "OPTION",
        "decision": quant_res.get("strategy_side") or quant_res.get("decision", "NO_TRADE"),
        "price": opt_prem,
        "stock_price": stock_p,
        "premium": opt_prem,
        "score": quant_res.get("setup_score", 0.0),
        "strategy": eff_strat if eff_strat not in ["NONE", "NO_STRATEGY_MATCH", "NO_SETUP"] else quant_res.get("strategy", "QUANT_SETUP"),
        "direction_evaluation": quant_res.get("direction_evaluation"),
        "confidence_evaluation": quant_res.get("confidence_evaluation"),
        "confluence_evaluation": quant_res.get("confluence_evaluation"),
        "htf_pattern": quant_res.get("htf_pattern"),
        "risk_metrics": {
            "entry_price": opt_prem,
            "stop_loss": opt_sl,
            "take_profit": opt_tp1,
            "opt_sl": opt_sl,
            "opt_tp1": opt_tp1,
            "opt_tp2": opt_tp2,
            "stock_entry": stock_p,
            "stock_sl": stock_sl,
            "stock_tp1": stock_tp1,
            "reward_risk_ratio": round(tp1_stock_delta / max(sl_stock_delta, 0.01), 2)
        },
        "entry_valid": quant_res.get("entry_valid", False),
        "option_data": opt_data,
        "option_evaluation": opt_eval if 'opt_eval' in locals() else None,
        "is_vetoed": quant_res.get("is_vetoed", False),
        "veto_reasons": quant_res.get("veto_reasons", [])
    }

    opt_trade_eval = global_trade_engine.evaluate_candidate(
        candidate_signal=opt_candidate_payload,
        is_market_open=is_mkt_open,
        market_reason=mkt_reason
    )
    quant_res["trade_evaluation"] = opt_trade_eval
    quant_res["trade_verdict"] = opt_trade_eval["verdict"]
    quant_res["decision_explanation"] = opt_trade_eval["decision_explanation"]
    if 'opt_eval' in locals() and opt_eval:
        quant_res["option_evaluation"] = opt_eval

    return {
        "quant_synthesis": quant_res,
        "option_contract": opt_data,
        "multi_leg_structure": opt_data.get("multi_leg_structure"),
        "confluence_evaluation": quant_res.get("confluence_evaluation"),
        "htf_pattern": quant_res.get("htf_pattern"),
        "trades": trades,
        "trade_history": trades
    }

# ==========================================
# 3.1 TRADE HISTORY ENDPOINTS
# ==========================================
@app.get("/api/v1/trades")
async def get_trades(symbol: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    """Retrieve real executed trades from Trade History enriched with market time info and live unrealized PnL."""
    trades = await db_manager.get_trades(symbol=symbol, status=status, limit=limit)
    
    from data.polygon_client import PolygonOptionsClient
    poc = PolygonOptionsClient()

    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    
    async def _enrich_open_trade(trade_item):
        contract_sym = trade_item.get("contract_symbol") or trade_item.get("symbol")
        entry_p = float(trade_item.get("entry_price") or 0.0)
        qty = float(trade_item.get("quantity") or 1.0)
        multiplier = 100.0 if (trade_item.get("option_type") in ["CALL", "PUT"] or "C0" in str(contract_sym) or "P0" in str(contract_sym)) else 1.0
        side = str(trade_item.get("side", "LONG")).upper()
        mark_p = entry_p

        if contract_sym:
            try:
                quote = await poc.get_option_realtime_quote(contract_sym)
                if quote:
                    bid = quote.get("bid_price", 0.0)
                    ask = quote.get("ask_price", 0.0)
                    last = quote.get("last_trade_price", 0.0)
                    if last > 0:
                        mark_p = last
                    elif bid > 0 and ask > 0:
                        mark_p = (bid + ask) / 2.0
                    elif bid > 0:
                        mark_p = bid
            except Exception:
                pass

        if side in ["LONG", "BUY"]:
            u_pnl = (mark_p - entry_p) * qty * multiplier
            u_pct = ((mark_p - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0
        else:
            u_pnl = (entry_p - mark_p) * qty * multiplier
            u_pct = ((entry_p - mark_p) / entry_p * 100.0) if entry_p > 0 else 0.0

        trade_item["current_price"] = round(mark_p, 2)
        trade_item["unrealized_pnl"] = round(u_pnl, 2)
        trade_item["unrealized_pnl_pct"] = round(u_pct, 2)
        trade_item["pnl"] = round(u_pnl, 2)
        trade_item["pnl_pct"] = round(u_pct, 2)
        trade_item["is_open"] = True

    if open_trades:
        await asyncio.gather(*[_enrich_open_trade(ot) for ot in open_trades], return_exceptions=True)

    for t in trades:
        t_info = MarketSchedule.format_position_timestamp(t.get("created_at"), t.get("closed_at"))
        t["time_session"] = t_info.get("time_session")
        t["time_gmt3"] = t_info.get("time_gmt3")
        t["time_gmt2"] = t_info.get("time_gmt2")
        t["time_et"] = t_info.get("time_gmt2")  # backwards compatibility alias
        t["time_utc"] = t_info.get("time_utc")
        t["date_str"] = t_info.get("date")
        t["duration_str"] = t_info.get("duration_str")
        t["duration_seconds"] = t_info.get("duration_seconds")
        t["time_display"] = t_info.get("full_display")

        d_str = t_info.get("date") or ""
        s_str = t_info.get("time_session") or ""
        t["opened_time_display"] = f"{d_str} {s_str}".strip() if (d_str and d_str != "—") else s_str

        if t.get("closed_at"):
            c_info = MarketSchedule.format_position_timestamp(t.get("closed_at"))
            cd_str = c_info.get("date") or ""
            cs_str = c_info.get("time_session") or ""
            t["closed_time_display"] = f"{cd_str} {cs_str}".strip() if (cd_str and cd_str != "—") else cs_str
        else:
            t["closed_time_display"] = "ACTIVE (OPEN)"
    return trades

@app.post("/api/v1/trades/open")
async def open_trade_endpoint(req: OpenTradeRequest, allow_outside_hours: bool = False):
    """Open a new position and record into Trade History with market hours gating."""
    target_sym = req.underlying or req.symbol
    is_open, session, market_reason = MarketSchedule.is_market_open(target_sym)

    can_bypass = req.allow_outside_hours or allow_outside_hours
    if not is_open and not can_bypass:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot open position: {market_reason} Allowed trading session: {MarketSchedule.OPEN_TIME_STR} - {MarketSchedule.CLOSE_TIME_STR} {MarketSchedule.TIMEZONE_NAME}."
        )

    trade_data = req.model_dump()
    trade_data.pop("allow_outside_hours", None)
    trade = await db_manager.create_trade(trade_data)
    if not trade:
        raise HTTPException(status_code=500, detail="Failed to open and record trade position.")

    try:
        from web.routes.ws import broadcast_trade_update
        await broadcast_trade_update(trade)
    except Exception as e:
        logger.warning(f"Failed to broadcast trade open: {e}")

    return {"success": True, "trade": trade}

@app.post("/api/v1/trades/close-all")
async def close_all_trades_endpoint(reason: str = "16:30 End-of-Session Forced Close"):
    """Force close all open positions immediately and return closed list."""
    closed = await db_manager.close_all_open_positions(exit_reason=reason)
    try:
        from web.routes.ws import broadcast_trade_update
        for ct in closed:
            await broadcast_trade_update(ct)
    except Exception as e:
        logger.warning(f"Failed to broadcast close-all: {e}")
    return {"success": True, "closed_count": len(closed), "trades": closed}

@app.post("/api/v1/trades/{trade_id}/close")
async def close_trade_endpoint(trade_id: str, req: CloseTradeRequest):
    """Close an active trade position and calculate realized P&L."""
    closed = await db_manager.close_trade(
        trade_id=trade_id,
        exit_price=req.exit_price,
        exit_reason=req.exit_reason or "Exit signal confirmed"
    )
    if not closed:
        raise HTTPException(status_code=404, detail=f"Open trade '{trade_id}' not found or already closed.")

    try:
        from web.routes.ws import broadcast_trade_update
        await broadcast_trade_update(closed)
    except Exception as e:
        logger.warning(f"Failed to broadcast trade close: {e}")

    return {"success": True, "trade": closed}

@app.post("/api/v1/watchlist")
async def add_watchlist_symbol(req: SymbolRequest):
    sym = sanitize_ticker(req.symbol)
    success = await db_manager.add_symbol(sym)
    return {"success": success, "symbol": sym}

@app.delete("/api/v1/watchlist/{symbol}")
async def remove_watchlist_symbol(symbol: str):
    sym = sanitize_ticker(symbol)
    success = await db_manager.remove_symbol(sym)
    return {"success": success, "symbol": sym}

# Helper to extract symbol and fetch live market metrics for AI
async def _get_live_market_context(msg: str) -> str:
    try:
        import yfinance as yf
        # Check watchlist first
        wl = await db_manager.get_watchlist()
        wl_symbols = {w.symbol.upper() for w in wl}
        
        known_tickers = {
            "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "TSLA",
            "AMD", "META", "AMZN", "GOOGL", "GOOG", "COIN", "PLTR", "BABA",
            "INTC", "DIS", "BA", "XOM", "JPM", "V", "MA", "SOXL", "SMCI"
        }
        known_tickers.update(wl_symbols)

        # Look for symbols
        words = re.findall(r'\b([A-Za-z0-9]{2,10}(?:\.IS)?)\b', msg)
        target_sym = None
        for w in words:
            up = w.upper()
            if up.endswith(".IS") or up in known_tickers:
                target_sym = up
                break

        if not target_sym:
            return ""

        def _fetch_candles(sym: str):
            t = yf.Ticker(sym)
            df = t.history(period="3mo", interval="1d")
            if df.empty or len(df) < 15:
                return None
            closes = [float(x) for x in df["Close"].tolist()]
            highs = [float(x) for x in df["High"].tolist()]
            lows = [float(x) for x in df["Low"].tolist()]
            volumes = [float(x) if (float(x) > 0) else 1.0 for x in df["Volume"].tolist()]
            curr = closes[-1]
            prev = closes[-2] if len(closes) >= 2 else curr
            chg = ((curr - prev) / prev) * 100 if prev else 0.0
            ema9 = FeatureEngine.calculate_ema(closes, 9)
            ema21 = FeatureEngine.calculate_ema(closes, 21)
            rsi = FeatureEngine.calculate_rsi(closes, 14)
            atr = FeatureEngine.calculate_atr(highs, lows, closes, 14)
            spread = ema9 - ema21
            trend = "BULLISH (Price > 9 EMA > 21 EMA)" if curr > ema9 > ema21 else (
                    "BEARISH (Price < 9 EMA < 21 EMA)" if curr < ema9 < ema21 else "MIXED / SIDEWAYS"
            )
            is_bist = sym.endswith(".IS")
            cur_sym = " TL" if is_bist else "$"
            prefix = "" if is_bist else "$"

            # Deep Quant Synthesis
            quant_lines = []
            if len(closes) >= 35:
                try:
                    q = QuantEngine.analyze_ticker(sym, highs, lows, closes, volumes)
                    conf = q.get("confluence_evaluation", {})
                    htf = q.get("htf_pattern", {})
                    quant_lines.append(f"- Decision / Verdict: {q.get('decision')} | Strategy: {q.get('strategy')}")
                    quant_lines.append(f"- Confluence Score: {conf.get('net_confluence', 0):.0f}% (Positive: {conf.get('confluence_score', 0):.0f}, Contradiction Penalty: {conf.get('contradiction_penalty', 0):.0f})")
                    if htf.get("has_htf_setup"):
                        quant_lines.append(f"- HTF Structural Pattern: {htf.get('primary_htf_pattern')} (Score: {htf.get('htf_score', 0):.0f})")
                    if q.get("reasons"):
                        quant_lines.append(f"- Key Confirmations: {'; '.join(q.get('reasons')[:3])}")
                    if q.get("warnings"):
                        quant_lines.append(f"- Risk Warnings: {'; '.join(q.get('warnings')[:2])}")
                    # Recommended Option Structure & 0DTE Expected Move
                    em = round(curr * 0.30 * 0.04, 2)  # ~1.2% daily expected move baseline
                    quant_lines.append(f"- 0DTE Expected Move: ±{prefix}{em:.2f}{cur_sym} (Range: {prefix}{curr-em:.2f} - {prefix}{curr+em:.2f})")
                    opt_bias = "BULL_CALL_SPREAD" if "BULL" in q.get("direction_bias", "") else ("BEAR_PUT_SPREAD" if "BEAR" in q.get("direction_bias", "") else "IRON_CONDOR")
                    quant_lines.append(f"- Recommended Option Structure: {opt_bias} (Defined-Risk)")
                except Exception as q_err:
                    logger.debug(f"Quant summary error: {q_err}")

            quant_block = "\n" + "\n".join(quant_lines) if quant_lines else ""

            return (
                f"\n[LIVE SYSTEM MARKET METRICS ({sym} - Date: {df.index[-1].strftime('%Y-%m-%d')} - Daily Bars)]:\n"
                f"- Symbol: {sym}\n"
                f"- Last Price: {prefix}{curr:.2f}{cur_sym} ({chg:+.2f}%)\n"
                f"- 9 EMA (Short-term Momentum): {prefix}{ema9:.2f}{cur_sym}\n"
                f"- 21 EMA (Trend Support): {prefix}{ema21:.2f}{cur_sym}\n"
                f"- 9/21 EMA Spread: {prefix}{spread:+.2f}{cur_sym}\n"
                f"- 14-Day RSI: {rsi:.1f}\n"
                f"- 14-Day ATR: {prefix}{atr:.2f}{cur_sym}\n"
                f"- Market Regime: {trend}\n"
                f"- Critical Levels: Target (+1 ATR) = {prefix}{curr+atr:.2f}, Support 1 (9 EMA) = {prefix}{ema9:.2f}, Support 2 (21 EMA) = {prefix}{ema21:.2f}"
                f"{quant_block}\n"
            )

        context_str = await asyncio.to_thread(_fetch_candles, target_sym)
        return context_str or ""
    except Exception as e:
        logger.warning(f"Market context error: {e}")
        return ""

@app.post("/api/v1/ai/chat")
async def ai_chat(req: ChatRequest):
    msg = req.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(msg) > 2500:
        raise HTTPException(status_code=400, detail="Message is too long (maximum 2500 characters).")

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key or "your_" in api_key.lower():
        return {
            "response": "⚠️ GEMINI_API_KEY is not configured or invalid! Please configure a valid Google Gemini API key in your .env file."
        }
    try:
        genai.configure(api_key=api_key)
        system_instruction = (
            "You are the execution-focused, quantitative Trading Assistant for Metobot AI Quantitative Terminal.\n"
            "STRICT DIRECTIVES:\n"
            "1. NEVER give generic textbook definitions, encyclopedic lectures, or filler explanations. The user expects concise, institutional-grade responses.\n"
            "2. When the user asks for levels, calculations, values, or analysis, provide EXACT NUMBERS, FORMULAS, and PRECISE MARKET LEVELS immediately.\n"
            "   - Reference the provided [LIVE SYSTEM MARKET METRICS] block to deliver accurate market figures.\n"
            "3. When the user asks for code, indicators, or strategies, directly provide CLEAN, PRODUCTION-READY CODE (TradingView Pine Script v5 or Python) without preamble.\n"
            "4. Maintain a Bloomberg or TradingView Quant Terminal professional style: concise, data-driven, and actionable."
        )

        market_context = await _get_live_market_context(msg)
        full_prompt = (market_context + "\n" + msg) if market_context else msg

        # Smart fallback list to prevent 429 quota exhaustion
        configured_model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
        candidates = [configured_model, "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
        unique_candidates = [m for i, m in enumerate(candidates) if m and m not in candidates[:i]]

        last_error = None
        for m_name in unique_candidates:
            try:
                model = genai.GenerativeModel(
                    model_name=m_name,
                    system_instruction=system_instruction
                )
                resp = model.generate_content(full_prompt)
                return {"response": resp.text}
            except Exception as model_err:
                last_error = model_err
                err_text = str(model_err)
                if "429" in err_text or "quota" in err_text.lower() or "404" in err_text:
                    logger.warning(f"⚠️ Gemini model {m_name} rate limited or unavailable, automatically switching to fallback model...")
                    continue
                else:
                    raise model_err

        if last_error:
            raise last_error
        return {"response": "Unable to receive a response from the AI model at this moment. Please try again shortly."}
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return {"response": f"AI Assistant Error: {str(e)}. Please check your API key configuration and network connectivity."}

@app.get("/api/v1/live-signals")
async def get_live_signals():
    """Return recent live signals generated by Bot 2."""
    return await db_manager.get_recent_live_signals(limit=25)

@app.get("/api/v1/model-weights")
async def get_model_weights():
    """Return latest optimized ML model weights from Bot 1 Trainer."""
    weights = await db_manager.get_latest_model_weights()
    return {"weights": weights or {}}

# ==========================================
# 4. OVERHAUL EXTENSIONS: PORTFOLIO, SETTINGS & ANALYTICS
# ==========================================
class ResetPortfolioRequest(BaseModel):
    initial_balance: Optional[float] = Field(None, gt=0.0)

class SettingsUpdateRequest(BaseModel):
    starting_balance: Optional[float] = Field(None, gt=0)
    risk_per_trade_pct: Optional[float] = Field(None, gt=0, le=100)
    max_capital_allocation_pct: Optional[float] = Field(None, gt=0, le=100)
    daily_loss_limit_pct: Optional[float] = Field(None, gt=0, le=100)
    min_model_confidence: Optional[float] = Field(None, ge=0, le=100)
    min_directional_edge: Optional[float] = Field(None, ge=0, le=100)
    min_bullish_score: Optional[float] = Field(None, ge=0, le=100)
    min_bearish_score: Optional[float] = Field(None, ge=0, le=100)
    max_open_positions_total: Optional[int] = Field(None, ge=1, le=50)
    cooldown_minutes: Optional[int] = Field(None, ge=0, le=240)
    tp1_multiplier: Optional[float] = Field(None, gt=0)
    tp2_multiplier: Optional[float] = Field(None, gt=0)
    hard_sl_multiplier: Optional[float] = Field(None, gt=0)
    breakeven_buffer_pct: Optional[float] = Field(None, ge=0)
    zero_dte_cutoff_hour_et: Optional[int] = Field(None, ge=0, le=23)
    zero_dte_cutoff_minute_et: Optional[int] = Field(None, ge=0, le=59)

@app.get("/api/v1/portfolio")
async def get_portfolio_status():
    """Return paper portfolio metrics, balances, exposure, daily P&L and risk gates."""
    db_state = await db_manager.get_portfolio_state()
    if db_state and isinstance(db_state, dict):
        global_portfolio.available_cash = db_state.get("available_cash", global_portfolio.available_cash)
        global_portfolio.starting_balance = db_state.get("starting_balance", global_portfolio.starting_balance)
        if global_portfolio.realized_pnl == 0.0:
            global_portfolio.realized_pnl = db_state.get("realized_pnl", 0.0)
        if global_portfolio.daily_pnl == 0.0:
            global_portfolio.daily_pnl = db_state.get("daily_pnl", 0.0)

    # Keep risk parameters aligned with global_config
    r = global_config.risk_per_trade_pct
    global_portfolio.risk_per_trade_pct = r if r <= 1.0 else r / 100.0
    m = global_config.max_portfolio_exposure_pct
    global_portfolio.max_exposure_pct = m if m <= 1.0 else m / 100.0
    d = global_config.daily_loss_limit_pct
    global_portfolio.daily_loss_limit_pct = d if d <= 1.0 else d / 100.0

    open_trades = await db_manager.get_open_trades()
    global_portfolio._open_positions = {t["trade_id"]: t for t in open_trades if "trade_id" in t}
    return global_portfolio.get_portfolio_summary()

@app.post("/api/v1/portfolio/reset")
async def reset_portfolio_endpoint(req: Optional[ResetPortfolioRequest] = None):
    """Reset the paper portfolio back to initial state ($100k or custom)."""
    bal = req.initial_balance if (req and req.initial_balance) else global_config.paper_starting_balance
    global_config.paper_starting_balance = float(bal)
    global_portfolio.reset(starting_balance=float(bal))
    await db_manager.reset_portfolio_state(starting_balance=float(bal))
    summary = global_portfolio.get_portfolio_summary()
    try:
        await broadcast_portfolio_update(summary)
    except Exception as e:
        logger.warning(f"Failed to broadcast portfolio reset: {e}")
    return {"success": True, "portfolio": summary}

@app.get("/api/v1/rejected-signals")
async def get_rejected_signals(limit: int = 50):
    """Return audit log of algorithmic signals vetoed by Trade Engine."""
    return await db_manager.get_recent_rejected_signals(limit=limit)

@app.get("/api/v1/performance-analytics")
async def get_performance_analytics():
    """Return comprehensive multi-dimensional trading performance analytics."""
    return await db_manager.get_performance_analytics()

@app.get("/api/v1/settings")
async def get_settings():
    """Return current trading and engine configuration settings normalized for UI display."""
    cfg = asdict(global_config)
    cfg["starting_balance"] = round(float(global_config.paper_starting_balance), 2)
    cfg["min_model_confidence"] = global_config.minimum_confidence
    cfg["min_directional_edge"] = global_config.min_direction_edge
    cfg["tp1_multiplier"] = global_config.tp1_atr_multiple
    cfg["tp2_multiplier"] = global_config.tp2_atr_multiple
    cfg["hard_sl_multiplier"] = global_config.sl_atr_multiple
    cfg["breakeven_buffer_pct"] = getattr(global_config, 'profit_protection_buffer_atr', 0.25)
    cfg["cooldown_minutes"] = getattr(global_config, 'cooldown_minutes', 15)

    # Return percentages (0 - 100 scale) for UI form inputs
    r_pct = global_config.risk_per_trade_pct * 100.0 if global_config.risk_per_trade_pct <= 1.0 else global_config.risk_per_trade_pct
    cfg["risk_per_trade_pct"] = round(r_pct, 2)

    m_pct = global_config.max_portfolio_exposure_pct * 100.0 if global_config.max_portfolio_exposure_pct <= 1.0 else global_config.max_portfolio_exposure_pct
    cfg["max_capital_allocation_pct"] = round(m_pct, 2)
    cfg["max_portfolio_exposure_pct"] = round(m_pct, 2)

    d_pct = global_config.daily_loss_limit_pct * 100.0 if global_config.daily_loss_limit_pct <= 1.0 else global_config.daily_loss_limit_pct
    cfg["daily_loss_limit_pct"] = round(d_pct, 2)

    return cfg

@app.post("/api/v1/settings")
async def update_settings(req: Dict[str, Any]):
    """Update trading and engine configuration settings and synchronize portfolio & database."""
    # Process Paper Portfolio specific parameters
    if "starting_balance" in req and req["starting_balance"] is not None:
        new_start = float(req["starting_balance"])
        global_config.paper_starting_balance = new_start
        global_portfolio.starting_balance = new_start
        global_portfolio.initial_balance = new_start
        await db_manager.update_starting_balance(new_start, adjust_cash=True)
        db_state = await db_manager.get_portfolio_state()
        if db_state and isinstance(db_state, dict):
            global_portfolio.available_cash = db_state.get("available_cash", global_portfolio.available_cash)

    if "risk_per_trade_pct" in req and req["risk_per_trade_pct"] is not None:
        raw_r = float(req["risk_per_trade_pct"])
        r_ratio = raw_r / 100.0 if raw_r > 1.0 else raw_r
        global_config.risk_per_trade_pct = r_ratio
        global_portfolio.risk_per_trade_pct = r_ratio

    raw_alloc = req.get("max_capital_allocation_pct") if "max_capital_allocation_pct" in req else req.get("max_portfolio_exposure_pct")
    if raw_alloc is not None:
        raw_m = float(raw_alloc)
        m_ratio = raw_m / 100.0 if raw_m > 1.0 else raw_m
        global_config.max_portfolio_exposure_pct = m_ratio
        global_portfolio.max_exposure_pct = m_ratio

    if "daily_loss_limit_pct" in req and req["daily_loss_limit_pct"] is not None:
        raw_d = float(req["daily_loss_limit_pct"])
        d_ratio = raw_d / 100.0 if raw_d > 1.0 else raw_d
        global_config.daily_loss_limit_pct = d_ratio
        global_portfolio.daily_loss_limit_pct = d_ratio

    if "cooldown_minutes" in req and req["cooldown_minutes"] is not None:
        global_config.cooldown_minutes = int(req["cooldown_minutes"])

    # Process other Trade Engine & Risk parameters
    if "min_model_confidence" in req and req["min_model_confidence"] is not None:
        global_config.minimum_confidence = float(req["min_model_confidence"])
    if "min_directional_edge" in req and req["min_directional_edge"] is not None:
        global_config.min_direction_edge = float(req["min_directional_edge"])
    if "tp1_multiplier" in req and req["tp1_multiplier"] is not None:
        global_config.tp1_atr_multiple = float(req["tp1_multiplier"])
    if "tp2_multiplier" in req and req["tp2_multiplier"] is not None:
        global_config.tp2_atr_multiple = float(req["tp2_multiplier"])
    if "hard_sl_multiplier" in req and req["hard_sl_multiplier"] is not None:
        global_config.sl_atr_multiple = float(req["hard_sl_multiplier"])
    if "breakeven_buffer_pct" in req and req["breakeven_buffer_pct"] is not None:
        global_config.profit_protection_buffer_atr = float(req["breakeven_buffer_pct"])

    # Generic attribute update fallback for remaining keys
    for k, v in req.items():
        if k not in [
            "starting_balance", "risk_per_trade_pct", "max_capital_allocation_pct",
            "max_portfolio_exposure_pct", "daily_loss_limit_pct", "cooldown_minutes",
            "min_model_confidence", "min_directional_edge", "tp1_multiplier",
            "tp2_multiplier", "hard_sl_multiplier", "breakeven_buffer_pct"
        ] and hasattr(global_config, k) and v is not None:
            try:
                cur_val = getattr(global_config, k)
                setattr(global_config, k, type(cur_val)(v))
            except Exception:
                pass

    # Refresh open trades into portfolio and produce updated summary
    open_trades = await db_manager.get_open_trades()
    global_portfolio._open_positions = {t["trade_id"]: t for t in open_trades if "trade_id" in t}
    summary = global_portfolio.get_portfolio_summary()

    try:
        await broadcast_portfolio_update(summary)
    except Exception as e:
        logger.warning(f"Failed to broadcast portfolio update after settings save: {e}")

    logger.info(f"Updated global trading configuration & synchronized portfolio: {req}")
    return {"success": True, "updated": req, "portfolio": summary}

@app.get("/api/v1/stock-analysis/{symbol}")
async def analyze_stock(symbol: str):
    sym = sanitize_ticker(symbol)
    
    def _fetch_hist():
        import yfinance as yf
        import pandas as pd
        t = yf.Ticker(sym)
        df = t.history(period="6mo", interval="1d")
        if df.empty:
            return [], [], [], [], []
        dates_list = [str(idx.strftime("%Y-%m-%d")) for idx in df.index]
        closes_list = [float(x) for x in df["Close"].tolist() if not pd.isna(x)]
        highs_list = [float(x) for x in df["High"].tolist() if not pd.isna(x)]
        lows_list = [float(x) for x in df["Low"].tolist() if not pd.isna(x)]
        volumes_list = [float(x) if (not pd.isna(x) and float(x) > 0) else 1.0 for x in df["Volume"].tolist()]
        return dates_list, closes_list, highs_list, lows_list, volumes_list

    try:
        dates, closes, highs, lows, volumes = await asyncio.to_thread(_fetch_hist)
    except Exception as e:
        return {"error": f"Failed to fetch price history for ({sym}): {str(e)}"}

    if len(closes) < 35:
        return {"error": f"Insufficient historical price data found for {sym}."}

    quant_res = QuantEngine.analyze_ticker(sym, highs, lows, closes, volumes)
    trades = await db_manager.get_trades(symbol=sym, limit=20)
    
    return {
        "symbol": sym,
        "price": quant_res.get("price", closes[-1]),
        "quant_synthesis": quant_res,
        "stock_evaluation": quant_res.get("stock_evaluation", {}),
        "bullish_score": quant_res.get("bullish_score", 0.0),
        "bearish_score": quant_res.get("bearish_score", 0.0),
        "direction_edge": quant_res.get("direction_edge", 0.0),
        "directional_bias": quant_res.get("directional_bias", "NEUTRAL"),
        "regime": quant_res.get("regime", "UNCLEAR"),
        "strategy": quant_res.get("strategy", "NONE"),
        "confidence": quant_res.get("confidence_evaluation", {}),
        "trade_evaluation": quant_res.get("trade_evaluation", {}),
        "trades": trades
    }

@app.get("/api/v1/chart-data/{symbol}")
async def get_chart_data(symbol: str, interval: str = "1d", limit: int = 150):
    """Return historical candlestick series + EMA 9, EMA 21, VWAP, and active trade levels."""
    sym = sanitize_ticker(symbol)
    
    def _fetch_bars():
        import yfinance as yf
        import pandas as pd
        t = yf.Ticker(sym)
        intv = interval.lower()
        if intv == "1m":
            df = t.history(period="5d", interval="1m")
        elif intv in ["5m", "15m"]:
            df = t.history(period="30d", interval=intv)
        elif intv == "1h":
            df = t.history(period="60d", interval="1h")
        elif intv == "4h":
            df = t.history(period="60d", interval="1h")
            if not df.empty:
                df = df.resample("4h").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum"
                }).dropna()
        else: # 1d
            intv = "1d"
            df = t.history(period="1y", interval="1d")

        if df.empty:
            return [], [], [], []
        
        raw_bars = []
        for idx, row in df.iterrows():
            c = float(row["Close"]) if not pd.isna(row["Close"]) else 0.0
            h = float(row["High"]) if not pd.isna(row["High"]) else c
            l = float(row["Low"]) if not pd.isna(row["Low"]) else c
            o = float(row["Open"]) if not pd.isna(row["Open"]) else c
            v = float(row["Volume"]) if not pd.isna(row["Volume"]) else 1.0
            if c > 0:
                t_val = int(idx.timestamp())
                raw_bars.append({
                    "time": t_val,
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": v
                })

        seen_times = set()
        clean_bars = []
        for b in raw_bars:
            if b["time"] not in seen_times:
                seen_times.add(b["time"])
                clean_bars.append(b)
        clean_bars.sort(key=lambda x: x["time"])

        if len(clean_bars) > limit:
            bars = clean_bars[-limit:]
        else:
            bars = clean_bars

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["volume"] for b in bars]
            
        ema9_series = []
        ema21_series = []
        vwap_series = []
        
        cum_pv = 0.0
        cum_vol = 0.0
        
        k9 = 2.0 / (9 + 1)
        k21 = 2.0 / (21 + 1)
        curr_ema9 = closes[0] if closes else 0.0
        curr_ema21 = closes[0] if closes else 0.0
        
        for i, b in enumerate(bars):
            price = b["close"]
            typ_price = (b["high"] + b["low"] + b["close"]) / 3.0
            vol = b["volume"]
            
            if i == 0:
                curr_ema9 = price
                curr_ema21 = price
            else:
                curr_ema9 = (price * k9) + (curr_ema9 * (1 - k9))
                curr_ema21 = (price * k21) + (curr_ema21 * (1 - k21))
                
            cum_pv += typ_price * vol
            cum_vol += vol
            v_val = (cum_pv / cum_vol) if cum_vol > 0 else price
            
            ema9_series.append({"time": b["time"], "value": round(curr_ema9, 2)})
            ema21_series.append({"time": b["time"], "value": round(curr_ema21, 2)})
            vwap_series.append({"time": b["time"], "value": round(v_val, 2)})
            
        return bars, ema9_series, ema21_series, vwap_series

    try:
        bars, ema9, ema21, vwap = await asyncio.to_thread(_fetch_bars)
    except Exception as e:
        return {"error": f"Failed to fetch chart data for ({sym}): {str(e)}", "bars": []}

    open_trades = await db_manager.get_open_trades()
    active_trade = next((t for t in open_trades if (t.get("underlying") == sym or t.get("symbol") == sym)), None)
    
    trade_lines = None
    if active_trade:
        trade_lines = {
            "trade_id": active_trade.get("trade_id"),
            "side": active_trade.get("side"),
            "entry_price": active_trade.get("entry_price"),
            "take_profit": active_trade.get("take_profit"),
            "tp1": active_trade.get("tp1"),
            "tp2": active_trade.get("tp2"),
            "stop_loss": active_trade.get("stop_loss"),
            "trailing_stop": active_trade.get("trailing_stop"),
            "quantity": active_trade.get("quantity")
        }

    return {
        "symbol": sym,
        "bars": bars,
        "ema9": ema9,
        "ema21": ema21,
        "vwap": vwap,
        "active_trade": trade_lines
    }

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run("web.api:app", host=host, port=port, reload=True)