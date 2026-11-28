# Project Instructions & Autonomous Deriv Bot Protocol (`manavmate53-jpg/Money`)

## Mandatory `Status` / `status` Response Format
When the user sends a message matching `Status` or `status` (case-insensitive, with or without trailing punctuation) or invokes `/status`:
1. Immediately run `git pull --ff-only origin main && python main.py --cycle status` in the workspace terminal.
2. Output the resulting 5 Markdown tables directly in your response.
3. Do NOT convert the tables back into bullet lists or long prose paragraphs. Keep any commentary to at most 1–2 concise lines below the tables only if a critical alert or action item exists.

## Engineering Guardrails
- Keep 100% pure OHLC price-action / liquidity / POI logic (zero volume profile or orderflow assumptions).
- Enforce `$100` risk discipline (`<= $1.50–$2.00` per trade, max 1 concurrent open trade, `$60.00` hard drawdown circuit breaker, 2h anti-revenge cooldown, 6h pending TTL evaluated strictly against M15 candles inside `[opened_at, opened_at + 6h]`).
- Use `src/reporting/status_table.py` (`python main.py --cycle status`) as the single source of truth for live telemetry formatting.
