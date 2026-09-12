"""
Metobot Institutional Veto Filter (v2.0)
Protects against chasing momentum, FVG distance extension, CVD/RSI divergences,
liquidity sweep traps, and options 0DTE / IV crush risks.
"""
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime, timezone, time
from loguru import logger


class VetoFilter:
    """
    Skor ne kadar yüksek olursa olsun işlemin açılmasını engelleyen kurumsal güvenlik kapısı.
    4 Ana Katman:
    1. Chasing Momentum & FVG/OB Mesafe Kapısı
    2. CVD ve RSI Divergence (Tükenme) Kapısı
    3. Likidite Süpürme & Hacim Zirvesi (Sweep/Climax) Kapısı
    4. Opsiyon 0DTE Gün Sonu Zamanı & IV Crush / Spread Kapısı
    """

    MAX_FVG_DISTANCE_ATR: float = 2.2      # FVG'den max 2.2 ATR uzaklaşmaya izin ver
    MAX_VWAP_DISTANCE_Z: float = 1.85      # VWAP z-distance sınırı
    MAX_OPTION_SPREAD_PCT: float = 12.0    # Opsiyon bid-ask makas limiti (%12)
    CUTOFF_0DTE_TIME_GMT3 = time(20, 30)   # 20:30 GMT+3 = 13:30 ET (0DTE için seans sonu tehlike saati)

    @classmethod
    def check_chasing_distance(cls, features: Dict[str, Any], side: str) -> Tuple[bool, Optional[str]]:
        """
        Fiyatın ana FVG (Fair Value Gap) veya taban bölgesinden aşırı uzaklaşıp
        uzaklaşmadığını (Risk/Reward çöküşü) denetler.
        """
        fvg_dist = float(features.get("fvg_distance_atr", 0.0))
        z_vwap = float(features.get("z_vwap_distance", 0.0))
        z_ema9 = float(features.get("z_ema9_distance", 0.0))

        is_long = side in ("LONG", "BUY")

        if is_long:
            if fvg_dist > cls.MAX_FVG_DISTANCE_ATR:
                return True, f"Chasing Momentum Veto: Fiyat FVG/Taban bölgesinden {fvg_dist:.2f} ATR aşırı uzaklaştı (Limit: {cls.MAX_FVG_DISTANCE_ATR:.1f} ATR). Zirveden giriş engellendi."
            if z_vwap > cls.MAX_VWAP_DISTANCE_Z or z_ema9 > 1.8:
                return True, f"Chasing Momentum Veto: Fiyat VWAP/EMA9 ortalamasından parabolik saptı (Z-VWAP: {z_vwap:.2f})."
        else:
            if fvg_dist > cls.MAX_FVG_DISTANCE_ATR:
                return True, f"Chasing Momentum Veto: Fiyat FVG/Tepe bölgesinden aşağı yönde {fvg_dist:.2f} ATR aşırı uzaklaştı. Düşüşün dibinden Short engellendi."
            if z_vwap < -cls.MAX_VWAP_DISTANCE_Z or z_ema9 < -1.8:
                return True, f"Chasing Momentum Veto: Fiyat VWAP/EMA9 ortalamasından aşağı yönde parabolik saptı (Z-VWAP: {z_vwap:.2f})."

        return False, None

    @classmethod
    def check_divergence_exhaustion(cls, features: Dict[str, Any], side: str) -> Tuple[bool, Optional[str]]:
        """
        Fiyat yeni tepe/dip yaparken RSI veya Cumulative Volume Delta (CVD)
        üzerinde oluşan uyumsuzlukları (Divergence) denetler.
        """
        div = features.get("divergence", {})
        if not div:
            return False, None

        is_long = side in ("LONG", "BUY")

        if is_long and div.get("bearish_divergence"):
            desc = div.get("div_description", "Bearish Divergence")
            return True, f"Momentum Exhaustion Veto: Fiyat yükselirken {desc} tespit edildi. Alıcı tükenmesi / dağıtım (Distribution) riski."

        if not is_long and div.get("bullish_divergence"):
            desc = div.get("div_description", "Bullish Divergence")
            return True, f"Momentum Exhaustion Veto: Fiyat düşerken {desc} tespit edildi. Satıcı tükenmesi / toplama (Accumulation) riski."

        return False, None

    @classmethod
    def check_liquidity_sweep_climax(cls, features: Dict[str, Any], side: str) -> Tuple[bool, Optional[str]]:
        """
        Kurumsal likidite süpürme (Liquidity Sweep) ve sahte kırılım (Fakeout) tuzaklarını denetler.
        """
        climax = features.get("climax", {})
        if not climax:
            return False, None

        is_long = side in ("LONG", "BUY")

        if is_long and climax.get("is_bearish_sweep"):
            upper_wick = climax.get("upper_wick_pct", 50.0)
            return True, f"Liquidity Sweep Veto: Zirvede uzun üst fitil (%{upper_wick:.0f}) ve hacim süpürmesi (Pinbar Trap) tespit edildi."

        if not is_long and climax.get("is_bullish_sweep"):
            lower_wick = climax.get("lower_wick_pct", 50.0)
            return True, f"Liquidity Sweep Veto: Dipte uzun alt fitil (%{lower_wick:.0f}) ve hacim süpürmesi (Pinbar Trap) tespit edildi."

        if climax.get("is_churning"):
            return True, "Volume Climax Veto: Anormal yüksek hacim karşısında fiyat ilerleyemiyor (Churning/Emilim Tuzağı)."

        return False, None

    @classmethod
    def check_options_time_and_iv(
        cls,
        option_data: Optional[Dict[str, Any]],
        dt: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        0DTE zaman erimesi (Theta Decay) ve Volatilite Çöküşü (IV Crush) risklerini denetler.
        """
        if not option_data or not isinstance(option_data, dict):
            return False, None

        # 1. 0DTE Zaman Bandı Kontrolü
        from engine.market_hours import MarketSchedule
        exp_date_str = option_data.get("expiration_date")
        s_dt = MarketSchedule.get_current_session_time(dt)
        current_date_str = s_dt.strftime("%Y-%m-%d")

        is_zero_dte = bool(option_data.get("is_0dte")) or (exp_date_str == current_date_str) or (option_data.get("dte") == 0)

        if is_zero_dte:
            current_time = s_dt.time()
            if current_time >= cls.CUTOFF_0DTE_TIME_GMT3:
                return True, (
                    f"0DTE Theta Risk Veto: 0DTE kontratlarda {cls.CUTOFF_0DTE_TIME_GMT3.strftime('%H:%M')} GMT+3 "
                    f"(13:30 ET) sonrası pozisyon açılamaz. Kapanış öncesi parabolik theta çöküşü riski."
                )

        # 2. Spread / Likidite Kontrolü
        spread_friction = option_data.get("spread_friction_pct")
        if spread_friction is None:
            try:
                raw_spread = option_data.get("bid_ask_spread")
                raw_prem = option_data.get("premium")
                if raw_spread and raw_prem and isinstance(raw_spread, str) and isinstance(raw_prem, str):
                    sp_val = float(raw_spread.replace("$", "").strip())
                    pr_val = float(raw_prem.replace("$", "").strip())
                    if pr_val > 0:
                        spread_friction = (sp_val / pr_val) * 100.0
            except Exception:
                spread_friction = None

        if spread_friction is not None and spread_friction > cls.MAX_OPTION_SPREAD_PCT:
            return True, f"Illiquid Option Veto: Opsiyon alış-satış makası (%{spread_friction:.1f}) güvenli %{cls.MAX_OPTION_SPREAD_PCT:.1f} sınırını aşıyor."

        # 3. IV Crush Kontrolü
        iv_raw = option_data.get("iv")
        iv_val = 0.0
        if isinstance(iv_raw, (int, float)):
            iv_val = float(iv_raw)
        elif isinstance(iv_raw, str) and "%" in iv_raw:
            try:
                iv_val = float(iv_raw.replace("%", "").strip()) / 100.0
            except Exception:
                pass

        if iv_val > 1.25:
            return True, f"IV Crush Veto: Opsiyon Implied Volatility (%{iv_val * 100:.1f}) aşırı primli. Düzeltmede ani prim buharlaşması riski."

        return False, None

    @classmethod
    def evaluate_veto(
        cls,
        features: Dict[str, Any],
        decision_side: str,
        option_data: Optional[Dict[str, Any]] = None,
        dt: Optional[datetime] = None
    ) -> Tuple[bool, List[str]]:
        """
        Merkezi Veto Kontrolü:
        Tüm güvenlik kapılarını sırayla çalıştırır. Herhangi biri takılırsa veto eder.
        """
        if decision_side not in ("LONG", "SHORT", "BUY", "SELL"):
            return False, []

        veto_reasons = []

        # Kapı 1: Chasing Momentum & FVG Mesafe Kontrolü
        v1, r1 = cls.check_chasing_distance(features, decision_side)
        if v1 and r1:
            veto_reasons.append(r1)

        # Kapı 2: Divergence / Uyumsuzluk Kontrolü
        v2, r2 = cls.check_divergence_exhaustion(features, decision_side)
        if v2 and r2:
            veto_reasons.append(r2)

        # Kapı 3: Likidite Süpürme / Climax Tuzağı Kontrolü
        v3, r3 = cls.check_liquidity_sweep_climax(features, decision_side)
        if v3 and r3:
            veto_reasons.append(r3)

        # Kapı 4: Opsiyon 0DTE & IV Kontrolü
        if option_data:
            v4, r4 = cls.check_options_time_and_iv(option_data, dt)
            if v4 and r4:
                veto_reasons.append(r4)

        is_vetoed = len(veto_reasons) > 0
        if is_vetoed:
            logger.warning(f"🛡️ VETO APPLIED for {decision_side}: {'; '.join(veto_reasons)}")

        return is_vetoed, veto_reasons

    def is_vetoed(self, tick_data: dict, current_regime: str) -> bool:
        """Geriye dönük uyumluluk (Legacy compatibility)."""
        if current_regime == "high_volatility":
            logger.warning("Veto: Yüksek volatilite rejimi (Haber/Makro etki).")
            return True
            
        spread = tick_data.get("high", 0) - tick_data.get("low", 0)
        price = tick_data.get("close", 1.0)
        if price > 0 and (spread / price) > 0.08:
            logger.warning(f"Veto: Anormal mum spread boyutu ({spread:.2f}).")
            return True
            
        return False