class PositionSizer:
    """Bot 2'nin ürettiği güven skoruna göre (0-100) işleme girilecek risk miktarını (Kasa %) ayarlar."""
    
    def __init__(self, base_risk_pct=0.01): # %1 varsayılan risk
        self.base_risk_pct = base_risk_pct

    def calculate_size(self, score: float, portfolio_balance: float) -> float:
        """Skor yükseldikçe alınan risk miktarını artırır (Kelly Criterion benzeri ölçekleme)."""
        
        if score < 75.0:
            return 0.0 # Yetersiz skor
            
        # 75 skorda 1x base risk, 95 skorda 2x base risk
        confidence_multiplier = (score - 70) / 15.0 
        confidence_multiplier = min(max(confidence_multiplier, 0.5), 2.0)
        
        risk_amount = portfolio_balance * (self.base_risk_pct * confidence_multiplier)
        return risk_amount