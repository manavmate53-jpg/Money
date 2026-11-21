# 📈 30-DAY CALIBRATION SCOREBOARD & VAULT TRACKER
*Auto-updated daily by the Investment Council Committee (`daily_council_committee.yml`)*  
*Last Refresh: **2026-11-21 19:12:39 UTC***

---

## 🎯 1. GRADUATION GATE STATUS: `LIVE_READY`
| Metric | Current Value | Required Live Gate | Status |
| :--- | :--- | :--- | :--- |
| **Calibration Window** | Day **52** / 30 | 30 full calendar days | ✅ PASSED |
| **Resolved Sample** | **40** trades | $\\ge 30$ resolved trades | ✅ PASSED |
| **Profit Factor** | **14.00** | $\\ge 1.50$ | ✅ PASSED |
| **Max Drawdown** | **3.7%** | $\\le 10.0\%$ | ✅ PASSED |
| **Rolling Win Rate** | **87.5%** | Baseline $> 50.0\%$ | ✅ HEALTHY |

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
