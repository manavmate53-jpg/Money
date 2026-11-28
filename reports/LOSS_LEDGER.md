# 🔴 LOSS LEDGER (POST-MORTEM & LOSS AUDIT REPOSITORY)
*Auto-generated from `losing_trades` SQLite table | Refreshed: 2026-11-28 09:45:59 UTC*
**Total Realized Loss**: `-$1.30` across `1` losing setups

| Trade ID | Symbol | Dir | Lot | Entry ➔ Exit | Realized R | Net Loss | Pre-Fund | Post-Fund | 5W Root Cause | What Improved? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TRD-20261122160735-1HZ7` | **1HZ75V** | BULLISH | 0.025 | 700.99 ➔ 648.88 | **-1.00R** | **-$1.30** | $100.00 | $98.70 | *WHO: SMC_ICT | WHAT: 1HZ75V Long 0.025 | WHEN: 45.4h hold | WHERE: 700.99->648.88 | WHY: The trade was a premature bullish entry following an SSL sweep that failed to hold. Price continued to decline significantly, indicating that the initial sweep was either incomplete, a fakeout, or the underlying market structure remained bearish, invalidating the bullish reversal premise. The entry lacked sufficient confirmation of a true structural shift.* | **Increase the minimum confirmation candles required after an SSL sweep to ensure stronger validation of a potential reversal. Specifically, require at least 2 bullish confirmation candles on the entry timeframe after a sweep before initiating a long position.** |
