import uuid
from typing import List, Dict, Any, Optional
from loguru import logger

class PaperTrader:
    def __init__(self, initial_capital: float = 10000.0):
        self.capital = initial_capital
        logger.info(f"PaperTrader başlatıldı. Başlangıç Sermayesi: ${initial_capital:.2f}")

    async def execute_order(self, symbol: str, side: str, qty: float, price: float) -> Dict[str, Any]:
        logger.info(f"Paper Trade Emri: {side.upper()} {qty}x {symbol} @ ${price:.2f}")
        return {
            "order_id": f"ord_{uuid.uuid4().hex[:8]}",
            "symbol": symbol,
            "side": side.lower(),
            "price": price,
            "status": "FILLED"
        }

    async def simulate(self, data: List[Any], symbol: str = "SPY", strategy_func=None) -> List[Dict[str, Any]]:
        """
        Geçmiş mum verileri üzerinde al-sat simülasyonu çalıştırır ve
        Post-Mortem motorunun analiz edebileceği zenginlikte kapanan işlem raporları üretir.
        """
        logger.info(f"Paper trader simülasyon döngüsü çalıştırılıyor ({symbol})...")
        trades = []
        if not data or len(data) < 25:
            logger.warning(f"Simülasyon için yetersiz veri: {len(data) if data else 0} bar.")
            return trades

        # Veriyi standart candle sözlüklerine çevir
        candles = []
        for i, item in enumerate(data):
            if isinstance(item, dict):
                candles.append(item)
            elif isinstance(item, (int, float)):
                candles.append({
                    "open": float(item),
                    "high": float(item) * 1.002,
                    "low": float(item) * 0.998,
                    "close": float(item),
                    "volume": 1000000.0
                })

        n = len(candles)
        # Her 5-10 barda bir işlem simülasyonu başlat
        for i in range(20, n - 5, 5):
            entry_candle = candles[i]
            prev_candle = candles[i - 1]
            entry_price = entry_candle["close"]
            entry_vol = entry_candle.get("volume", 1000000.0)
            avg_vol_20 = sum(c.get("volume", 1000000.0) for c in candles[i-20:i]) / 20.0

            # Trend ve Sinyal yönü belirle
            is_long = entry_price >= prev_candle["close"]
            trade_side = "long" if is_long else "short"
            signal_type = "BOS" if (i % 2 == 0) else "FVG_ENTRY"

            # TP ve SL seviyeleri (Risk/Reward 1:2)
            sl_pct = 0.012  # %1.2 Zarar durdur
            tp_pct = 0.024  # %2.4 Kâr al

            if is_long:
                sl_price = round(entry_price * (1.0 - sl_pct), 2)
                tp_price = round(entry_price * (1.0 + tp_pct), 2)
                fvg_zone = [entry_price * 0.99, entry_price * 1.005]
                closest_pool = sl_price * 1.001 if (i % 3 == 0) else sl_price * 0.98
            else:
                sl_price = round(entry_price * (1.0 + sl_pct), 2)
                tp_price = round(entry_price * (1.0 - tp_pct), 2)
                fvg_zone = [entry_price * 0.995, entry_price * 1.01]
                closest_pool = sl_price * 0.999 if (i % 3 == 0) else sl_price * 1.02

            # Sonraki 5 bar boyunca fiyatın TP mi SL mi olduğunu takip et
            result = "TIMEOUT"
            exit_price = entry_price
            mae = entry_price  # Maksimum aleyhte hareket

            for future_idx in range(i + 1, min(i + 6, n)):
                future_bar = candles[future_idx]
                f_high = future_bar["high"]
                f_low = future_bar["low"]

                if is_long:
                    mae = min(mae, f_low)
                    if f_low <= sl_price:
                        result = "SL"
                        exit_price = sl_price
                        break
                    elif f_high >= tp_price:
                        result = "TP"
                        exit_price = tp_price
                        break
                else:
                    mae = max(mae, f_high)
                    if f_high >= sl_price:
                        result = "SL"
                        exit_price = sl_price
                        break
                    elif f_low <= tp_price:
                        result = "TP"
                        exit_price = tp_price
                        break

            # 20 EMA'ya göre HTF rejim tespiti
            htf_regime = "bullish" if entry_price > (sum(c["close"] for c in candles[i-20:i]) / 20.0) else "bearish"

            trade = {
                "id": f"tr_{symbol.lower()}_{i}",
                "trade_id": f"tr_{symbol.lower()}_{i}",
                "symbol": symbol,
                "side": trade_side,
                "signal_type": signal_type,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "result": result,  # "TP", "SL", "TIMEOUT"
                "mae": mae,
                "pnl": round((exit_price - entry_price) if is_long else (entry_price - exit_price), 2),
                "market_context": {
                    "entry_candle_volume": entry_vol,
                    "avg_volume_20": avg_vol_20,
                    "closest_liquidity_pool": closest_pool,
                    "fvg_zone": fvg_zone,
                    "htf_regime": htf_regime
                }
            }
            trades.append(trade)

        logger.info(f"✓ Simülasyon tamamlandı ({symbol}): {len(trades)} işlem üretildi.")
        return trades