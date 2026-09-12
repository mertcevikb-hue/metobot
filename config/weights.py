# Bot 1 ilk kez çalışıp veritabanını doldurana kadar Bot 2'nin kullanacağı varsayılan SMC katsayıları
DEFAULT_WEIGHTS = {
    "BOS_confirmation": 25.0,
    "OB_freshness": 20.0,
    "FVG_proximity": 15.0,
    "liquidity_sweep": 25.0,
    "HTF_trend_alignment": 15.0,
    "execution_threshold": 75.0,  # İşleme girmek için minimum skor barajı
    "BOS_volume_threshold": 1.2   # Hacim ortalamasının min. kaç katı olmalı?
}