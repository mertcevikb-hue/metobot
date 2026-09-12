class ScoreCalculator:
    """Modüllerden gelen teknik ve opsiyon özelliklerini ağırlıklarla çarparak güven skoru üretir."""
    
    def compute(self, features: dict, regime: str, weights: dict) -> float:
        base_score = 0.0
        max_possible = 0.0

        import math
        # 1. Temel Teknik Özellikleri (Technical Features) katsayılarıyla çarp
        for feature_key, weight_value in weights.items():
            if feature_key in features:
                val = features[feature_key]
                if isinstance(val, (int, float)) and not math.isnan(val) and not math.isinf(val):
                    if isinstance(weight_value, (int, float)) and not math.isnan(weight_value):
                        base_score += (val * weight_value)
            
            # Toplam ağırlık limitini (max 100) hesapla
            if isinstance(weight_value, (int, float)) and not math.isnan(weight_value) and feature_key != "execution_threshold":
                max_possible += weight_value

        if max_possible <= 0:
            return 0.0

        # 2. 0-100 arasına normalize et
        normalized_score = (base_score / max_possible) * 100.0

        # 3. Rejim çarpanları uygula
        if regime == "trending_bull" and features.get("is_long_setup", 0) == 1.0:
            normalized_score *= 1.1 # Trend yönündeyse %10 bonus
        elif regime == "ranging":
            if features.get("liquidity_sweep", 0) > 0:
                normalized_score *= 1.15 # Ranging piyasada likidite kapma çok değerlidir

        # 4. Opsiyon Verisi Doğrulaması ve Skor Ayarlaması (YENİ EKLENEN KISIM)
        opt_features = features.get("option", {})
        if opt_features:
            # Likidite Cezası (Spread Friction): Alış-satış makası çok açıksa skoru düşür
            spread_friction = opt_features.get("spread_friction_pct", 100.0)
            if spread_friction > 10.0:  # %10'dan fazla spread varsa (kayma riski)
                normalized_score -= (spread_friction * 1.5)
            
            # Kurumsal Akış (Institutional Flow) Bonusu: V/OI oranı yüksekse
            z_vol_oi = opt_features.get("z_vol_oi", 0.0)
            if z_vol_oi > 0:
                normalized_score += (z_vol_oi * 10.0) # Max +10 puan
            
            # Zımni Volatilite (IV) Çarpanı
            z_iv = opt_features.get("z_iv", 0.0)
            if regime == "VOLATILITY_COMPRESSION" and z_iv < 0:
                normalized_score *= 1.1 # IV düşükken (prim ucuzken) almak avantajlıdır
            elif z_iv > 0.8:
                normalized_score -= 5.0 # Aşırı yüksek IV (pahalı prim) cezası
                
            # Eğer opsiyon tamamen likitsiz veya geçersizse işlemi iptal sınırına çek
            if not opt_features.get("is_liquid", True):
                normalized_score = min(normalized_score, 45.0) # Execution Eşiğinin altına it

        # 5. Tavan ve Taban limitleri
        if math.isnan(normalized_score) or math.isinf(normalized_score):
            return 0.0
        return max(0.0, min(round(float(normalized_score), 2), 100.0))