# 🔴 LOSS LEDGER (POST-MORTEM & LOSS AUDIT REPOSITORY)
*Auto-generated from `losing_trades` SQLite table | Refreshed: 2026-11-24 13:30:08 UTC*
**Total Realized Loss**: `-$1.30` across `1` losing setups

| Trade ID | Symbol | Dir | Lot | Entry ➔ Exit | Realized R | Net Loss | Pre-Fund | Post-Fund | 5W Root Cause | What Improved? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TRD-20261122160735-1HZ7` | **1HZ75V** | BULLISH | 0.025 | 700.99 ➔ 648.88 | **-1.00R** | **-$1.30** | $100.00 | $98.70 | *Structural invalidation: Price wicked into unmitigated pool before reversing. | Risk alert: Entry occurred too early without waiting for absorption confirmation.* | **Require minimum 2-candle confirmation after liquidity sweep on 1HZ75V.** |
