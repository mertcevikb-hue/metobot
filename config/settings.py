import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    API_PORT = int(os.getenv("API_PORT", 8000))
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    # Crypto Exchange Settings
    EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")
    API_KEY = os.getenv("EXCHANGE_API_KEY", "")
    SECRET_KEY = os.getenv("EXCHANGE_SECRET_KEY", "")
    USE_TESTNET = os.getenv("USE_TESTNET", "True").lower() == "true"
    
    # Polygon API Settings for Options Data
    POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
    POLYGON_BASE_URL = os.getenv("POLYGON_BASE_URL", "https://api.polygon.io")
    POLYGON_WS_URL = os.getenv("POLYGON_WS_URL", "wss://socket.polygon.io/options")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    
settings = Settings()