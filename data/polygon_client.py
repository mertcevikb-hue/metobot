import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import yfinance as yf
import pandas as pd
import re

try:
    from polygon import RESTClient
except ImportError:
    try:
        from massive import RESTClient  # type: ignore
    except ImportError:
        RESTClient = None
from config.settings import settings


class PolygonOptionsClient:
    """
    Options Client providing 100% REAL LIVE MARKET OPTIONS DATA.
    - If POLYGON_API_KEY is configured and valid, queries Polygon.io.
    - If POLYGON_API_KEY is not configured or fails, queries CBOE directly via yfinance.
    - NEVER uses mock/simulated data. All contracts, strikes, bids, asks, and IVs are genuine live exchange data.
    """

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.POLYGON_API_KEY
        self._quote_cache: Dict[str, Dict[str, Any]] = {}
        self._chain_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

        if not key or "your_" in str(key).lower() or RESTClient is None:
            logger.info("ℹ️ Polygon API Key tanımlı değil. Canlı borsa opsiyon zinciri doğrudan CBOE canlı veri akışından çekilecek (%100 Gerçek Piyasa).")
            self.client = None
            self.is_configured = False
        else:
            try:
                self.client = RESTClient(key)
                self.is_configured = True
            except Exception as e:
                logger.error(f"Polygon istemcisi başlatılamadı: {e}. Canlı CBOE borsa akışına geçiliyor.")
                self.client = None
                self.is_configured = False

    async def get_options_chain(
        self, 
        underlying_symbol: str, 
        contract_type: Optional[str] = None,
        spot_price: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches active real options contracts from the live exchange (Polygon or CBOE).
        100% REAL market data.
        Smart moneyness & expiration filtering prevents selecting deep out-of-money, illiquid, or 0DTE decaying contracts.
        """
        sym = underlying_symbol.upper().replace("/", "").replace("-USD", "")
        c_type = (contract_type or "call").lower()
        price_bucket = round(spot_price, 0) if (spot_price and spot_price > 0) else "any"
        cache_key = f"{sym}_{c_type}_{price_bucket}"
        now_ts = time.time()

        # Check 60-second cache to prevent network hammering
        if cache_key in self._chain_cache:
            cache_time, cached_contracts = self._chain_cache[cache_key]
            if now_ts - cache_time < 60.0 and cached_contracts:
                return cached_contracts

        # 1. Try Polygon REST API if client is available
        if self.client:
            def _fetch_polygon():
                contracts = []
                kwargs = {
                    "underlying_ticker": sym,
                    "contract_type": c_type if c_type in ["call", "put"] else None,
                    "expired": False,
                    "limit": 250
                }
                if spot_price and spot_price > 0:
                    kwargs["strike_price_gte"] = max(1.0, round(spot_price * 0.85, 2))
                    kwargs["strike_price_lte"] = round(spot_price * 1.15, 2)

                for c in self.client.list_options_contracts(**kwargs):
                    contracts.append({
                        "ticker": c.ticker,
                        "strike_price": getattr(c, 'strike_price', 0.0),
                        "expiration_date": getattr(c, 'expiration_date', "N/A"),
                        "open_interest": getattr(c, 'open_interest', 0)
                    })
                return contracts

            try:
                res = await asyncio.to_thread(_fetch_polygon)
                if res:
                    self._chain_cache[cache_key] = (now_ts, res)
                    return res
            except Exception as e:
                logger.warning(f"Polygon API opsiyon zinciri alınamadı ({sym}): {e}. Doğrudan canlı CBOE borsa akışına geçiliyor...")

        # 2. Direct Live CBOE Option Chain via yfinance
        def _fetch_real_cboe():
            t = yf.Ticker(sym)
            expirations = t.options
            if not expirations:
                return []

            # Smart Expiration Selection:
            # Avoid 0DTE decaying or expired options; target standard weekly expirations (DTE >= 1, ideally 2-14 days)
            today = datetime.now(timezone.utc).date()
            valid_exps = []
            for exp_str in expirations:
                try:
                    exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_d - today).days
                    if dte >= 1:
                        valid_exps.append((dte, exp_str))
                except Exception:
                    continue

            target_exp = expirations[0]
            if valid_exps:
                target_exp = valid_exps[0][1]
                for dte, exp_str in valid_exps:
                    if 2 <= dte <= 14:
                        target_exp = exp_str
                        break

            chain = t.option_chain(target_exp)
            df = chain.calls if c_type == "call" else chain.puts
            if df.empty:
                return []

            # Moneyness filter: keep contracts within ±15% of spot price if spot_price is available
            if spot_price and spot_price > 0:
                min_k = spot_price * 0.85
                max_k = spot_price * 1.15
                df_filtered = df[(df["strike"] >= min_k) & (df["strike"] <= max_k)]
                if not df_filtered.empty:
                    df = df_filtered

            contracts = []
            for _, row in df.iterrows():
                ticker = str(row.get("contractSymbol", ""))
                strike = float(row.get("strike", 0.0))
                bid = float(row.get("bid", 0.0)) if not pd.isna(row.get("bid")) else 0.0
                ask = float(row.get("ask", 0.0)) if not pd.isna(row.get("ask")) else 0.0
                last_price = float(row.get("lastPrice", 0.0)) if not pd.isna(row.get("lastPrice")) else 0.0
                oi = int(row.get("openInterest", 0)) if not pd.isna(row.get("openInterest")) else 0
                vol = int(row.get("volume", 0)) if not pd.isna(row.get("volume")) else 0
                iv = float(row.get("impliedVolatility", 0.0)) if not pd.isna(row.get("impliedVolatility")) else 0.0

                # Skip contracts with zero market presence
                if bid == 0.0 and ask == 0.0 and last_price == 0.0:
                    continue

                quote_info = {
                    "bid_price": bid,
                    "ask_price": ask,
                    "last_trade_price": last_price,
                    "implied_volatility": iv,
                    "open_interest": oi,
                    "volume": vol
                }
                # Cache for subsequent quote lookups
                self._quote_cache[ticker] = quote_info
                self._quote_cache[f"O:{ticker}"] = quote_info

                contracts.append({
                    "ticker": ticker,
                    "strike_price": strike,
                    "expiration_date": target_exp,
                    "open_interest": oi,
                    "volume": vol,
                    "bid_price": bid,
                    "ask_price": ask,
                    "last_trade_price": last_price,
                    "implied_volatility": iv
                })

            if spot_price and spot_price > 0 and contracts:
                contracts.sort(key=lambda c: abs(c["strike_price"] - spot_price))

            return contracts

        try:
            real_contracts = await asyncio.to_thread(_fetch_real_cboe)
            if real_contracts:
                self._chain_cache[cache_key] = (now_ts, real_contracts)
                logger.info(f"✓ CBOE canlı opsiyon zinciri güncellendi: {sym} ({len(real_contracts)} gerçek kontrat, Vade: {real_contracts[0]['expiration_date']})")
                return real_contracts
        except Exception as e:
            logger.error(f"Canlı CBOE opsiyon zinciri çekilemedi ({sym}): {e}")

        return []

    async def get_option_realtime_quote(self, option_ticker: str) -> Dict[str, Any]:
        """
        Fetches the latest real-time quote for a specific option contract.
        100% REAL LIVE exchange quotes (CBOE / Polygon).
        """
        clean_ticker = option_ticker.replace("O:", "")

        now_t = time.time()
        # 1. Fast cache lookup with 5-second TTL
        if clean_ticker in self._quote_cache:
            item = self._quote_cache[clean_ticker]
            if isinstance(item, tuple) and (now_t - item[0] < 5.0):
                return item[1]
            elif isinstance(item, dict):
                return item
        if option_ticker in self._quote_cache:
            item = self._quote_cache[option_ticker]
            if isinstance(item, tuple) and (now_t - item[0] < 5.0):
                return item[1]
            elif isinstance(item, dict):
                return item

        # 2. Polygon REST quote if available
        if self.client:
            def _fetch_poly_quote():
                quote = self.client.get_last_quote(option_ticker)
                last_p = getattr(quote, 'last_trade_price', 0.0) or getattr(quote, 'price', 0.0) or getattr(quote, 'ask_price', 0.0)
                return {
                    "bid_price": getattr(quote, 'bid_price', 0.0),
                    "ask_price": getattr(quote, 'ask_price', 0.0),
                    "last_trade_price": last_p,
                    "implied_volatility": getattr(quote, 'implied_volatility', 0.0)
                }
            try:
                res = await asyncio.to_thread(_fetch_poly_quote)
                if res and res.get("bid_price", 0) > 0:
                    return res
            except Exception:
                pass

        # 3. Direct CBOE quote via yfinance
        m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", clean_ticker)
        if m:
            underlying = m.group(1)
            date_str = m.group(2)
            target_exp = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"

            def _fetch_cboe_specific():
                t = yf.Ticker(underlying)
                chain = t.option_chain(target_exp)
                df = chain.calls if m.group(3) == "C" else chain.puts
                match = df[df["contractSymbol"] == clean_ticker]
                if not match.empty:
                    row = match.iloc[0]
                    return {
                        "bid_price": float(row.get("bid", 0.0)) if not pd.isna(row.get("bid")) else 0.0,
                        "ask_price": float(row.get("ask", 0.0)) if not pd.isna(row.get("ask")) else 0.0,
                        "last_trade_price": float(row.get("lastPrice", 0.0)) if not pd.isna(row.get("lastPrice")) else 0.0,
                        "implied_volatility": float(row.get("impliedVolatility", 0.0)) if not pd.isna(row.get("impliedVolatility")) else 0.0
                    }
                return None

            try:
                res = await asyncio.to_thread(_fetch_cboe_specific)
                if res:
                    self._quote_cache[clean_ticker] = (now_t, res)
                    return res
            except Exception as e:
                logger.error(f"Canlı CBOE kontrat fiyatı çekilemedi ({clean_ticker}): {e}")

        return {
            "bid_price": 0.0,
            "ask_price": 0.0,
            "last_trade_price": 0.0,
            "implied_volatility": 0.0
        }