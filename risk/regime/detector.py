class RegimeDetector:
    """Mevcut piyasa koşullarını (Trend, Range, Volatil) tespit eder."""
    
    def detect(self, tick_data: dict) -> str:
        # Gerçek uygulamada ADX, ATR ve ardışık mum kapanışlarına bakılır.
        # Örnek Rejimler: "trending_bull", "trending_bear", "ranging", "high_volatility"
        
        volatility_index = 15 # Örnek ATR/VIX değeri
        
        if volatility_index > 40:
            return "high_volatility"
        
        # Basit trend mock mantığı
        if tick_data.get("close", 0) > tick_data.get("open", 0):
            return "trending_bull"
        else:
            return "ranging"