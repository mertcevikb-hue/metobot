from loguru import logger
from typing import List, Dict, Any
import copy

class WeightOptimizer:
    def __init__(self):
        # Varsayılan başlangıç ağırlıkları (0-100 skorlaması için katsayılar)
        self.baseline_weights = {
            "BOS_confirmation": 25.0,
            "OB_freshness": 20.0,
            "FVG_proximity": 15.0,
            "Liquidity_sweep": 25.0,
            "HTF_trend_alignment": 15.0,
            "BOS_volume_threshold": 1.2, # Hacim eşiği çarpanı
            "execution_threshold": 75.0  # İşleme girme barajı
        }

    def calculate_new_weights(self, attribution_logs: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Geçmiş işlemlerdeki kayıp ve kazanç nedenlerini toplayarak
        ideal ağırlıkları hesaplar.
        """
        if not attribution_logs:
            return self.baseline_weights

        logger.info(f"{len(attribution_logs)} adet post-mortem logu analiz ediliyor...")
        
        # Olay frekanslarını say
        reason_counts = {
            "FAKE_BOS": 0,
            "LIQUIDITY_SWEEP_TRAP": 0,
            "UNMITIGATED_FVG_FAILURE": 0,
            "HTF_TREND_ALIGNMENT": 0
        }

        for log in attribution_logs:
            reason = log.get("primary_reason")
            if reason in reason_counts:
                reason_counts[reason] += 1

        total_logs = len(attribution_logs)
        new_weights = copy.deepcopy(self.baseline_weights)

        # 1. FAKE BOS (Sahte Kırılım) çok yaşanıyorsa hacim onayını zorlaştır ve BOS ağırlığını düşür
        if reason_counts["FAKE_BOS"] / total_logs > 0.20:
            new_weights["BOS_confirmation"] *= 0.8  # Ağırlığı %20 azalt
            new_weights["BOS_volume_threshold"] *= 1.15 # Hacim eşiğini %15 artır
            logger.debug("Optimizasyon: FAKE_BOS oranı yüksek. BOS ağırlığı düşürüldü.")

        # 2. Likidite Tuzakları (Stop Patlatma) çok yaşanıyorsa Sweep onayının ağırlığını artır
        if reason_counts["LIQUIDITY_SWEEP_TRAP"] / total_logs > 0.15:
            new_weights["Liquidity_sweep"] *= 1.2 # Sweep şartının önemini %20 artır
            new_weights["execution_threshold"] = min(new_weights["execution_threshold"] + 2, 85.0)
            logger.debug("Optimizasyon: Likidite tuzağı yoğun. Sweep ağırlığı artırıldı.")

        # 3. FVG İhlalleri çoksa, FVG giriş stratejisinin etkisini azalt
        if reason_counts["UNMITIGATED_FVG_FAILURE"] / total_logs > 0.20:
            new_weights["FVG_proximity"] *= 0.75
            logger.debug("Optimizasyon: FVG ihlali yüksek. FVG ağırlığı düşürüldü.")

        # 4. Trend uyumu başarılıysa trend katsayısını ödüllendir
        if reason_counts["HTF_TREND_ALIGNMENT"] / total_logs > 0.30:
            new_weights["HTF_trend_alignment"] *= 1.15
            logger.debug("Optimizasyon: HTF trend uyumu yüksek. Trend ağırlığı artırıldı.")

        # Ağırlıkları normalize et (Katsayıların toplamı 100 olmalı)
        self._normalize_weights(new_weights)
        
        self.baseline_weights = new_weights
        return new_weights

    def _normalize_weights(self, weights: Dict[str, float]):
        """Skorlama bileşenlerinin toplamının her zaman 100 olmasını sağlar."""
        core_features = ["BOS_confirmation", "OB_freshness", "FVG_proximity", "Liquidity_sweep", "HTF_trend_alignment"]
        total = sum(weights.get(k, 0.0) for k in core_features)
        
        if total > 0:
            for k in core_features:
                weights[k] = round((weights.get(k, 0.0) / total) * 100.0, 2)

    def optimize(self, attribution_logs=None):
        return self.calculate_new_weights(attribution_logs or [])