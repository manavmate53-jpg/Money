# 🏛 CAPITAL ADVISOR — $100 LIVE EXECUTION BLUEPRINT
*Auto-updated every hour by the Execution & Risk Desk (`hourly_execution_desk.yml`)*  
*Last Refresh: **2026-11-25 19:08:00 UTC***

---

## 📊 1. 30-DAY CALIBRATION & VAULT SCOREBOARD
- **Readiness State**: `TRAINING_CALIBRATION (Day 5/30)`
- **Simulation Epoch**: `#1`
- **Resolved Sample**: `1` trades (`0W / 1L`)
- **Rolling Win Rate**: **0.0%**
- **Profit Factor**: **0.00** (Target: $\ge 1.50$)
- **Max Drawdown**: **1.3%** (Target: $\le 10.0\%$)
- **Active Bankroll**: **$98.70**
- **Locked Profit Vault ("Profit Brought Home")**: **$0.00**

---

## 🎯 2. #1 RECOMMENDED INSTRUMENT
- **Instrument**: **`STEPINDEX`** *(Ranked #1 for $100 capital)*
- **Directional Bias**: **BULLISH**
- **Market Regime**: `DIRECTIONAL_TREND`
- **Primary Strategy**: `SMC_ICT`
- **Champion Rule Alignment**: *PrimeFX Liquidity Raid: PDL Swept + 4-Bar Hold*

---

## 🎫 3. EXACT EXECUTION TICKET
| Parameter | Exact Value | Notes |
| :--- | :--- | :--- |
| **Recommended Lot Size** | **`0.15 lots`** | Granular nano-lot execution |
| **Dollar Risk ($)** | **`$1.49`** | **1.49%** of $100 equity |
| **Required Margin** | `$1.10` | Leverage 1:1000 |
| **Entry Zone** | **`7352.00 - 7355.50`** | Structural ATR-calibrated entry band |
| **Stop Loss (SL)** | **`7345.57`** | 9.93 points distance |
| **Take Profit 1 (TP1)** | **`7380.33`** | Planned R:R: **1:2.5** |
| **Take Profit 2 (TP2)** | **`7385.20`** | Major liquidity pool |
| **Cover-Risk-to-Zero** | **`7365.43 (+1.0R displacement)`** | Ratchet SL to Break-Even at $+1.0R$ |
| **Estimated Hold Time** | **2.1 hours (2 bars)** | Based on $\text{ATR}_{14}$ velocity |

---

## 🔍 4. CONFLUENCE SIGNALS
- Liquidity: PDL swept at 7352.70 (4-candle hold)
- POI: H1 BULLISH_IFVG [7352.00-7355.50] (IDM_SWEPT_CONFIRMED)
- POI: H4 BEARISH_IFVG [7365.40-7365.50] (NO_IDM_NEEDED)
- Pure PA: Support-to-Resistance polarity flip @ 7354.90
- k-NN Analogs (k=20): 45.0% win rate, +0.57R avg

---

## ⚠️ 5. GEMINI COUNCIL WARNING / EDGE NOTE
> *[1HZ75V] Increase the minimum confirmation candles required after an SSL sweep to ensure stronger validation of a potential reversal. Specifically, require at least 2 bullish confirmation candles on the entry timeframe after a sweep before initiating a long position.*


### Why Other Indices Were Disqualified on $100 Capital
- **1HZ100V**: DISQUALIFIED_ON_$100: Minimum lot 0.2 on 1HZ100V with 40.85 pt SL risks $8.17 (8.2%), exceeding $2.00 hard cap.
- **JD25**: DISQUALIFIED_ON_$100: Minimum lot 0.01 on JD25 with 1113.61 pt SL risks $11.14 (11.1%), exceeding $2.00 hard cap.
- **CRASH1000**: DISQUALIFIED_ON_$100: Minimum lot 0.2 on CRASH1000 with 64.82 pt SL risks $12.96 (13.0%), exceeding $2.00 hard cap.

