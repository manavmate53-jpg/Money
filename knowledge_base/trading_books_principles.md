# FOUNDATIONAL TRADING BOOKS: DISTILLED LAWS & SYNTHETIC INDEX PRINCIPLES

---

## 1. *How to Trade Synthetic Indices* — Vince Stanzione
- **Audited CSPRNG Dynamics**: Deriv synthetic indices are powered by an audited Cryptographically Secure Pseudo-Random Number Generator with fixed mathematical volatility. They are 100% free of news shocks, economic announcements, earnings releases, and interbank liquidity vacuums.
- **Tick Speed Segmentation**:
  - `1s` variants (e.g. `Volatility 25 (1s)`, `Volatility 50 (1s)`) update exactly once every second (86,400 ticks per day), exhibiting smoother micro-trends and tighter nano-lot granularity (`0.005` min lot).
  - Standard variants (e.g. `Volatility 75`, `Volatility 100`) update every 2 seconds (43,200 ticks per day) with higher point-value multiples (`0.01` min lot on V75 equals $10 per 1,000 points).
- **Asymmetric Instruments**:
  - **Boom Indices**: Steady downward drift punctuated by sudden violent upward spikes. Never short Boom indices without hard stop loss.
  - **Crash Indices**: Steady upward drift punctuated by sudden violent downward crashes. Never buy Crash indices without hard stop loss.
  - **Step Index**: Discrete binary steps of 0.1 points with equal probability, rewarding tight Mean Reversion and Order Block reaction trading.

---

## 2. *Trading in the Zone* — Mark Douglas
- **Probabilistic Mindset**: Any single trade outcome is completely random and independent, while a series of 50 to 100 trades produces a statistically predictable edge.
- **Five Fundamental Truths**:
  1. Anything can happen on any given tick.
  2. You do not need to know what will happen next to make money.
  3. There is a random distribution between wins and losses for any set of edge-defining variables.
  4. An edge is nothing more than an indication of a higher probability of one thing happening over another.
  5. Every moment in the market is unique.
- **Rule of Mechanical Execution**: No hesitation, no regret, no front-running, and no altering stops during an active trade.

---

## 3. *The New Trading for a Living* — Dr. Alexander Elder
- **Elder's Triple Screen Trading System**:
  - **Screen 1 (Tide - H4)**: Identifies the macro market wave using One-Time Framing and 20 EMA trend direction.
  - **Screen 2 (Wave - H1)**: Identifies pullbacks counter to the macro trend (e.g. oversold retracements in an uptrend) using Supply/Demand zones and FVG retests.
  - **Screen 3 (Ripple - M15)**: Identifies precision entry timing via Candlestick confirmation (Pin Bar, Engulfing, MSS).
- **The 2% Rule**: Maximum capital risked on any individual trade shall never exceed **1.5% to 2.0%** of total account equity.
- **The 6% Monthly Drawdown Circuit Breaker**: If total cumulative monthly drawdown reaches **6%**, all trading halts immediately for 14 calendar days to preserve capital.

---

## 4. *Market Wizards* — Jack D. Schwager
- **Asymmetric Risk-to-Reward Ratio**: Enforce a mandatory minimum planned R:R of **>= 1:2.5** (Risk $1 to make $2.50+). A 40% win-rate system with a 1:2.5 R:R yields an expectancy of:
  $$E = (0.40 \times 2.5) - (0.60 \times 1.0) = +0.40R \text{ per trade}$$
- **Aggressive Loser Cutting**: Never hope, pray, or widen a stop loss. Invalidation price must be determined before entry is placed and respected unconditionally.
- **Pyramiding & Anti-Martingale**: Never add to a losing position. Only scale into winning trades when risk is protected to zero.

---

## 5. *The Mathematics of Money Management* — Ralph Vince
- **Fractional Kelly Criterion**:
  $$f^* = \frac{p(b + 1) - 1}{b}$$
  where $p$ is win probability and $b$ is reward-to-risk ratio. In high-volatility synthetic environments, full Kelly risks catastrophic ruin; the system must enforce **Quarter-Kelly ($f^* / 4$)** with a hard ceiling of 2.0% risk.
- **Risk of Ruin (RoR)**: To guarantee an RoR $< 0.1\%$, position size must remain strictly under $2.0\%$ per trade, ensuring an account can withstand a 15-loss consecutive drawdown cluster without bankruptcy.

---

## 6. *Mind Over Markets* — James Dalton
- **Auction Market Theory**: Markets exist solely to facilitate trade between buyers and sellers, moving from balance (rotational bracket) to imbalance (directional discovery).
- **Value Area Calculation**:
  - **Value Area**: The price range where 70% of historical volume/time was spent during the session.
  - **Value Area High (VAH)** & **Value Area Low (VAL)**: Critical support and resistance extremes.
  - **Point of Control (POC)**: The price level with maximum market acceptance.
- **Day Types**:
  - **Trend Day**: Unidirectional expansion with consecutive higher values; trade strictly with momentum.
  - **Non-Trend / Bracket Day**: Price is contained within previous range; fade extremes at VAH/VAL and do not chase breakouts.
