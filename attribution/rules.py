class SMCRuleEvaluator:
    """
    Kapanan bir işlemin piyasa bağlamını analiz ederek hatanın/başarının 
    hangi SMC kuralından kaynaklandığını tespit eder.
    """

    @staticmethod
    def check_fake_bos(trade, market_context) -> bool:
        """
        Kural: Sahte Kırılım (Fake Break of Structure)
        Tanım: İşleme girilen BOS sinyali hacimsiz miydi veya kırılım sonrası
               mum gövdesi (body close) yapamayıp fitil olarak mı kaldı?
        """
        # Örnek mantık: Giriş sinyali BOS ise ve kırılım mumu hacmi ortalamanın altındaysa
        is_bos_trade = trade.get("signal_type") == "BOS"
        breakout_volume = market_context.get("entry_candle_volume", 0)
        avg_volume = market_context.get("avg_volume_20", 1)

        if is_bos_trade and (breakout_volume < avg_volume * 0.8):
            return True
        return False

    @staticmethod
    def check_liquidity_sweep_trap(trade, market_context) -> bool:
        """
        Kural: Likidite Süpürme Tuzağı (Liquidity Sweep Trap)
        Tanım: İşlem Stop Loss olduysa, SL seviyesi doğrudan bir EQH/EQL 
               (Eşit Tepe/Dip) seviyesinin hemen altında/üstünde miydi?
        """
        sl_price = trade.get("stop_loss")
        trade_side = trade.get("side") # "long" veya "short"
        closest_liquidity_pool = market_context.get("closest_liquidity_pool")

        if not closest_liquidity_pool:
            return False

        # Long işlemde SL, likidite havuzunun (SSL) çok yakınına konmuşsa fiyat burayı süpürmüş olabilir
        if trade_side == "long" and abs(sl_price - closest_liquidity_pool) / sl_price < 0.002:
            return True
        
        return False

    @staticmethod
    def check_unmitigated_fvg_failure(trade, market_context) -> bool:
        """
        Kural: FVG İhlali (Unmitigated FVG Failure)
        Tanım: İşlem bir FVG (Fair Value Gap) dönüşüne güvenilerek açıldı ancak
               fiyat FVG'yi hiç tepki vermeden (mitigasyon olmadan) tamamen geçti mi?
        """
        is_fvg_trade = trade.get("signal_type") == "FVG_ENTRY"
        fvg_zone = market_context.get("fvg_zone") # [alt_sinir, ust_sinir]
        mae = trade.get("mae") # İşlem açıkken görülen maksimum zarar seviyesi

        if is_fvg_trade and fvg_zone:
            # Fiyat FVG'nin tamamen dışına çıkıp SL olduysa
            if (trade.get("side") == "long" and mae < fvg_zone[0]) or \
               (trade.get("side") == "short" and mae > fvg_zone[1]):
                return True
        return False

    @staticmethod
    def check_trend_alignment_success(trade, market_context) -> bool:
        """
        Kural: Trend Uyumlu Başarı (Trend Alignment Success)
        Tanım: İşlem TP olduysa, ana rejimle (HTF Trend) sinyal yönü aynı mıydı?
        """
        trade_side = trade.get("side")
        htf_trend = market_context.get("htf_regime") # "bullish", "bearish", "ranging"

        if (trade_side == "long" and htf_trend == "bullish") or \
           (trade_side == "short" and htf_trend == "bearish"):
            return True
        return False