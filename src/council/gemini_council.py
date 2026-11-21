"""
Gemini Multi-Persona Council & Reflexion Engine.
Powered by the official google-genai SDK.
Personas:
1. Chart & Structure Auditor (Multimodal chart image + candle structure)
2. Risk & Bankroll Critic (ATR invalidation, capital preservation, hold duration)
3. Council Strategist (Bayesian weight mutation & rule refinement logged to COUNCIL_NOTES.md)
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class MacroBiasSchema(BaseModel):
    symbol: str
    macro_bias: str = Field(description="BULLISH, BEARISH, or NEUTRAL")
    confidence_score: float = Field(description="Confidence between 0.0 and 1.0")
    primary_regime: str = Field(description="DIRECTIONAL_TREND, ROTATIONAL_RANGE, or HIGH_VOLATILITY_EXPANSION")
    h4_structural_zone: str = Field(description="Key H4 support/demand or resistance/supply zone")
    key_invalidation_level: float = Field(description="Price level where macro bias is invalidated")
    macro_target: float = Field(description="Primary higher timeframe liquidity target")
    summary_reasoning: str = Field(description="Detailed multi-timeframe structural reasoning")


class PostMortemSchema(BaseModel):
    structural_diagnosis: str = Field(description="Diagnosis by Structure Auditor")
    risk_criticism: str = Field(description="Criticism by Risk Critic")
    actionable_rule_mutation: str = Field(description="Concrete rule mutation by Council Strategist")
    weight_adjustment: Dict[str, float] = Field(description="Bayesian weight deltas between -0.05 and +0.05")


class TacticalSniperSchema(BaseModel):
    approved: bool = Field(description="Whether the M15/H1 sniper entry is approved")
    adjusted_lot_multiplier: float = Field(description="Scaling factor between 0.5 and 1.0")
    sniper_confidence: float = Field(description="Tactical confidence score")
    tactical_rationale: str = Field(description="Brief tactical reason for entry timing")


class GeminiCouncil:
    NOTES_FILE = "data/COUNCIL_NOTES.md"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

        os.makedirs(os.path.dirname(os.path.abspath(self.NOTES_FILE)), exist_ok=True)
        if not os.path.exists(self.NOTES_FILE):
            with open(self.NOTES_FILE, "w") as f:
                f.write("# GEMINI COUNCIL EPISODIC REFLEXION JOURNAL\n\nPersistent lessons, post-mortems, and strategy mutations.\n\n---\n")

    def _call_with_retry(self, model: str, contents: list, config: Any = None, max_retries: int = 3) -> Any:
        """Executes Gemini API call with exponential backoff retry."""
        if not self.client:
            return None
        last_err = None
        for attempt in range(max_retries):
            try:
                if config:
                    return self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config
                    )
                else:
                    return self.client.models.generate_content(
                        model=model,
                        contents=contents
                    )
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise last_err

    def evaluate_macro_regime(
        self,
        symbol: str,
        technical_summary: Dict[str, Any],
        image_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Invokes gemini-2.5-pro with multimodal vision to determine authoritative H4 macro bias.
        """
        prompt = f"""
You are the Macro & Regime Desk Lead for an institutional Deriv Synthetic Indices quantitative bot.
Analyze the following multi-timeframe structural metrics and chart image for {symbol}:

Technical Telemetry:
{json.dumps(technical_summary, default=str)}

Provide a strict, authoritative macro bias evaluation adhering to the schema.
"""
        if self.client:
            try:
                contents = [prompt]
                if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 100:
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MacroBiasSchema,
                    temperature=0.2
                )
                response = self._call_with_retry("gemini-2.5-pro", contents, config=config)
                return json.loads(response.text)
            except Exception as e:
                pass

        # Heuristic fallback if offline or rate-limited
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
            "summary_reasoning": f"Deterministic structural bias {bias} derived from {regime} and SMC swing alignment."
        }

    def validate_tactical_sniper(
        self,
        symbol: str,
        macro_bias: str,
        m15_setup: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Uses fast gemini-2.5-flash for 1-hour tactical screening of M15 entries against H4 bias.
        """
        prompt = f"""
You are the Tactical Execution Risk Manager.
Evaluate whether this intraday M15 setup aligns cleanly with the H4 Macro Bias ({macro_bias}) on {symbol}:

M15 Setup:
{json.dumps(m15_setup, default=str)}

Respond with structured tactical validation.
"""
        if self.client:
            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TacticalSniperSchema,
                    temperature=0.1
                )
                response = self._call_with_retry("gemini-2.5-flash", [prompt], config=config)
                return json.loads(response.text)
            except Exception:
                pass

        # Heuristic tactical check
        direction = m15_setup.get("direction", "NEUTRAL")
        aligned = (direction == macro_bias) or macro_bias == "NEUTRAL"
        return {
            "approved": aligned,
            "adjusted_lot_multiplier": 1.0 if aligned else 0.5,
            "sniper_confidence": 0.85 if aligned else 0.40,
            "tactical_rationale": "Aligned with macro bias" if aligned else "Counter-trend setup penalised"
        }

    def run_post_mortem(
        self,
        trade_data: Dict[str, Any],
        image_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes multi-persona post-mortem on a completed trade outcome using gemini-2.5-pro.
        """
        symbol = trade_data.get("symbol", "UNKNOWN")
        outcome = trade_data.get("outcome", "LOSS")
        pnl = trade_data.get("realized_pnl", 0.0)
        r_mult = trade_data.get("realized_r", 0.0)
        strategy = trade_data.get("strategy", "SMC_ICT")

        prompt = f"""
You are the 3-Persona Quantitative Council governing an autonomous Deriv Synthetic Indices bot:
1. Chart & Structure Auditor: Candlestick/liquidity/zone analysis
2. Risk & Bankroll Critic: ATR spacing, trailing stop, and bankroll discipline
3. Council Strategist: Specific concrete algorithmic adjustment and Bayesian weight deltas

Conduct an unsparing post-mortem on this resolved trade:
Symbol: {symbol}
Outcome: {outcome} (Realized PnL: ${pnl:.2f}, R-multiple: {r_mult:.2f}R)
Strategy Primary: {strategy}
Trade Details: {json.dumps(trade_data, default=str)}
"""
        if self.client:
            try:
                contents = [prompt]
                if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 100:
                    with open(image_path, "rb") as img_f:
                        img_bytes = img_f.read()
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PostMortemSchema,
                    temperature=0.3
                )
                response = self._call_with_retry("gemini-2.5-pro", contents, config=config)
                parsed = json.loads(response.text)
                diagnosis = parsed.get("structural_diagnosis", "")
                criticism = parsed.get("risk_criticism", "")
                mutation = parsed.get("actionable_rule_mutation", "")
                weight_adj = parsed.get("weight_adjustment", {})

                self._append_to_notes(symbol, outcome, pnl, r_mult, strategy, diagnosis, criticism, mutation)
                return {
                    "symbol": symbol,
                    "outcome": outcome,
                    "structural_diagnosis": diagnosis,
                    "risk_criticism": criticism,
                    "actionable_rule_mutation": mutation,
                    "weight_adjustment": weight_adj,
                    "raw_response": response.text
                }
            except Exception:
                pass

        # Robust heuristic fallback
        if outcome == "WIN":
            diagnosis = f"Structure confirmed: {strategy} displacement cleanly targeted opposing liquidity."
            criticism = "Risk respected: Stop loss remained protected outside ATR noise corridor."
            mutation = f"Reinforce {strategy} weighting on {symbol} during current regime."
            weight_adj = {strategy: 0.03}
        else:
            diagnosis = f"Structural invalidation: Price wicked into unmitigated pool before reversing."
            criticism = "Risk alert: Entry occurred too early without waiting for absorption confirmation."
            mutation = f"Require minimum 2-candle confirmation after liquidity sweep on {symbol}."
            weight_adj = {strategy: -0.03}

        self._append_to_notes(symbol, outcome, pnl, r_mult, strategy, diagnosis, criticism, mutation)

        return {
            "symbol": symbol,
            "outcome": outcome,
            "structural_diagnosis": diagnosis,
            "risk_criticism": criticism,
            "actionable_rule_mutation": mutation,
            "weight_adjustment": weight_adj,
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
        mutation: str
    ):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry_text = f"""
### [{now}] {symbol} — {outcome} (${pnl:+.2f} / {r_mult:+.2f}R)
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
