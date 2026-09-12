from loguru import logger
from typing import Dict, Any
from .rules import SMCRuleEvaluator

class PostMortemEngine:
    def __init__(self):
        self.evaluator = SMCRuleEvaluator()

    def evaluate(self, trade: Dict[str, Any], market_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Kapanan işlemi analiz eder ve bir sebeplendirme raporu (Attribution Log) döndürür.
        """
        result = trade.get("result") # "TP", "SL", "BE" (Break Even), "TIMEOUT"
        trade_id = trade.get("id")
        
        logger.debug(f"Post-Mortem Analizi Başlıyor: İşlem {trade_id} Sonuç: {result}")

        attribution = {
            "trade_id": trade_id,
            "symbol": trade.get("symbol"),
            "side": trade.get("side"),
            "result": result,
            "primary_reason": "UNKNOWN",
            "tags": [],
            "weight_adjustment_hint": {} # ML modelinin anlayacağı ağırlık güncelleme sinyali
        }

        if result == "SL":
            # Zararla kapanan işlemlerin nedenini bul (Hata Atfetme)
            if self.evaluator.check_fake_bos(trade, market_context):
                attribution["primary_reason"] = "FAKE_BOS"
                attribution["tags"].append("Low Volume Breakout")
                attribution["weight_adjustment_hint"] = {"BOS_volume_threshold": "+10%"}
                
            elif self.evaluator.check_liquidity_sweep_trap(trade, market_context):
                attribution["primary_reason"] = "LIQUIDITY_SWEEP_TRAP"
                attribution["tags"].append("Poor SL Placement")
                attribution["weight_adjustment_hint"] = {"Sweep_confirmation_weight": "+15%"}
                
            elif self.evaluator.check_unmitigated_fvg_failure(trade, market_context):
                attribution["primary_reason"] = "UNMITIGATED_FVG_FAILURE"
                attribution["tags"].append("Ignored FVG")
                attribution["weight_adjustment_hint"] = {"FVG_entry_weight": "-10%"}

            else:
                attribution["primary_reason"] = "MARKET_NOISE"
                attribution["tags"].append("Unexplained Variance")

        elif result == "TP":
            # Kârla kapanan işlemlerin nedenini bul (Başarı Atfetme)
            if self.evaluator.check_trend_alignment_success(trade, market_context):
                attribution["primary_reason"] = "HTF_TREND_ALIGNMENT"
                attribution["tags"].append("Trend Following")
                attribution["weight_adjustment_hint"] = {"HTF_trend_weight": "+5%"}
            else:
                attribution["primary_reason"] = "COUNTER_TREND_SUCCESS"
                attribution["tags"].append("Mean Reversion")

        # Zaman aşımı (Timeout) durumları için analiz
        elif result == "TIMEOUT":
            attribution["primary_reason"] = "LOW_VOLATILITY_REGIME"
            attribution["tags"].append("Dead Market")
            attribution["weight_adjustment_hint"] = {"Veto_low_volatility": "ENABLE"}

        logger.info(f"İşlem {trade_id} Sebeplendirmesi: {attribution['primary_reason']}")
        return attribution