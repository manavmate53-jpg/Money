"""
Gemini Multi-Persona Council & Reflexion Engine.
Powered by the official google-genai SDK with strict Pydantic schemas (zero Dict fields).
Personas:
1. Chart & Structure Auditor (Multimodal BEFORE/AFTER chart images + candle structure)
2. Risk & Bankroll Critic (ATR invalidation, trailing stops, capital preservation)
3. Council Strategist (Typed weight adjustments & machine-enforced rule mutations)
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class MacroAnalysisSchema(BaseModel):
    symbol: str
    macro_bias: str = Field(description="BULLISH, BEARISH, or NEUTRAL")
    confidence_score: float = Field(description="Confidence between 0.0 and 1.0")
    primary_regime: str = Field(description="DIRECTIONAL_TREND, ROTATIONAL_RANGE, HIGH_VOLATILITY_EXPANSION, or RANDOM_WALK_CHOP")
    h4_structural_zone: str = Field(description="Key H4 support/demand or resistance/supply zone")
    key_invalidation_level: float = Field(description="Price level where macro bias is invalidated")
    macro_target: float = Field(description="Primary higher timeframe liquidity target")
    summary_reasoning: str = Field(description="Detailed multi-timeframe structural reasoning")


# Backward-compatible alias
MacroBiasSchema = MacroAnalysisSchema


class TradeValidationSchema(BaseModel):
    approved: bool = Field(description="Whether the M15/H1 sniper entry is approved")
    adjusted_lot_multiplier: float = Field(description="Scaling factor between 0.5 and 1.0")
    sniper_confidence: float = Field(description="Tactical confidence score between 0.0 and 1.0")
    tactical_rationale: str = Field(description="Concise tactical rationale for entry timing and sweep confirmation")


# Backward-compatible alias
TacticalSniperSchema = TradeValidationSchema


class StrategyWeightDelta(BaseModel):
    strategy_name: str = Field(description="Exact framework name: SMC_ICT, SUPPLY_DEMAND, or PRICE_ACTION_AUCTION")
    delta: float = Field(description="Suggested Bayesian weight delta between -0.05 and +0.05")
    reason: str = Field(description="Concise rationale linking outcome to strategy adjustment")


class PostMortemSchema(BaseModel):
    structural_diagnosis: str = Field(description="Diagnosis by Chart & Structure Auditor")
    risk_criticism: str = Field(description="Criticism by Risk & Bankroll Critic")
    actionable_rule_mutation: str = Field(description="Concrete rule mutation by Council Strategist")
    recommended_min_sweep_candles: int = Field(default=2, description="Minimum confirmation candles required after sweep (1-4)")
    recommended_min_rr: float = Field(default=2.0, description="Minimum planned R:R threshold (1.5-4.0)")
    weight_adjustments: List[StrategyWeightDelta] = Field(description="Typed list of strategy weight adjustments")


class GeminiCouncil:
    NOTES_FILE = "data/COUNCIL_NOTES.md"
    SCREENING_MODELS = ["gemini-2.5-flash", "gemini-3-flash-preview"]
    COUNCIL_PRO_MODELS = ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-flash-preview"]
    RETRY_BACKOFFS = [2.0, 5.0, 12.0]

    def __init__(self, api_key: Optional[str] = None, notes_file: Optional[str] = None):
        self.NOTES_FILE = notes_file or self.NOTES_FILE
        self.api_key = api_key if api_key is not None else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self.client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {type(e).__name__}: {e}")
                self.client = None

        os.makedirs(os.path.dirname(os.path.abspath(self.NOTES_FILE)), exist_ok=True)
        if not os.path.exists(self.NOTES_FILE):
            with open(self.NOTES_FILE, "w") as f:
                f.write("# GEMINI COUNCIL EPISODIC REFLEXION JOURNAL\n\nPersistent lessons, post-mortems, and strategy mutations.\n\n---\n")

    @staticmethod
    def _is_permanent_model_unavailable(err: Exception) -> bool:
        msg = str(err)
        if "404" in msg and "NOT_FOUND" in msg:
            return True
        if "limit: 0" in msg and ("FreeTier" in msg or "RESOURCE_EXHAUSTED" in msg):
            return True
        return False

    def _call_with_retry(
        self,
        model: str,
        contents: list,
        config: Any = None,
        max_retries: int = 3,
        fallback_models: Optional[List[str]] = None
    ) -> tuple[Any, str]:
        """
        Executes Gemini API call with structured logging, [2s, 5s, 12s] backoff on transient
        429/5xx errors, and automatic fallback across healthy models when Pro quotas/models are unavailable.
        Returns (response, model_used).
        """
        if not self.client:
            raise RuntimeError("Gemini client is not initialized (missing API key or google-genai SDK)")

        candidate_models = [model]
        for fb in (fallback_models or []):
            if fb not in candidate_models:
                candidate_models.append(fb)

        last_err: Optional[Exception] = None
        for model_name in candidate_models:
            for attempt in range(max_retries):
                try:
                    kwargs: Dict[str, Any] = {"model": model_name, "contents": contents}
                    if config is not None:
                        kwargs["config"] = config
                    resp = self.client.models.generate_content(**kwargs)
                    return resp, model_name
                except Exception as e:
                    last_err = e
                    logger.error(f"Gemini API error [{model_name}] (attempt {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
                    if self._is_permanent_model_unavailable(e):
                        break  # Immediately advance to fallback model without wasting backoff time
                    if attempt < max_retries - 1:
                        wait_s = self.RETRY_BACKOFFS[min(attempt, len(self.RETRY_BACKOFFS) - 1)]
                        time.sleep(wait_s)

        raise last_err if last_err else RuntimeError("Gemini API failed with unknown error")

    def evaluate_macro_regime(
        self,
        symbol: str,
        technical_summary: Dict[str, Any],
        image_path: Optional[str] = None,
        model_name: str = "gemini-2.5-flash"
    ) -> Dict[str, Any]:
        """
        Evaluates H4/H1 macro bias using Gemini multimodal vision + quantitative telemetry.
        Uses gemini-2.5-flash for per-symbol macro scans by default to stay fast within rate limits.
        """
        prompt = f"""
