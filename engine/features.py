"""
Continuous technical and options feature calculation using normalized metrics.
Eliminates binary bucket quantization (the 72.5% problem) and unifies
equity technicals with options chain liquidity and volatility dynamics.
"""
import math
from typing import List, Dict, Any, Optional


class FeatureEngine:
    """Computes normalized, continuous price-action and technical indicators."""

    @staticmethod
    def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if not closes or not highs or not lows:
            return 1e-4
        if len(closes) < period + 1:
            return max(highs[-1] - lows[-1], 1e-4)
        
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            tr_list.append(tr)
        
        atr = sum(tr_list[:period]) / period
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
        return max(atr, 1e-4)

    @staticmethod
    def calculate_ema(series: List[float], period: int) -> float:
        if not series or period <= 0:
            return series[-1] if series else 0.0
        if len(series) < period:
            period = len(series)
            
        k = 2.0 / (period + 1)
        ema = sum(series[:period]) / period
        for price in series[period:]:
            ema = (price - ema) * k + ema
        return ema

    @staticmethod
    def calculate_rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def calculate_vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> float:
        cum_vol = sum(volumes)
        if cum_vol <= 0 or len(closes) == 0:
            return closes[-1] if closes else 0.0
        
        length = min(len(highs), len(lows), len(closes), len(volumes))
        cum_tp_vol = sum(((highs[i] + lows[i] + closes[i]) / 3.0) * volumes[i] for i in range(length))
        return cum_tp_vol / cum_vol

    @classmethod
    def extract_features(
        cls,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        if not closes:
            return {}

        price = closes[-1]
        atr = cls.calculate_atr(highs, lows, closes, 14)
        ema9 = cls.calculate_ema(closes, 9)
        ema21 = cls.calculate_ema(closes, 21)
        ema50 = cls.calculate_ema(closes, min(50, len(closes)))
        vwap = cls.calculate_vwap(highs, lows, closes, volumes)
        rsi = cls.calculate_rsi(closes, 14)
        
        # Volatility-Normalized Distance Metrics (bounded in [-1, +1])
        atr_safe = max(atr, 1e-6)
        z_ema_separation = math.tanh((ema9 - ema21) / atr_safe)
        z_vwap_distance = math.tanh((price - vwap) / atr_safe)
        z_ema9_distance = math.tanh((price - ema9) / atr_safe)
        
        # EMA Slope Calculation (normalized over 3 periods by ATR)
        ema9_prev3 = cls.calculate_ema(closes[:-3], 9) if len(closes) > 12 else ema9
        ema_slope = math.tanh((ema9 - ema9_prev3) / (3 * atr_safe))
        
        # Volume Dynamics
        vol_window = volumes[-20:] if volumes else [1.0]
        n_vol = len(vol_window)
        mean_vol = sum(vol_window) / n_vol
        variance_vol = sum((x - mean_vol) ** 2 for x in vol_window) / n_vol
    @staticmethod
    def calculate_rsi_series(closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return [50.0] * len(closes)
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = [50.0] * period
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        first_rsi = 100.0 if avg_loss == 0.0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
        rsi_series.append(first_rsi)
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi = 100.0 if avg_gain > 0 else 50.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            rsi_series.append(rsi)
            
        return rsi_series

    @staticmethod
    def calculate_cvd_series(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> List[float]:
        cvd_series = []
        cum_delta = 0.0
        n = min(len(highs), len(lows), len(closes), len(volumes))
        for i in range(n):
            spread = highs[i] - lows[i]
            if spread > 1e-6:
                intra_ratio = (2.0 * closes[i] - highs[i] - lows[i]) / spread
                delta = volumes[i] * max(min(intra_ratio, 1.0), -1.0)
            else:
                delta = 0.0
            cum_delta += delta
            cvd_series.append(cum_delta)
        return cvd_series

    @staticmethod
    def find_nearest_fvg(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, Any]:
        """
        Finds the nearest active (unmitigated or fresh) Fair Value Gap (FVG).
        Bullish FVG: lows[i] > highs[i-2] (gap between highs[i-2] and lows[i])
        Bearish FVG: highs[i] < lows[i-2] (gap between highs[i] and lows[i-2])
        """
        n = len(closes)
        if n < 3:
            return {"active": False, "type": "NONE", "top": 0.0, "bottom": 0.0, "mid": closes[-1] if closes else 0.0}

        current_price = closes[-1]
        active_fvg = None
        
        lookback = min(n, 35)
        for i in range(n - 1, n - lookback + 1, -1):
            # Bullish FVG
            if lows[i] > highs[i - 2]:
                gap_top = lows[i]
                gap_bottom = highs[i - 2]
                gap_mid = (gap_top + gap_bottom) / 2.0
                is_mitigated = min(lows[i:]) < gap_bottom
                if not is_mitigated:
                    active_fvg = {
                        "active": True,
                        "type": "BULLISH",
                        "top": gap_top,
                        "bottom": gap_bottom,
                        "mid": gap_mid,
                        "bar_index": i
                    }
                    break
            # Bearish FVG
            elif highs[i] < lows[i - 2]:
                gap_top = lows[i - 2]
                gap_bottom = highs[i]
                gap_mid = (gap_top + gap_bottom) / 2.0
                is_mitigated = max(highs[i:]) > gap_top
                if not is_mitigated:
                    active_fvg = {
                        "active": True,
                        "type": "BEARISH",
                        "top": gap_top,
                        "bottom": gap_bottom,
                        "mid": gap_mid,
                        "bar_index": i
                    }
                    break

        if not active_fvg:
            recent_mid = (max(highs[-5:]) + min(lows[-5:])) / 2.0 if n >= 5 else current_price
            return {"active": False, "type": "NONE", "top": recent_mid, "bottom": recent_mid, "mid": recent_mid}

        return active_fvg

    @staticmethod
    def detect_divergences(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        rsi_series: List[float],
        cvd_series: List[float],
        window: int = 30
    ) -> Dict[str, Any]:
        """
        Detects regular bearish and bullish divergences between Price, RSI, and CVD.
        Regular Bearish Divergence (Call / Long Danger): Price Higher High, RSI/CVD Lower High.
        Regular Bullish Divergence (Put / Short Danger): Price Lower Low, RSI/CVD Higher Low.
        """
        n = len(closes)
        res = {
            "bearish_rsi_div": False,
            "bearish_cvd_div": False,
            "bullish_rsi_div": False,
            "bullish_cvd_div": False,
            "bearish_divergence": False,
            "bullish_divergence": False,
            "div_description": "None"
        }
        if n < 15:
            return res

        lookback = min(n, window)
        p_highs = highs[-lookback:]
        p_lows = lows[-lookback:]
        r_series = rsi_series[-lookback:]
        c_series = cvd_series[-lookback:]

        peaks = []
        for i in range(2, len(p_highs) - 2):
            if p_highs[i] >= p_highs[i-1] and p_highs[i] >= p_highs[i-2] and \
               p_highs[i] >= p_highs[i+1] and p_highs[i] >= p_highs[i+2]:
                peaks.append(i)

        troughs = []
        for i in range(2, len(p_lows) - 2):
            if p_lows[i] <= p_lows[i-1] and p_lows[i] <= p_lows[i-2] and \
               p_lows[i] <= p_lows[i+1] and p_lows[i] <= p_lows[i+2]:
                troughs.append(i)

        # Bearish Divergence detection
        if len(peaks) >= 2:
            p1, p2 = peaks[-2], peaks[-1]
            if p_highs[p2] > p_highs[p1]:
                if r_series[p2] < r_series[p1] - 1.5:
                    res["bearish_rsi_div"] = True
                if c_series[p2] < c_series[p1]:
                    res["bearish_cvd_div"] = True

        curr_high = p_highs[-1]
        if peaks:
            prev_peak = peaks[-1]
            if curr_high >= p_highs[prev_peak] and (len(p_highs) - 1 - prev_peak) >= 2:
                if r_series[-1] < r_series[prev_peak] - 1.5:
                    res["bearish_rsi_div"] = True
                if c_series[-1] < c_series[prev_peak]:
                    res["bearish_cvd_div"] = True

        # Bullish Divergence detection
        if len(troughs) >= 2:
            t1, t2 = troughs[-2], troughs[-1]
            if p_lows[t2] < p_lows[t1]:
                if r_series[t2] > r_series[t1] + 1.5:
                    res["bullish_rsi_div"] = True
                if c_series[t2] > c_series[t1]:
                    res["bullish_cvd_div"] = True

        curr_low = p_lows[-1]
        if troughs:
            prev_trough = troughs[-1]
            if curr_low <= p_lows[prev_trough] and (len(p_lows) - 1 - prev_trough) >= 2:
                if r_series[-1] > r_series[prev_trough] + 1.5:
                    res["bullish_rsi_div"] = True
                if c_series[-1] > c_series[prev_trough]:
                    res["bullish_cvd_div"] = True

        res["bearish_divergence"] = res["bearish_rsi_div"] or res["bearish_cvd_div"]
        res["bullish_divergence"] = res["bullish_rsi_div"] or res["bullish_cvd_div"]

        div_notes = []
        if res["bearish_rsi_div"]:
            div_notes.append("Bearish RSI Divergence")
        if res["bearish_cvd_div"]:
            div_notes.append("Bearish CVD Delta Divergence")
        if res["bullish_rsi_div"]:
            div_notes.append("Bullish RSI Divergence")
        if res["bullish_cvd_div"]:
            div_notes.append("Bullish CVD Delta Divergence")

        res["div_description"] = ", ".join(div_notes) if div_notes else "None"
        return res

    @staticmethod
    def detect_climax_candle(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        atr: float
    ) -> Dict[str, Any]:
        if len(closes) < 5:
            return {"is_climax": False, "is_bearish_sweep": False, "is_bullish_sweep": False, "is_churning": False, "reason": "Normal"}

        c = closes[-1]
        h = highs[-1]
        l = lows[-1]
        v = volumes[-1]
        bar_range = max(h - l, 1e-6)
        mean_vol = sum(volumes[-20:]) / len(volumes[-20:]) if volumes else 1.0

        upper_wick = h - c
        lower_wick = c - l
        upper_wick_pct = upper_wick / bar_range
        lower_wick_pct = lower_wick / bar_range

        # Sweep of Highs: Range >= 0.6 ATR, upper wick >= 45%, high volume
        is_bearish_sweep = (bar_range >= 0.5 * atr) and (upper_wick_pct >= 0.45) and (v >= 1.20 * mean_vol)
        
        # Sweep of Lows: Range >= 0.6 ATR, lower wick >= 45%, high volume
        is_bullish_sweep = (bar_range >= 0.5 * atr) and (lower_wick_pct >= 0.45) and (v >= 1.20 * mean_vol)

        # Churning: Volume > 2.0x avg with tight price move
        is_churning = (v > 2.0 * mean_vol) and (abs(c - closes[-2]) < 0.35 * atr) if len(closes) >= 2 else False

        is_climax = is_bearish_sweep or is_bullish_sweep or is_churning
        reasons = []
        if is_bearish_sweep:
            reasons.append("Liquidity Sweep Highs (Upper Wick Pinbar)")
        if is_bullish_sweep:
            reasons.append("Liquidity Sweep Lows (Lower Wick Pinbar)")
        if is_churning:
            reasons.append("Volume Climax / Churning Distribution")

        return {
            "is_climax": is_climax,
            "is_bearish_sweep": is_bearish_sweep,
            "is_bullish_sweep": is_bullish_sweep,
            "is_churning": is_churning,
            "upper_wick_pct": round(upper_wick_pct * 100, 1),
            "lower_wick_pct": round(lower_wick_pct * 100, 1),
            "reason": ", ".join(reasons) if reasons else "Normal candle structure"
        }

    @classmethod
    def extract_features(
        cls,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float]
    ) -> Dict[str, Any]:
        if not closes:
            return {}

        price = closes[-1]
        atr = cls.calculate_atr(highs, lows, closes, 14)
        ema9 = cls.calculate_ema(closes, 9)
        ema21 = cls.calculate_ema(closes, 21)
        ema50 = cls.calculate_ema(closes, min(50, len(closes)))
        vwap = cls.calculate_vwap(highs, lows, closes, volumes)
        rsi = cls.calculate_rsi(closes, 14)
        
        # Volatility-Normalized Distance Metrics (bounded in [-1, +1])
        atr_safe = max(atr, 1e-6)
        z_ema_separation = math.tanh((ema9 - ema21) / atr_safe)
        z_vwap_distance = math.tanh((price - vwap) / atr_safe)
        z_ema9_distance = math.tanh((price - ema9) / atr_safe)
        
        # EMA Slope Calculation (normalized over 3 periods by ATR)
        ema9_prev3 = cls.calculate_ema(closes[:-3], 9) if len(closes) > 12 else ema9
        ema_slope = math.tanh((ema9 - ema9_prev3) / (3 * atr_safe))
        
        # Volume Dynamics
        vol_window = volumes[-20:] if volumes else [1.0]
        n_vol = len(vol_window)
        mean_vol = sum(vol_window) / n_vol
        variance_vol = sum((x - mean_vol) ** 2 for x in vol_window) / n_vol
        std_vol = math.sqrt(variance_vol) if variance_vol > 0 else 1.0
        current_vol = volumes[-1] if volumes else 0.0
        z_volume = math.tanh((current_vol - mean_vol) / std_vol)
        
        # Bollinger Band Width & Compression
        price_window = closes[-20:] if len(closes) >= 20 else closes
        n_price = len(price_window)
        mean_price = sum(price_window) / n_price
        variance_price = sum((x - mean_price) ** 2 for x in price_window) / n_price
        std_price = math.sqrt(max(variance_price, 0.0))
        
        bb_upper = ema21 + (2.0 * std_price)
        bb_lower = ema21 - (2.0 * std_price)
        bb_width = (bb_upper - bb_lower) / max(ema21, 1e-4)
        is_compressed = bb_width < 0.006
        
        # Momentum Rate-of-Change
        if len(closes) >= 6 and closes[-6] != 0:
            roc_5 = ((closes[-1] - closes[-6]) / closes[-6]) * 100.0
        else:
            roc_5 = 0.0
        z_momentum = math.tanh(roc_5 / 3.0)

        rel_vol_pct = round(((current_vol - mean_vol) / mean_vol) * 100.0, 1) if mean_vol > 0 else 0.0

        # Fair Value Gap & Distance Metrics
        fvg_info = cls.find_nearest_fvg(highs, lows, closes)
        fvg_mid = fvg_info.get("mid", ema21)
        fvg_distance = abs(price - fvg_mid)
        fvg_distance_atr = round(fvg_distance / atr_safe, 2)
        z_fvg_distance = math.tanh(fvg_distance / (2.0 * atr_safe))
        
        # Series for Divergence and Momentum Exhaustion
        rsi_series = cls.calculate_rsi_series(closes, 14)
        cvd_series = cls.calculate_cvd_series(highs, lows, closes, volumes)
        cvd_current = cvd_series[-1] if cvd_series else 0.0
        
        divergence = cls.detect_divergences(highs, lows, closes, rsi_series, cvd_series)
        climax = cls.detect_climax_candle(highs, lows, closes, volumes, atr)

        return {
            "price": price,
            "atr": atr,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "vwap": vwap,
            "rsi": rsi,
            "z_ema_separation": z_ema_separation,
            "z_vwap_distance": z_vwap_distance,
            "z_ema9_distance": z_ema9_distance,
            "ema_slope": ema_slope,
            "z_volume": z_volume,
            "z_momentum": z_momentum,
            "bb_width": bb_width,
            "is_compressed": is_compressed,
            "relative_volume_pct": rel_vol_pct,
            "fvg_info": fvg_info,
            "fvg_distance_atr": fvg_distance_atr,
            "z_fvg_distance": z_fvg_distance,
            "cvd": cvd_current,
            "divergence": divergence,
            "climax": climax
        }


class OptionFeatureEngine:
    """Processes options chain metrics into normalized quantitative signals."""

    @staticmethod
    def extract_option_features(
        option_quote: Optional[Dict[str, Any]], 
        contract_meta: Optional[Dict[str, Any]], 
        spot_price: float,
        spot_atr: float = 1.0
    ) -> Dict[str, Any]:
        """
        Converts Polygon/Massive quotes and metadata into bounded quantitative metrics.
        Returns neutral fallback metrics if option data is missing or incomplete.
        """
        quote = option_quote or {}
        meta = contract_meta or {}

        bid = float(quote.get("bid_price") or 0.0)
        ask = float(quote.get("ask_price") or 0.0)
        last = float(quote.get("last_trade_price") or 0.0)
        
        # Implied Volatility (IV)
        iv_raw = quote.get("implied_volatility")
        iv = float(iv_raw) if iv_raw is not None else 0.0
        
        # Volume & Open Interest
        volume = int(meta.get("volume") or quote.get("day", {}).get("volume") or 0)
        open_interest = int(meta.get("open_interest") or 1)
        strike_price = float(meta.get("strike_price") or spot_price)
        expiration = meta.get("expiration_date", "Data unavailable")
        contract_ticker = meta.get("ticker", "Data unavailable")

        # 1. Spread Friction Metric (Slippage Risk)
        mid_price = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (last if last > 0 else 1.0)
        spread_abs = max(ask - bid, 0.0) if (bid > 0 and ask > 0) else 0.0
        spread_friction_pct = (spread_abs / mid_price) * 100.0 if mid_price > 0 else 100.0
        z_spread_friction = math.tanh(spread_friction_pct / 5.0)  # Bounded: 0 = tight, 1 = illiquid

        # 2. Institutional Flow Metric (Volume-to-Open-Interest Ratio)
        vol_oi_ratio = volume / max(open_interest, 1)
        z_vol_oi = math.tanh(vol_oi_ratio / 2.0)

        # 3. Normalized Strike Proximity (Moneyness vs ATR)
        strike_diff = strike_price - spot_price
        z_strike_distance = math.tanh(strike_diff / max(2.0 * spot_atr, 1e-4))

        # 4. Normalized Implied Volatility (Centered at 30% baseline)
        z_iv = math.tanh((iv - 0.30) / 0.15) if iv > 0 else 0.0

        # 5. Composite Option Score (0 - 100)
        # Rewards low spread friction, high volume/OI flow, and valid quotes
        if bid > 0 and ask > 0:
            liquidity_points = max(0.0, 50.0 - (spread_friction_pct * 5.0))
            flow_points = min(z_vol_oi * 30.0, 30.0)
            pricing_points = 20.0 if last > 0 else 10.0
            option_score = round(max(0.0, min(100.0, liquidity_points + flow_points + pricing_points)), 1)
        else:
            option_score = 0.0

        return {
            "contract_ticker": contract_ticker,
            "strike_price": strike_price,
            "expiration": expiration,
            "bid": bid,
            "ask": ask,
            "last": last,
            "mid_price": round(mid_price, 2),
            "spread_abs": round(spread_abs, 2),
            "spread_friction_pct": round(spread_friction_pct, 2),
            "z_spread_friction": z_spread_friction,
            "iv": iv,
            "z_iv": z_iv,
            "volume": volume,
            "open_interest": open_interest,
            "vol_oi_ratio": round(vol_oi_ratio, 2),
            "z_vol_oi": z_vol_oi,
            "z_strike_distance": z_strike_distance,
            "option_score": option_score,
            "is_liquid": spread_friction_pct < 10.0 and (bid > 0 and ask > 0)
        }


class UnifiedMarketEngine:
    """Orchestrates continuous equity features and option metrics simultaneously."""

    @classmethod
    def extract_all(
        cls,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        option_quote: Optional[Dict[str, Any]] = None,
        contract_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        tech_features = FeatureEngine.extract_features(highs, lows, closes, volumes)
        spot_price = tech_features.get("price", closes[-1] if closes else 0.0)
        spot_atr = tech_features.get("atr", 1.0)

        opt_features = OptionFeatureEngine.extract_option_features(
            option_quote=option_quote,
            contract_meta=contract_meta,
            spot_price=spot_price,
            spot_atr=spot_atr
        )

        return {
            **tech_features,
            "option": opt_features
        }