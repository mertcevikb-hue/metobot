"""
Signal Dispatcher - Routes trading signals to execution and broadcasting.

Responsibilities:
1. Format signals for different channels
2. Broadcast to WebSocket subscribers (dashboard)
3. Send to external notifications (Telegram, Discord)
4. Execute on broker or log for paper trading
5. Audit all signal dispatches
6. Enrich signals with real-time options data and liquidity gating
"""

import asyncio
import json
import os
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, timezone
from loguru import logger
import aiohttp
from dotenv import load_dotenv

load_dotenv()

from risk.position_sizing import PositionSizer
from execution.broker import MockBroker
from data.polygon_client import PolygonOptionsClient


class SignalDispatcher:
    """Dispatch trading signals to multiple channels and record confirmed executions."""

    def __init__(self, broker=None, db=None, notify_telegram=True, notify_discord=True):
        self.broker = broker or MockBroker()
        self.db = db
        self.sizer = PositionSizer()
        self.polygon = PolygonOptionsClient()
        self.websocket_subscribers = []  # [(callback, symbol)] pairs
        self.notify_telegram = notify_telegram
        self.notify_discord = notify_discord
        self.dispatch_log = []

    def _get_db(self):
        if self.db is None:
            from database.db_manager import DatabaseManager
            self.db = DatabaseManager()
        return self.db

    async def subscribe_websocket(self, symbol: str, callback: Callable):
        """Subscribe a WebSocket callback to symbol signals."""
        self.websocket_subscribers.append((symbol, callback))
        logger.info(f"WebSocket subscriber added for {symbol}")

    async def _enrich_with_options_data(self, signal: Dict[str, Any]):
        """
        Fetches the nearest ATM option contract from Polygon, calculates the option score,
        and appends it to the signal payload to prevent "Data unavailable" UI errors.
        Acts as a final liquidity gate.
        """
        # If signal already has valid active option data provided, do not overwrite
        existing_opt = signal.get("option_data")
        if existing_opt and (existing_opt.get("status") == "Active" or (existing_opt.get("contract_ticker") and existing_opt.get("contract_ticker") != "N/A")):
            if "status" not in existing_opt:
                existing_opt["status"] = "Active"
            return

        symbol = signal.get("symbol", "UNKNOWN")
        price = signal.get("price", 0.0)
        decision = signal.get("decision", "NEUTRAL")

        # Default to CALL for Neutral/Long, PUT for Short
        contract_type = "put" if decision in ["SHORT", "SELL"] else "call"

        # Default fallback payload for the UI to prevent rendering N/A
        signal["option_data"] = {
            "status": "Data unavailable",
            "contract_ticker": "N/A",
            "strike_price": "N/A",
            "expiration_date": "N/A",
            "premium": "N/A",
            "iv": "N/A",
            "open_interest": "N/A",
            "volume": 0,
            "bid_ask_spread": "N/A",
            "option_score": 0.0
        }

        if price <= 0:
            return

        try:
            # 1. Fetch active option chain with spot price moneyness filtering
            contracts = await self.polygon.get_options_chain(
                underlying_symbol=symbol, 
                contract_type=contract_type,
                spot_price=price
            )
            
            if not contracts:
                return

            # 2. Find the optimal At-The-Money (ATM) / Near-The-Money strike within ±10%
            near_money = [
                c for c in contracts 
                if "strike_price" in c and c["strike_price"] > 0
                and abs(c["strike_price"] - price) / price <= 0.10
            ]
            valid_contracts = near_money if near_money else [c for c in contracts if "strike_price" in c and c["strike_price"] > 0]
            if not valid_contracts:
                return

            if contract_type == "call":
                otm_list = [c for c in valid_contracts if c["strike_price"] >= (price * 0.99)]
                atm_contract = min(otm_list or valid_contracts, key=lambda c: abs(c["strike_price"] - price))
            else:
                otm_list = [c for c in valid_contracts if c["strike_price"] <= (price * 1.01)]
                atm_contract = min(otm_list or valid_contracts, key=lambda c: abs(c["strike_price"] - price))

            # 3. Fetch real-time quote for the ATM contract
            ticker = atm_contract["ticker"]
            quote = await self.polygon.get_option_realtime_quote(ticker)

            bid = quote.get("bid_price", 0.0) or atm_contract.get("bid_price", 0.0)
            ask = quote.get("ask_price", 0.0) or atm_contract.get("ask_price", 0.0)
            last = quote.get("last_trade_price", 0.0) or atm_contract.get("last_trade_price", 0.0)
            premium = last if last > 0 else ((bid + ask) / 2.0 if (bid + ask) > 0 else 0.0)
            
            iv = quote.get("implied_volatility") or atm_contract.get("implied_volatility", 0.0)
            iv_display = f"{iv * 100:.2f}%" if iv else "N/A"
            
            volume = (quote.get("day") or {}).get("volume", 0) or quote.get("volume", 0) or atm_contract.get("volume", 0)
            open_interest = quote.get("open_interest", 0) or atm_contract.get("open_interest", 0)

            # 4. Calculate Option Score & Liquidity
            spread_abs = abs(ask - bid)
            spread_pct = (spread_abs / premium) * 100.0 if premium > 0 else 100.0
            is_liquid = (spread_pct < 20.0 or spread_abs <= 0.25) and premium > 0 and (bid > 0 or last > 0)
            
            option_score = 0.0
            if is_liquid:
                liquidity_pts = max(0.0, 50.0 - (spread_pct * 2.5))
                flow_pts = min((volume / max(open_interest, 1)) * 30.0, 30.0)
                pricing_pts = 20.0 if last > 0 else 10.0
                option_score = round(max(0.0, min(100.0, liquidity_pts + flow_pts + pricing_pts)), 1)

            # 5. Populate the signal with valid data for the Dashboard
            signal["option_data"] = {
                "status": "Active" if is_liquid else "Illiquid",
                "contract_type": contract_type.upper(),
                "contract_ticker": ticker,
                "strike_price": f"${atm_contract['strike_price']:.2f}",
                "expiration_date": atm_contract.get("expiration_date", "N/A"),
                "premium": f"${premium:.2f}",
                "iv": iv_display,
                "open_interest": open_interest,
                "volume": volume,
                "bid_ask_spread": f"${spread_abs:.2f}",
                "option_score": option_score
            }
            if is_liquid and premium > 0:
                signal["premium"] = premium
                signal["instrument"] = "OPTION"

            # 6. Final Risk Gate: Veto trade if option is illiquid, 0DTE afternoon cutoff, or IV crush
            from filters.veto_filter import VetoFilter
            is_opt_vetoed, opt_veto_reason = VetoFilter.check_options_time_and_iv(signal["option_data"])

            if (not is_liquid or is_opt_vetoed) and signal.get("decision") in ["LONG", "SHORT", "BUY", "SELL"]:
                signal["decision"] = "NO_TRADE"
                signal["entry_valid"] = False
                reasons = []
                if not is_liquid:
                    reasons.append(f"Option {ticker} is illiquid (Spread: {spread_pct:.1f}%).")
                if is_opt_vetoed and opt_veto_reason:
                    reasons.append(opt_veto_reason)
                signal["veto_reason"] = "; ".join(reasons)
                signal.setdefault("warnings", []).append(f"VETO: {signal['veto_reason']}")
                logger.warning(f"Trade vetoed: {signal['veto_reason']}")

        except Exception as e:
            logger.error(f"Failed to fetch Polygon options data for {symbol}: {e}")

    async def dispatch(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch signal to all channels.

        Args:
            signal: Signal dict from QuantEngine

        Returns:
            Dispatch result
        """
        symbol = signal.get("symbol", "UNKNOWN")
        logger.info(f"📤 Dispatching signal for {symbol}")

        # Enrich the signal with Polygon Options Data before broadcasting
        await self._enrich_with_options_data(signal)

        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_score": signal.get("score", 0),
            "decision": signal.get("decision", "NO_TRADE"),
            "dispatches": {}
        }

        try:
            # 1. Broadcast to WebSocket subscribers (dashboard)
            await self._broadcast_websocket(signal)
            result["dispatches"]["websocket"] = "sent"

            # 2. External notifications
            if signal.get("score", 0) >= 70:  # Only notify on high-confidence signals
                if self.notify_telegram:
                    await self._notify_telegram(signal)
                    result["dispatches"]["telegram"] = "sent"

                if self.notify_discord:
                    await self._notify_discord(signal)
                    result["dispatches"]["discord"] = "sent"

            # 3. Execute on broker (paper trading mode)
            if signal.get("entry_valid") and signal.get("decision") in ["LONG", "SHORT"]:
                order = await self._execute_broker(signal)
                result["dispatches"]["broker"] = f"order_{order.get('id', 'pending')}"
                result["order_id"] = order.get("id")
                result["order"] = order
                result["trade"] = order.get("trade")
                result["status"] = order.get("status")

            # 4. Log dispatch
            self.dispatch_log.append(result)

            logger.success(f"✓ Signal dispatched: {result}")
            return result

        except Exception as e:
            logger.error(f"❌ Dispatch error: {e}")
            result["error"] = str(e)
            return result

    async def _broadcast_websocket(self, signal: Dict[str, Any]):
        """Broadcast signal to WebSocket subscribers."""
        symbol = signal.get("symbol")
        if not self.websocket_subscribers:
            return

        tasks = []
        for sub_symbol, callback in self.websocket_subscribers:
            if sub_symbol == symbol:
                try:
                    tasks.append(callback(signal))
                except Exception as e:
                    logger.error(f"WebSocket callback error: {e}")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.debug(f"Broadcast to {len(tasks)} WebSocket subscribers")

    async def _notify_telegram(self, signal: Dict[str, Any]):
        """Send signal to Telegram."""
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")

            if not token or not chat_id:
                logger.warning("Telegram credentials not configured")
                return

            message = self._format_telegram_message(signal)

            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    "chat_id": str(chat_id),
                    "text": message
                }
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info(f"📱 Telegram alert sent successfully for {signal.get('symbol')}")
                    else:
                        resp_text = await resp.text()
                        logger.warning(f"Telegram API {resp.status}: {resp_text}")

        except Exception as e:
            logger.error(f"Telegram error: {e}")

    async def notify_position_exit(self, symbol: str, exit_price: float, exit_reason: str, pnl: Optional[float] = None):
        """Send position close notification to Telegram."""
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")

            if not token or not chat_id:
                return

            lines = [
                "🛑 METOBOT 0.5 POZİSYON KAPANDI 🛑",
                "",
                f"🔷 Varlık: {symbol}",
                f"🔷 Çıkış Fiyatı: ${exit_price:.2f}",
                f"🔷 Neden: {exit_reason}"
            ]
            if pnl is not None:
                pnl_sign = "+" if pnl >= 0 else ""
                lines.append(f"🔷 Gerçekleşen K/Z: {pnl_sign}${pnl:.2f}")

            message = "\n".join(lines)

            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {"chat_id": str(chat_id), "text": message}
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info(f"📱 Telegram exit alert sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram exit alert error: {e}")

    async def _notify_discord(self, signal: Dict[str, Any]):
        """Send signal to Discord webhook."""
        try:
            webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

            if not webhook_url:
                logger.warning("Discord webhook not configured")
                return

            embed = self._format_discord_embed(signal)

            # TODO: Implement actual Discord webhook call
            # async with aiohttp.ClientSession() as session:
            #     async with session.post(webhook_url, json={"embeds": [embed]}) as resp:
            #         logger.info(f"Discord sent: {resp.status}")

            logger.info(f"🎮 Discord: Signal posted")

        except Exception as e:
            logger.error(f"Discord error: {e}")

    async def _execute_broker(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Execute order on broker (paper or live) and record trade in Trade History upon confirmation."""
        try:
            symbol = signal.get("symbol", "UNKNOWN")
            underlying = signal.get("underlying") or symbol
            from engine.market_hours import MarketSchedule
            is_market_open, session, market_reason = MarketSchedule.is_market_open(symbol)

            # Trade Engine Gatekeeper with VETO authority
            trade_eval = signal.get("trade_evaluation")
            # If trade_eval was generated on stock before option enrichment, or not present, re-evaluate
            if not trade_eval or (signal.get("option_data") and trade_eval.get("planned_notional", 0) > 1000 and signal.get("premium")):
                from engine.trade_engine import global_trade_engine
                trade_eval = global_trade_engine.evaluate_candidate(
                    candidate_signal=signal,
                    is_market_open=is_market_open,
                    market_reason=market_reason
                )
                signal["trade_evaluation"] = trade_eval

            if not trade_eval.get("approved"):
                logger.warning(f"🚫 Trade Engine VETO for {symbol}: {trade_eval.get('primary_reason')}")
                db = self._get_db()
                await db.save_rejected_signal({
                    "symbol": symbol,
                    "underlying": underlying,
                    "direction": signal.get("decision", "NO_TRADE"),
                    "strategy": signal.get("strategy"),
                    "score": signal.get("score"),
                    "confidence": signal.get("confidence_evaluation", {}).get("model_confidence", signal.get("score")),
                    "market_regime": signal.get("regime"),
                    "reason_code": trade_eval.get("primary_reason", "VETO_REJECTED"),
                    "reason_detail": trade_eval.get("decision_explanation")
                })
                return {
                    "id": None,
                    "status": "rejected",
                    "reason": trade_eval.get("primary_reason"),
                    "rejection_reasons": trade_eval.get("rejection_reasons", []),
                    "error": trade_eval.get("primary_reason")
                }

            # Final Safety Gate: 0DTE Time and IV Crush Check
            from filters.veto_filter import VetoFilter
            is_opt_vetoed, opt_veto_reason = VetoFilter.check_options_time_and_iv(signal.get("option_data"))
            if is_opt_vetoed:
                logger.warning(f"🚫 Order execution rejected by Option Veto: {opt_veto_reason}")
                return {
                    "id": None,
                    "status": "rejected",
                    "reason": opt_veto_reason,
                    "error": opt_veto_reason
                }

            # Calculate planned quantity and notional size
            planned_qty = trade_eval.get("planned_quantity") or signal.get("quantity") or 1.0
            signal["quantity"] = planned_qty

            order = await self.broker.place_order(signal)
            logger.success(f"Order placed: {order.get('id')}")

            # Record in Trade History (trade_logs) ONLY when position entry is confirmed
            if order.get("status") in ["filled", "open"]:
                db = self._get_db()
                opt = signal.get("option_data", {})
                raw_strike = opt.get("strike_price")
                strike_num = None
                if raw_strike and isinstance(raw_strike, str) and "$" in raw_strike:
                    try:
                        strike_num = float(raw_strike.replace("$", "").strip())
                    except ValueError:
                        pass
                elif isinstance(raw_strike, (int, float)):
                    strike_num = float(raw_strike)

                contract_sym = opt.get("contract_ticker")
                if not contract_sym or contract_sym == "N/A":
                    contract_sym = signal.get("symbol")

                is_call = signal.get("decision") in ["LONG", "BUY"]
                opt_type = opt.get("contract_type", "CALL" if is_call else "PUT") if opt.get("strike_price") not in (None, "N/A") else "STOCK"

                risk_m = signal.get("risk_metrics", {})
                sl = risk_m.get("stop_loss") or signal.get("stop_loss")
                tp = risk_m.get("take_profit") or signal.get("take_profit")

                stock_p = float(signal.get("price", 0.0))
                atr = float(signal.get("features", {}).get("atr", 1.0))
                tp1 = risk_m.get("tp1") or round(stock_p + 1.75 * atr if is_call else stock_p - 1.75 * atr, 2)
                tp2 = risk_m.get("tp2") or tp or round(stock_p + 3.50 * atr if is_call else stock_p - 3.50 * atr, 2)

                conf_eval = signal.get("confidence_evaluation", {})
                dir_eval = signal.get("direction_evaluation", {})

                trade_payload = {
                    "trade_id": order.get("id"),
                    "symbol": contract_sym,
                    "underlying": underlying,
                    "option_type": opt_type,
                    "strike_price": strike_num,
                    "expiration": opt.get("expiration_date") if opt.get("expiration_date") != "N/A" else None,
                    "contract_symbol": contract_sym,
                    "side": signal.get("decision", "LONG"),
                    "entry_price": order.get("fill_price", signal.get("price", 0.0)),
                    "stop_loss": sl,
                    "take_profit": tp2,
                    "tp1": tp1,
                    "tp2": tp2,
                    "trailing_stop": sl,
                    "quantity": planned_qty,
                    "score": signal.get("score"),
                    "direction_score": dir_eval.get("bullish_score") if is_call else dir_eval.get("bearish_score"),
                    "direction_edge": dir_eval.get("direction_edge"),
                    "confidence": conf_eval.get("model_confidence", signal.get("score")),
                    "strategy": signal.get("strategy", "QUANT_STRATEGY"),
                    "signal_type": signal.get("position_state", "ENTRY_READY"),
                    "entry_thesis": trade_eval.get("decision_explanation") or "Institutional trade entry approved.",
                    "instrument": "OPTION" if opt_type in ["CALL", "PUT"] else "STOCK",
                    "is_0dte": 1 if opt.get("is_0dte") else 0,
                    "status": "OPEN"
                }

                trade = await db.create_trade(trade_payload)
                order["trade"] = trade

                # Notify PositionManager
                try:
                    from engine.position_manager import global_position_manager
                    global_position_manager.open_position(trade)
                except Exception as pm_err:
                    logger.debug(f"PositionManager open error: {pm_err}")

                # Broadcast real-time trade event to WebSocket clients
                try:
                    from web.routes.ws import broadcast_trade_update
                    await broadcast_trade_update(trade)
                except Exception as b_err:
                    logger.debug(f"Broadcast trade error: {b_err}")

            return order

        except Exception as e:
            logger.error(f"Broker execution error: {e}")
            return {"error": str(e)}

    async def execute_position_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "Exit signal confirmed",
        result: str = "CLOSED"
    ) -> Optional[Dict[str, Any]]:
        """Close an active position for the symbol and update Trade History."""
        try:
            db = self._get_db()
            open_trade = await db.get_open_trade_for_symbol(symbol)
            if not open_trade:
                logger.debug(f"No open trade found to exit for symbol: {symbol}")
                return None

            trade_id = open_trade["trade_id"]
            actual_exit_price = float(exit_price)
            opt_type = open_trade.get("option_type")
            entry_p = float(open_trade.get("entry_price") or 0.0)

            # If this is an option trade and caller passed the underlying stock price instead of option premium
            strike_p = float(open_trade.get("strike_price") or 0.0)
            is_stock_spot_price = (
                (entry_p > 0 and actual_exit_price > entry_p * 4.0 and actual_exit_price > 25.0) or
                (strike_p > 0 and abs(actual_exit_price - strike_p) / strike_p < 0.25 and actual_exit_price > 25.0)
            )
            if opt_type in ["CALL", "PUT"] and is_stock_spot_price:
                contract_sym = open_trade.get("contract_symbol") or open_trade.get("symbol")
                resolved_opt_quote = False
                if contract_sym and contract_sym != open_trade.get("underlying"):
                    try:
                        quote = await self.polygon.get_option_realtime_quote(contract_sym)
                        bid = quote.get("bid_price", 0.0)
                        last = quote.get("last_trade_price", 0.0)
                        ask = quote.get("ask_price", 0.0)
                        opt_bid = bid if bid > 0 else (last if last > 0 else ask)
                        if opt_bid > 0:
                            actual_exit_price = opt_bid
                            resolved_opt_quote = True
                    except Exception as q_err:
                        logger.warning(f"Could not fetch option exit quote for {contract_sym}: {q_err}")

                # Rational option price estimation: calculate mark from delta and spot move
                if not resolved_opt_quote and entry_p > 0:
                    intrinsic = max(0.0, actual_exit_price - strike_p) if opt_type == "CALL" else max(0.0, strike_p - actual_exit_price)
                    if intrinsic > 0:
                        actual_exit_price = round(max(intrinsic, 0.05), 2)
                    elif strike_p > 0:
                        delta = 0.50 if opt_type == "CALL" else -0.50
                        spot_change = (actual_exit_price - strike_p)
                        estimated = max(0.05, entry_p + (delta * spot_change * 0.10))
                        actual_exit_price = round(estimated, 2)
                    else:
                        actual_exit_price = entry_p

            # Close on broker
            await self.broker.close_order(trade_id, actual_exit_price)

            # Close in database
            closed_trade = await db.close_trade(
                trade_id=trade_id,
                exit_price=actual_exit_price,
                exit_reason=exit_reason,
                result=result
            )

            # Notify PositionManager
            try:
                from engine.position_manager import global_position_manager
                underlying = open_trade.get("underlying") or symbol
                global_position_manager.close_position(
                    underlying=underlying,
                    exit_price=actual_exit_price,
                    exit_reason=exit_reason,
                    result_code=result
                )
            except Exception as pm_err:
                logger.debug(f"PositionManager close error: {pm_err}")

            if closed_trade:
                try:
                    from web.routes.ws import broadcast_trade_update
                    await broadcast_trade_update(closed_trade)
                except Exception as b_err:
                    logger.debug(f"Broadcast trade exit error: {b_err}")

            return closed_trade
        except Exception as e:
            logger.error(f"Failed to execute position exit for {symbol}: {e}")
            return None
            return None

    def _format_telegram_message(self, signal: Dict[str, Any]) -> str:
        """
        Format signal as Telegram message matching user's canonical Metobot template.
        Template:
        🚨 METOBOT 0.5 SİNYAL RAPORU 🚨

        🔷 Varlık: NVDA
        🔷 Yön: LONG
        🔷 Kalite Sınıfı: NO
        🔷 Güven Oranı: %25.93
        🔷 Stop-Loss: $0.00
        🔷 Risk Çarpanı: 0.0R
        🔷 Piyasa Rejimi: TRENDING
        """
        symbol = signal.get("symbol", "UNKNOWN")

        # Yön
        dec = str(signal.get("decision", "NO TRADE")).upper()
        strat_side = str(signal.get("strategy_side", signal.get("features", {}).get("side", ""))).upper()
        if dec in ("LONG", "BUY"):
            side_str = "LONG"
        elif dec in ("SHORT", "SELL"):
            side_str = "SHORT"
        elif strat_side in ("LONG", "SHORT"):
            side_str = strat_side
        else:
            raw_r = str(signal.get("regime", "")).upper()
            side_str = "LONG" if "BULL" in raw_r else ("SHORT" if "BEAR" in raw_r else "NÖTR")

        score = float(signal.get("score", 0.0))
        entry_valid = bool(signal.get("entry_valid", False))

        # Kalite Sınıfı
        if not entry_valid or score < 50.0 or side_str == "NÖTR":
            kalite = "NO"
        elif score >= 85.0:
            kalite = "A+ (YÜKSEK)"
        elif score >= 75.0:
            kalite = "A"
        elif score >= 65.0:
            kalite = "B"
        else:
            kalite = "C"

        # Stop-Loss ve Risk Çarpanı
        risk = signal.get("risk_metrics", {})
        stop_loss = float(risk.get("stop_loss", 0.0))
        rrr = float(risk.get("reward_risk_ratio", 0.0))

        # Piyasa Rejimi
        raw_regime = str(signal.get("regime", "UNKNOWN")).upper()
        if "TREND" in raw_regime:
            regime_str = "TRENDING"
        elif "RANGE" in raw_regime:
            regime_str = "RANGE"
        elif "COMPRESSION" in raw_regime:
            regime_str = "VOLATILITY_COMPRESSION"
        else:
            regime_str = raw_regime

        lines = [
            "🚨 METOBOT 0.5 SİNYAL RAPORU 🚨",
            "",
            f"🔷 Varlık: {symbol}",
            f"🔷 Yön: {side_str}",
            f"🔷 Kalite Sınıfı: {kalite}",
            f"🔷 Güven Oranı: %{score:.2f}",
            f"🔷 Stop-Loss: ${stop_loss:.2f}",
            f"🔷 Risk Çarpanı: {rrr:.1f}R",
            f"🔷 Piyasa Rejimi: {regime_str}"
        ]

        # Opsiyon sözleşme detayları varsa ekle
        opt = signal.get("option_data", {})
        if opt and opt.get("contract_ticker"):
            raw_prem = str(opt.get("premium", "0.0")).replace("$", "").strip()
            try:
                premium = float(raw_prem)
            except ValueError:
                premium = 0.0
            raw_strike = str(opt.get("strike_price", "")).replace("$", "").strip()
            lines.append(f"🔷 Opsiyon: {opt.get('contract_ticker')} @ ${premium:.2f}")
            if raw_strike:
                lines.append(f"🔷 Strike: ${raw_strike} | Vade: {opt.get('expiration_date', 'N/A')}")

        return "\n".join(lines)

    def _format_discord_embed(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Format signal as Discord embed."""
        symbol = signal.get("symbol", "UNKNOWN")
        score = signal.get("score", 0)
        decision = signal.get("decision", "NO_TRADE")
        opt = signal.get("option_data", {})

        color = 0x00FF00 if decision == "LONG" else (0xFF0000 if decision == "SHORT" else 0x808080)

        return {
            "title": f"Metobot Signal: {symbol}",
            "description": f"Decision: {decision}",
            "color": color,
            "fields": [
                {"name": "Score", "value": f"{score:.1f}/100", "inline": True},
                {"name": "Regime", "value": signal.get("regime", "?"), "inline": True},
                {"name": "Price", "value": f"${signal.get('price', 0):.2f}", "inline": False},
                {"name": "ATM Option", "value": f"{opt.get('contract_ticker', 'N/A')} ({opt.get('premium', 'N/A')})", "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def get_dispatch_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent dispatch history."""
        return self.dispatch_log[-limit:]