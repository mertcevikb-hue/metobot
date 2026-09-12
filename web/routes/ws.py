from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
import asyncio
import json
import re
from data.polygon_client import PolygonOptionsClient

router = APIRouter()
active_connections: list = []
polygon_client = PolygonOptionsClient()

# ---------------------------------------------------------
# Bot 2 Signal Broadcast
# ---------------------------------------------------------
@router.websocket("/live")
async def websocket_endpoint(websocket: WebSocket):
    """Stream live scores and executed trade updates to browser clients (Dashboard)."""
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            # Keep-alive ping/pong listener
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info("A WebSocket client disconnected.")
    except Exception as e:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.warning(f"WebSocket connection terminated: {e}")


async def broadcast_signal(signal_data: dict):
    """Broadcast newly generated algorithmic signal to all connected clients."""
    if not active_connections:
        return
    message = json.dumps(signal_data)
    dead_connections = []
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception:
            dead_connections.append(connection)

    for dead in dead_connections:
        if dead in active_connections:
            active_connections.remove(dead)


async def broadcast_trade_update(trade_data: dict):
    """Broadcast confirmed trade executions (open/close) to all connected clients in real-time."""
    if not active_connections:
        return
    payload = {
        "type": "trade_update",
        "trade": trade_data
    }
    message = json.dumps(payload)
    dead_connections = []
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception:
            dead_connections.append(connection)

    for dead in dead_connections:
        if dead in active_connections:
            active_connections.remove(dead)


async def broadcast_portfolio_update(portfolio_data: dict):
    """Broadcast real-time portfolio balance and risk updates to all connected clients."""
    if not active_connections:
        return
    payload = {
        "type": "portfolio_update",
        "portfolio": portfolio_data
    }
    message = json.dumps(payload)
    dead_connections = []
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception:
            dead_connections.append(connection)

    for dead in dead_connections:
        if dead in active_connections:
            active_connections.remove(dead)


async def broadcast_rejected_signal(rejected_data: dict):
    """Broadcast rejected trade evaluation (VETO) to all connected clients."""
    if not active_connections:
        return
    payload = {
        "type": "rejected_signal",
        "signal": rejected_data
    }
    message = json.dumps(payload)
    dead_connections = []
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception:
            dead_connections.append(connection)

    for dead in dead_connections:
        if dead in active_connections:
            active_connections.remove(dead)


# ---------------------------------------------------------
# Realtime Options Stream
# ---------------------------------------------------------
@router.websocket("/ws/options/{option_ticker}")
async def live_options_feed(websocket: WebSocket, option_ticker: str):
    """Provide real-time option chain order book stream to the options page."""
    # Sanitization & Injection Protection
    clean_ticker = re.sub(r"[^A-Za-z0-9:\-_]", "", option_ticker)
    if not clean_ticker:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        while True:
            quote = await polygon_client.get_option_realtime_quote(clean_ticker)
            if quote:
                await websocket.send_json({
                    "ticker": clean_ticker,
                    "bid": quote.get("bid_price", 0.0),
                    "ask": quote.get("ask_price", 0.0),
                    "last": quote.get("last_trade_price", 0.0),
                    "iv": quote.get("implied_volatility", 0.0)
                })
            await asyncio.sleep(2)
    except (WebSocketDisconnect, asyncio.CancelledError):
        logger.info(f"Client disconnected from options stream ({clean_ticker}).")
    except Exception as e:
        logger.warning(f"Error in options WebSocket stream ({clean_ticker}): {e}")