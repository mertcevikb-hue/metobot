from database.db_manager import DatabaseManager
from loguru import logger
import asyncio

class DynamicWeightManager:
    """Bot 1'in ürettiği ağırlıkları Bot 2 için canlı olarak yükler."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.current_weights = {}
        self.current_version = 0

    async def load_latest_weights(self):
        data = await self.db.get_latest_weights()
        if data:
            self.current_weights = data["weights"]
            self.current_version = data["version"]
        else:
            logger.warning("Veritabanında ağırlık bulunamadı. Varsayılanlar kullanılacak.")
            from config.weights import DEFAULT_WEIGHTS
            self.current_weights = DEFAULT_WEIGHTS
            
    async def check_for_updates(self):
        """Her döngüde yeni bir eğitim versiyonu var mı kontrol eder."""
        data = await self.db.get_latest_weights()
        if data and data["version"] > self.current_version:
            self.current_weights = data["weights"]
            self.current_version = data["version"]
            logger.success(f"Yeni Model Yüklendi (Hot-Reload) -> Versiyon: {self.current_version}")

    def get_weights(self) -> dict:
        return self.current_weights