You are the Macro & Regime Desk Lead for an institutional Deriv Synthetic Indices trading desk.
Analyze the quantitative telemetry and attached chart image for {symbol}:

Technical Telemetry:
{json.dumps(technical_summary, default=str)}

Respond strictly adhering to MacroAnalysisSchema with concrete structural levels and non-generic reasoning.
"""
        if self.client:
            try:
                contents: list = [prompt]
                if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 500:
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MacroAnalysisSchema,
                    temperature=0.2
                )
                fallback = self.SCREENING_MODELS if "flash" in model_name else self.COUNCIL_PRO_MODELS
                response, used_model = self._call_with_retry(model_name, contents, config=config, fallback_models=fallback)
                parsed = json.loads(response.text)
                parsed["ai_model"] = used_model
                return parsed
            except Exception as e:
                logger.error(f"Macro evaluation fell back to heuristic for {symbol}: {type(e).__name__}: {e}")

        # Deterministic offline fallback
        regime = technical_summary.get("price_action", {}).get("regime", "ROTATIONAL_RANGE")
        trend = technical_summary.get("smc_ict", {}).get("structure", {}).get("trend", "NEUTRAL")
        curr_price = technical_summary.get("current_price", 2500.0)
        atr = technical_summary.get("price_action", {}).get("atr_14", 20.0) or 20.0

        bias = "BULLISH" if trend == "BULLISH" else ("BEARISH" if trend == "BEARISH" else "NEUTRAL")
        invalidation = curr_price - (2.0 * atr) if bias == "BULLISH" else curr_price + (2.0 * atr)
        target = curr_price + (4.0 * atr) if bias == "BULLISH" else curr_price - (4.0 * atr)

        return {
            "symbol": symbol,
            "macro_bias": bias,
            "confidence_score": 0.80 if bias != "NEUTRAL" else 0.50,
            "primary_regime": regime,
            "h4_structural_zone": f"Zone around {curr_price:.2f}",
            "key_invalidation_level": round(invalidation, 2),
            "macro_target": round(target, 2),
            "summary_reasoning": f"Deterministic structural bias {bias} derived from {regime} and SMC swing alignment.",
            "ai_model": "heuristic_fallback"
        }

    def validate_tactical_sniper(
        self,
        symbol: str,
        macro_bias: str,
        m15_setup: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Screens tactical M15/H1 setups against authoritative H4 Macro Bias using gemini-2.5-flash.
        """
        prompt = f"""
You are the Tactical Execution Risk Manager.
Evaluate whether this intraday M15/H1 setup aligns cleanly with the authoritative H4 Macro Bias ({macro_bias}) on {symbol}:

Setup Telemetry:
{json.dumps(m15_setup, default=str)}

Reject counter-trend or low-confirmation entries when H4 macro bias is opposite or NEUTRAL without structure shift.
Respond strictly using TradeValidationSchema.
"""
        if self.client:
            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TradeValidationSchema,
                    temperature=0.1
                )
                response, used_model = self._call_with_retry(
                    "gemini-2.5-flash", [prompt], config=config, fallback_models=self.SCREENING_MODELS
                )
                parsed = json.loads(response.text)
                parsed["ai_model"] = used_model
                return parsed
            except Exception as e:
                logger.error(f"Tactical sniper validation fell back for {symbol}: {type(e).__name__}: {e}")

        direction = m15_setup.get("direction", "NEUTRAL")
        aligned = (direction == macro_bias) and macro_bias in ("BULLISH", "BEARISH")
        return {
            "approved": aligned,
            "adjusted_lot_multiplier": 1.0 if aligned else 0.5,
            "sniper_confidence": 0.85 if aligned else 0.40,
            "tactical_rationale": "Aligned with H4 macro bias" if aligned else f"Counter-macro or neutral H4 bias ({macro_bias})",
            "ai_model": "heuristic_fallback"
        }

    def run_post_mortem(
        self,
        trade_data: Dict[str, Any],
        image_path: Optional[str] = None,
        before_image_path: Optional[str] = None,
        after_image_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes multi-persona multimodal post-mortem comparing BEFORE & AFTER charts.
        Uses typed PostMortemSchema (StrategyWeightDelta list — zero Dict fields).
        Prefers Pro model chain with clean fallback to multimodal Flash when Pro quota is constrained.
        """
        symbol = trade_data.get("symbol", "UNKNOWN")
        outcome = trade_data.get("outcome", "LOSS")
        pnl = trade_data.get("realized_pnl", 0.0)
        r_mult = trade_data.get("realized_r", 0.0)
        strategy = trade_data.get("strategy", "SMC_ICT")

        prompt = f"""
You are the 3-Persona Quantitative Council governing an autonomous Deriv Synthetic Indices bot:
1. Chart & Structure Auditor: Inspect BEFORE & AFTER chart visuals, sweep zones, and liquidity invalidation
2. Risk & Bankroll Critic: Audit ATR spacing, break-even timing, entry zone width, and pending order freshness
3. Council Strategist: Prescribe concrete machine-enforceable rule mutations (sweep confirmation candles, min R:R) and typed Bayesian strategy weight deltas

Conduct an unsparing, specific post-mortem on this resolved trade (explain Who, What, When, Where, Why):
Symbol: {symbol}
Outcome: {outcome} (Realized PnL: ${pnl:.2f}, R-multiple: {r_mult:.2f}R)
Strategy Primary: {strategy}
Trade Details: {json.dumps(trade_data, default=str)}
"""
        if self.client:
            try:
                contents: list = [prompt]
                attached_paths = []
                for p in [before_image_path, after_image_path, image_path]:
                    if p and p not in attached_paths and os.path.exists(p) and os.path.getsize(p) > 500:
                        with open(p, "rb") as img_f:
                            contents.append(types.Part.from_bytes(data=img_f.read(), mime_type="image/png"))
                        attached_paths.append(p)

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PostMortemSchema,
                    temperature=0.25
                )
                response, used_model = self._call_with_retry(
                    "gemini-3.1-pro-preview",
                    contents,
                    config=config,
                    fallback_models=self.COUNCIL_PRO_MODELS
                )
                parsed = json.loads(response.text)
                diagnosis = parsed.get("structural_diagnosis", "")
                criticism = parsed.get("risk_criticism", "")
                mutation = parsed.get("actionable_rule_mutation", "")
                raw_deltas = parsed.get("weight_adjustments", []) or []
                weight_adj = {
                    item["strategy_name"]: float(item["delta"])
                    for item in raw_deltas
                    if isinstance(item, dict) and "strategy_name" in item and "delta" in item
                }
                if not weight_adj:
                    weight_adj = {strategy: 0.03 if outcome == "WIN" else -0.03}

                self._append_to_notes(symbol, outcome, pnl, r_mult, strategy, diagnosis, criticism, mutation, used_model)
                return {
                    "symbol": symbol,
                    "outcome": outcome,
                    "structural_diagnosis": diagnosis,
                    "risk_criticism": criticism,
                    "actionable_rule_mutation": mutation,
                    "recommended_min_sweep_candles": int(parsed.get("recommended_min_sweep_candles", 2)),
                    "recommended_min_rr": float(parsed.get("recommended_min_rr", 2.0)),
                    "weight_adjustments": raw_deltas,
                    "weight_adjustment": weight_adj,
                    "ai_model": used_model,
                    "raw_response": f"[{used_model}] {response.text}"
                }
            except Exception as e:
                logger.error(f"Post-mortem fell back to heuristic for {symbol}: {type(e).__name__}: {e}")

        # Robust heuristic fallback
        if outcome == "WIN":
            diagnosis = f"Structure confirmed: {strategy} displacement cleanly targeted opposing liquidity."
            criticism = "Risk respected: Stop loss remained protected outside ATR noise corridor."
            mutation = f"Reinforce {strategy} weighting on {symbol} during current regime."
            weight_adj = {strategy: 0.03}
        else:
            diagnosis = f"Structural invalidation: Price wicked into unmitigated pool before reversing on {symbol}."
            criticism = "Risk alert: Entry occurred too early without waiting for 2-candle absorption confirmation."
            mutation = f"Require minimum 2-candle confirmation after liquidity sweep on {symbol}."
            weight_adj = {strategy: -0.03}

        self._append_to_notes(symbol, outcome, pnl, r_mult, strategy, diagnosis, criticism, mutation, "heuristic_fallback")

        return {
            "symbol": symbol,
            "outcome": outcome,
            "structural_diagnosis": diagnosis,
            "risk_criticism": criticism,
            "actionable_rule_mutation": mutation,
            "recommended_min_sweep_candles": 2,
            "recommended_min_rr": 2.0,
            "weight_adjustments": [{"strategy_name": k, "delta": v, "reason": mutation} for k, v in weight_adj.items()],
            "weight_adjustment": weight_adj,
            "ai_model": "heuristic_fallback",
            "raw_response": "Heuristic Engine"
        }

    def _append_to_notes(
        self,
        symbol: str,
        outcome: str,
        pnl: float,
        r_mult: float,
        strategy: str,
        diagnosis: str,
        criticism: str,
        mutation: str,
        model_tag: str = "gemini"
    ):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry_text = f"""
### [{now}] {symbol} — {outcome} (${pnl:+.2f} / {r_mult:+.2f}R) · `{model_tag}`
- **Strategy**: `{strategy}`
- **Structure Auditor**: {diagnosis}
- **Risk Critic**: {criticism}
- **Council Strategist Rule Mutation**: {mutation}

---
"""
        try:
            with open(self.NOTES_FILE, "a") as f:
                f.write(entry_text)
        except Exception:
            pass
