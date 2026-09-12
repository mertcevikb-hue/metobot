from fastapi import APIRouter, Depends
from database.db_manager import DatabaseManager

router = APIRouter()
db = DatabaseManager()

@router.get("/signals")
async def get_recent_signals(limit: int = 20):
    """Bot 2'nin ürettiği son canlı sinyalleri getirir."""
    signals = await db.get_recent_signals(limit=limit)
    return {"status": "success", "data": signals}

@router.get("/weights")
async def get_active_weights():
    """Bot 1'in eğittiği ve şu an aktif olan model katsayılarını getirir."""
    weights = await db.get_latest_weights()
    return {"status": "success", "data": weights}