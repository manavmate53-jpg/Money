# 📈 30-DAY CALIBRATION SCOREBOARD & VAULT TRACKER
*Auto-updated daily by the Investment Council Committee (`daily_council_committee.yml`)*  
*Last Refresh: **2026-11-22 10:10:13 UTC***

---

## 🎯 1. GRADUATION GATE STATUS: `LIVE_READY`
| Metric | Current Value | Required Live Gate | Status |
| :--- | :--- | :--- | :--- |
| **Calibration Window** | Day **53** / 30 | 30 full calendar days | ✅ PASSED |
| **Resolved Sample** | **40** trades | $\\ge 30$ resolved trades | ✅ PASSED |
| **Profit Factor** | **14.00** | $\\ge 1.50$ | ✅ PASSED |
| **Max Drawdown** | **3.7%** | $\\le 10.0\%$ | ✅ PASSED |
| **Rolling Win Rate** | **87.5%** | Baseline $> 50.0\%$ | ✅ HEALTHY |
| **Monte Carlo Pass Prob** | **100.0%** | $\\ge 65.0\%$ (+20% before -15%) | ✅ PASSED |
| **Monte Carlo P95 Worst DD** | **2.0R** | $\\le 10.0R$ | ✅ RESILIENT |
| **Sharpe / Sortino Ratio** | **30.90 / 31.05** | Sharpe $\\ge 1.5$ / Sortino $\\ge 2.0$ | ✅ OPTIMAL |
| **Net Real Return** | **$+0.00** | Payouts minus Resets | 🟢 PROFITABLE |

### Remaining Graduation Gate Blockers:
✅ All criteria fulfilled for live deployment!

---

## 🏦 2. CAPITAL & PROFIT-LOCK VAULT SCOREBOARD
- **Active Trading Bankroll**: **`$100.00`**
- **Locked Profit Vault ("Profit Brought Home")**: **`$0.00`**
- **Total Combined Capital**: **`$100.00`**
- **Current Simulation Epoch**: `#1` (Auto-reset sandbox on equity $< $15.00)

---

## 🛡️ 3. INSTITUTIONAL RISK CONTROLS ACTIVE
- **Maximum Risk per Trade**: 1.5% (hard cap 2.0%)
- **Cover Risk to Zero**: Move SL to Break-Even at $+1.0R$ (Fabio Valentini)
- **Session Walk-Away**: Stop new entries upon $+3.0R$ realized gain (Freddy Sarento)
- **Small-Account Protection**: Automatically filters out $0.01$ lot V75 on $\$100$ balances to preserve capital
