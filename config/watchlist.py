import re
from typing import Dict, List, Optional
from datetime import datetime, timezone
import zoneinfo

WATCHLIST = {
    "NASDAQ": ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"],
    "NYSE": ["JPM", "GLD", "TLT", "XLE", "XLF"],
    "BIST": ["GARAN", "AKBNK", "THYAO", "YKBNK"]
}

MARKET_HOURS = {
    "NASDAQ": {"open": "09:30", "close": "16:00"},
    "NYSE": {"open": "09:30", "close": "16:00"},
    "BIST": {"open": "09:30", "close": "18:00"}
}

TICKER_VALIDATOR = re.compile(r"^[A-Za-z0-9.\-_/]{1,15}$")


class WatchlistManager:
    def __init__(self):
        self.watchlist = {k: list(v) for k, v in WATCHLIST.items()}

    def get_symbols(self, exchange: Optional[str] = None) -> List[str]:
        if exchange:
            return self.watchlist.get(exchange.upper(), [])
        return [s for symbols in self.watchlist.values() for s in symbols]

    def add_symbol(self, exchange: str, symbol: str) -> bool:
        clean_sym = (symbol or "").strip().upper().replace("$", "")
        if not clean_sym or not TICKER_VALIDATOR.match(clean_sym):
            return False
        clean_ex = exchange.strip().upper()
        if clean_ex not in self.watchlist:
            self.watchlist[clean_ex] = []
        if clean_sym not in self.watchlist[clean_ex]:
            self.watchlist[clean_ex].append(clean_sym)
            return True
        return False

    def remove_symbol(self, exchange: str, symbol: str) -> bool:
        clean_sym = (symbol or "").strip().upper().replace("$", "")
        clean_ex = exchange.strip().upper()
        if clean_ex in self.watchlist and clean_sym in self.watchlist[clean_ex]:
            self.watchlist[clean_ex].remove(clean_sym)
            return True
        return False

    def is_market_open(self, exchange: str) -> bool:
        clean_ex = exchange.strip().upper()
        tz_map = {
            "NASDAQ": "America/New_York",
            "NYSE": "America/New_York",
            "BIST": "Europe/Istanbul"
        }
        tz_name = tz_map.get(clean_ex, "America/New_York")
        try:
            now_dt = datetime.now(zoneinfo.ZoneInfo(tz_name))
        except Exception:
            now_dt = datetime.now(timezone.utc)

        # Hafta sonu kontrolü (Pazartesi: 0, Pazar: 6)
        if now_dt.weekday() >= 5:
            return False

        current = now_dt.strftime("%H:%M")
        market = MARKET_HOURS.get(clean_ex)
        return market["open"] <= current <= market["close"] if market else False