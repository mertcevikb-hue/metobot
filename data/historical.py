import asyncio
from typing import List, Dict, Any, Optional
from loguru import logger
import yfinance as yf

class HistoricalDataLoader:
    def __init__(self, symbol: str = "SPY", period: str = "6mo", interval: str = "1d"):
        self.symbol = symbol.upper()
        self.period = period
        self.interval = interval

    async def fetch_data(self, symbol: Optional[str] = None, period: Optional[str] = None, interval: Optional[str] = None) -> List[Dict[str, Any]]:
        """yfinance kullanarak belirtilen sembol için gerçek OHLCV mum verilerini çeker."""
        import re
        import pandas as pd
        clean_sym = re.sub(r"[^A-Za-z0-9.\-_]", "", (symbol or self.symbol).upper().replace("$", ""))
        if not clean_sym:
            logger.warning("Geçersiz sembol talebi reddedildi.")
            return []
            
        target_symbol = clean_sym
        target_period = period or self.period
        target_interval = interval or self.interval

        def _download():
            try:
                is_intraday = any(x in str(target_interval) for x in ['m', 'h'])
                df = yf.download(
                    target_symbol,
                    period=target_period,
                    interval=target_interval,
                    prepost=is_intraday,
                    progress=False
                )
                if df.empty:
                    logger.warning(f"{target_symbol} için yfinance verisi boş döndü.")
                    return []
                
                # MultiIndex sütun yapısını düzleştir
                if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
                    df.columns = df.columns.get_level_values(0)

                candles = []
                for idx, row in df.iterrows():
                    c = float(row["Close"]) if not pd.isna(row.get("Close")) else 0.0
                    if c <= 0:
                        continue
                    o = float(row["Open"]) if not pd.isna(row.get("Open")) else c
                    h = float(row["High"]) if not pd.isna(row.get("High")) else c
                    l = float(row["Low"]) if not pd.isna(row.get("Low")) else c
                    v = float(row["Volume"]) if not pd.isna(row.get("Volume")) else 0.0
                    
                    candles.append({
                        "date": str(idx),
                        "open": o if o > 0 else c,
                        "high": max(h, c, o),
                        "low": min(l, c, o) if l > 0 else c,
                        "close": c,
                        "volume": v if v > 0 else 1.0
                    })
                return candles
            except Exception as e:
                logger.error(f"yfinance veri çekme hatası ({target_symbol}): {e}")
                return []

        candles = await asyncio.to_thread(_download)
        return candles

    async def fetch_recent_data(self, symbol: Optional[str] = None, limit: int = 100, days: Optional[int] = None) -> List[Dict[str, Any]]:
        """İstenen sembolün son N günlük mum verilerini döndürür."""
        target_period = f"{days}d" if days and days > 0 else self.period
        candles = await self.fetch_data(symbol=symbol, period=target_period)
        if candles and len(candles) > limit:
            return candles[-limit:]
        return candles

    @classmethod
    async def get_closes(cls, symbol: str = "SPY") -> List[float]:
        loader = cls(symbol=symbol)
        candles = await loader.fetch_data()
        return [c["close"] for c in candles if c.get("close") is not None]