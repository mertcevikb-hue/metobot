"""
Data validation layer for quantitative pipeline.
Rejects malformed, stale, or incomplete market feeds.
Protects against corrupt data, NaN/Inf, price invariants, and illiquid feeds.
"""
from typing import List, Dict, Any, Tuple, Optional
import math

class DataGuard:
    @staticmethod
    def validate_ohlcv(
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
        min_bars: int = 35
    ) -> Tuple[bool, Optional[str]]:
        if not closes or not highs or not lows or not volumes:
            return False, "Empty series arrays provided."
        
        n = len(closes)
        if n < min_bars:
            return False, f"Insufficient historical depth: {n} bars (minimum {min_bars} required)."
        
        if not (len(highs) == len(lows) == len(volumes) == n):
            return False, "Mismatched OHLCV array lengths."

        for i in range(n):
            c, h, l, v = closes[i], highs[i], lows[i], volumes[i]
            if any(x is None or math.isnan(x) or math.isinf(x) for x in (c, h, l, v)):
                return False, f"Corrupted or NaN/Inf value detected at index {i}."
            if c <= 0 or h <= 0 or l <= 0:
                return False, f"Non-positive pricing detected at index {i} (Close: {c}, High: {h}, Low: {l})."
            if v < 0:
                return False, f"Negative volume detected at index {i} ({v})."
            if h < l:
                return False, f"Invariant violation High < Low at index {i} ({h} < {l})."
            if h < c:
                return False, f"Invariant violation High < Close at index {i} ({h} < {c})."
            if l > c:
                return False, f"Invariant violation Low > Close at index {i} ({l} > {c})."
        
        if sum(volumes) <= 0:
            return False, "Illiquid asset: Zero trading volume across entire historical feed."
            
        return True, None