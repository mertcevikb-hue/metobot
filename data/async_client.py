import ccxt.async_support as ccxt
from config.settings import settings
from loguru import logger

class AsyncExchangeClient:
    """Gerçek piyasa verisi çekmek ve işlem yapmak için CCXT yöneticisi."""
    
    def __init__(self):
        exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
        self.exchange = exchange_class({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        
        if settings.USE_TESTNET:
            self.exchange.set_sandbox_mode(True)
            logger.info(f"{settings.EXCHANGE_ID} testnet (sandbox) aktif.")

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100):
        try:
            return await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as e:
            logger.error(f"Veri çekme hatası ({symbol}): {e}")
            return []

    async def close(self):
        await self.exchange.close()