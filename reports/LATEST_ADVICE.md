# 🏛 CAPITAL ADVISOR — $100 LIVE EXECUTION BLUEPRINT
*Auto-updated every hour by the Execution & Risk Desk (`hourly_execution_desk.yml`)*  
*Last Refresh: **2026-11-28 12:03:31 UTC***

---

## 📊 1. 30-DAY CALIBRATION & VAULT SCOREBOARD
- **Readiness State**: `TRAINING_CALIBRATION (Day 7/30)`
- **Simulation Epoch**: `#1`
- **Resolved Sample**: `1` trades (`0W / 1L`)
- **Rolling Win Rate**: **0.0%**
- **Profit Factor**: **0.00** (Target: $\ge 1.50$)
- **Max Drawdown**: **1.3%** (Target: $\le 10.0\%$)
- **Active Bankroll**: **$98.70**
- **Locked Profit Vault ("Profit Brought Home")**: **$0.00**

---

## 🎯 2. #1 RECOMMENDED INSTRUMENT
- **Instrument**: **`R_100`** *(Ranked #1 for $100 capital)*
- **Directional Bias**: **BEARISH**
- **Market Regime**: `DIRECTIONAL_TREND`
- **Primary Strategy**: `SMC_ICT`
- **Champion Rule Alignment**: *PrimeFX Pure Price-Action + Liquidity Playbook*

---

## 🎫 3. EXACT EXECUTION TICKET
| Parameter | Exact Value | Notes |
| :--- | :--- | :--- |
| **Recommended Lot Size** | **`0.52 lots`** | Granular nano-lot execution |
| **Dollar Risk ($)** | **`$1.49`** | **1.49%** of $100 equity |
| **Required Margin** | `$0.19` | Leverage 1:1000 |
| **Entry Zone** | **`362.54 - 363.62`** | Structural ATR-calibrated entry band |
| **Stop Loss (SL)** | **`365.40`** | 2.86 points distance |
| **Take Profit 1 (TP1)** | **`355.39`** | Planned R:R: **1:2.5** |
| **Take Profit 2 (TP2)** | **`353.29`** | Major liquidity pool |
| **Cover-Risk-to-Zero** | **`359.68 (+1.0R displacement)`** | Ratchet SL to Break-Even at $+1.0R$ |
| **Estimated Hold Time** | **1.0 hours (1 bars)** | Based on $\text{ATR}_{14}$ velocity |

---

## 🔍 4. CONFLUENCE SIGNALS
- Structure: Bearish LL/LH structure progression
- M15 MSS: Bearish body close shift below 361.20
- POI: H1 BULLISH_OB [352.84-358.57] (CLEAR_DIRECT_POI)
- POI: H4 BEARISH_FVG [364.37-381.76] (CLEAR_DIRECT_POI)
- Candlestick Trigger: BEARISH_ENGULFING
- k-NN Analogs (k=20): 50.0% win rate, +0.75R avg

---

## ⚠️ 5. GEMINI COUNCIL WARNING / EDGE NOTE
> *[1HZ75V] Increase the minimum confirmation candles required after an SSL sweep to ensure stronger validation of a potential reversal. Specifically, require at least 2 bullish confirmation candles on the entry timeframe after a sweep before initiating a long position.*


### Why Other Indices Were Disqualified on $100 Capital
- **CRASH1000**: DISQUALIFIED_ON_$100: Minimum lot 0.2 on CRASH1000 with 21.67 pt SL risks $4.33 (4.3%), exceeding $2.00 hard cap.

