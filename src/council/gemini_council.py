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
from datetime import datetime, timezone
from typing import Dict, Any, Optional

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


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

    def run_post_mortem(
        self,
        trade_data: Dict[str, Any],
        image_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes multi-persona post-mortem on a completed trade outcome.
        """
        symbol = trade_data.get("symbol", "UNKNOWN")
        outcome = trade_data.get("outcome", "LOSS")
        pnl = trade_data.get("realized_pnl", 0.0)
        r_mult = trade_data.get("realized_r", 0.0)
        strategy = trade_data.get("strategy", "SMC_ICT")

        prompt = f"""
You are the 3-Persona Quantitative Council governing an autonomous Deriv Synthetic Indices bot.
Conduct an unsparing, analytical post-mortem on the following trade outcome:

Symbol: {symbol}
Outcome: {outcome} (Realized PnL: ${pnl:.2f}, R-multiple: {r_mult:.2f}R)
Strategy Primary: {strategy}
Trade Details: {json.dumps(trade_data, default=str)}

Respond in valid JSON format with these exact keys:
1. "structural_diagnosis": (by Structure Auditor: Candlestick/liquidity/zone analysis)
2. "risk_criticism": (by Risk Critic: ATR spacing, trailing stop, and bankroll discipline)
3. "actionable_rule_mutation": (by Council Strategist: specific concrete algorithmic adjustment)
4. "weight_adjustment": (dict of framework delta adjustments between -0.05 and +0.05)
"""

        gemini_response_text = None
        diagnosis = ""
        criticism = ""
        mutation = ""
        weight_adj = {}

        if self.client:
            try:
                contents = [prompt]
                # If image exists and is valid size, attach for multimodal vision
                if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 100:
                    with open(image_path, "rb") as img_f:
                        img_bytes = img_f.read()
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

                response = self.client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=contents
                )
                gemini_response_text = response.text

                # Parse JSON block
                clean_json = gemini_response_text.strip()
                if "```json" in clean_json:
                    clean_json = clean_json.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_json:
                    clean_json = clean_json.split("```")[1].split("```")[0].strip()

                parsed = json.loads(clean_json)
                diagnosis = parsed.get("structural_diagnosis", "")
                criticism = parsed.get("risk_criticism", "")
                mutation = parsed.get("actionable_rule_mutation", "")
                weight_adj = parsed.get("weight_adjustment", {})
            except Exception as e:
                gemini_response_text = f"API fallback invoked: {str(e)}"

        # Robust heuristic fallback if API unavailable or parsing failed
        if not diagnosis:
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

        # Append to COUNCIL_NOTES.md
        self._append_to_notes(symbol, outcome, pnl, r_mult, strategy, diagnosis, criticism, mutation)

        return {
            "symbol": symbol,
            "outcome": outcome,
            "structural_diagnosis": diagnosis,
            "risk_criticism": criticism,
            "actionable_rule_mutation": mutation,
            "weight_adjustment": weight_adj,
            "raw_response": gemini_response_text or "Heuristic Engine"
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
