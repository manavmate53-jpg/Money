# MONEY: AUTONOMOUS DERIV SYNTHETIC INDICES TRADING BOT & GEMINI COUNCIL ADVISOR

[![Hourly Execution Desk](https://github.com/manavmate53-jpg/Money/actions/workflows/hourly_execution_desk.yml/badge.svg)](https://github.com/manavmate53-jpg/Money/actions/workflows/hourly_execution_desk.yml)
[![Macro Regime Desk](https://github.com/manavmate53-jpg/Money/actions/workflows/macro_regime_desk.yml/badge.svg)](https://github.com/manavmate53-jpg/Money/actions/workflows/macro_regime_desk.yml)
[![Investment Council](https://github.com/manavmate53-jpg/Money/actions/workflows/daily_council_committee.yml/badge.svg)](https://github.com/manavmate53-jpg/Money/actions/workflows/daily_council_committee.yml)
[![Deriv Synthetic Trading](https://img.shields.io/badge/Deriv-Synthetic_Indices-00a8ff.svg)](https://deriv.com)
[![Gemini Council](https://img.shields.io/badge/Gemini_2.5-Multimodal_Council-4285F4.svg)](https://deepmind.google/technologies/gemini/)

An institutional-grade, self-training quantitative trading bot and capital advisor designed for **Deriv Synthetic Indices** (`Volatility 10–100 (1s)`, `Jump 10–100`, `Step Index`, `Volatility 75`). 

Runs 24/7 autonomously inside **GitHub Actions** (`schedule` + exact-time `repository_dispatch`) across distinct hedge-fund operational cadences, persisting state across runs via automated Git commits and providing human-ready `#1 + Uncorrelated Backup` trade tickets in [`reports/LATEST_ADVICE.md`](reports/LATEST_ADVICE.md).

---

## 🏛 HEDGE-FUND DESK CADENCES (GITHUB ACTIONS)

| Hedge-Fund Desk | GitHub Actions Cadence | Core Responsibility | Gemini Model & Action | State Output |
| :--- | :--- | :--- | :--- | :--- |
| **1. Execution & Risk Desk** | **Hourly + `repository_dispatch` (`run-hourly-desk`)** | M15 intra-window fill/4h TTL & 16h stagnation exit tracking, `MARKET_CONFIRMATION` + 50%-eq `LIMIT_PULLBACK` entries across 3 uncorrelated buckets | `gemini-2.5-flash` | [`reports/LATEST_ADVICE.md`](reports/LATEST_ADVICE.md), `data/trading_state.db` |
| **2. Macro & Regime Desk** | **4-Hourly + `repository_dispatch` (`run-macro-desk`)** | Captures H4/H1/M15 TradingView PNGs via Playwright, computes Hurst/ADX(14)/Kaufman ER(20), PrimeFX POIs & Pure Price Action | `gemini-2.5-flash` (Vision + Quant Blend) | [`config/macro_bias.json`](config/macro_bias.json) |
| **3. Investment Council & Vault** | **Daily + `repository_dispatch` (`run-council-desk`)** | Deep post-mortem on closed trades, mutates weights/filters every 5 losses, sweeps 50% profit into Vault, runs Monte Carlo & resets epochs | `gemini-2.5-pro` (Reflexion) | [`config/strategy_weights.json`](config/strategy_weights.json), [`reports/TRAINING_DASHBOARD.md`](reports/TRAINING_DASHBOARD.md) |
| **4. On-Demand Advisor** | **Manual Trigger** (`workflow_dispatch`) | Run on-demand scan or custom capital allocation (e.g. `$100`, `$500`) from the GitHub Actions tab | `gemini-2.5-pro` | [`reports/LATEST_ADVICE.md`](reports/LATEST_ADVICE.md) |

> [!TIP]
> **Exact On-Time Cron Scheduling via `repository_dispatch`**: Because GitHub Actions `schedule:` crons frequently experience 30–120+ minute queue delays on public repos, all 3 automated workflows also listen to **`repository_dispatch`** (`run-hourly-desk`, `run-macro-desk`, `run-council-desk`). Trigger them on the exact minute from macOS `launchd`, cron-job.org, or locally via:
> ```bash
> export GITHUB_TOKEN="<your-github-pat-with-repo-workflow-scope>"
> python scripts/trigger_dispatch.py --desk hourly   # or --desk macro | --desk council
> python scripts/trigger_dispatch.py --print-launchd-plist --desk hourly  # print exact macOS launchd plist
> ```

---

## 📑 LIVE REPORTS & REPOSITORIES

| Report | Purpose | Update Cadence |
| :--- | :--- | :--- |
| **[`reports/LATEST_ADVICE.md`](reports/LATEST_ADVICE.md)** | Live $100 Capital Advisor Ticket (lot, entry, SL, TP, hold time) | Hourly (`hourly_execution_desk.yml`) |
| **[`reports/PROFIT_LEDGER.md`](reports/PROFIT_LEDGER.md)** | All winning trades, pre/post fund balance, 5W thesis & vault accumulation | Real-time on trade win |
| **[`reports/LOSS_LEDGER.md`](reports/LOSS_LEDGER.md)** | All losing trades, 5W root-cause diagnosis & `what_improved` algorithmic update | Real-time on trade loss |
| **[`reports/TRADE_VISUAL_JOURNAL.md`](reports/TRADE_VISUAL_JOURNAL.md)** | Side-by-side BEFORE vs. AFTER multi-timeframe (`H4, H1, M15`) chart gallery | Daily & on trade exit |
| **[`reports/TRAINING_DASHBOARD.md`](reports/TRAINING_DASHBOARD.md)** | 30-day calibration gate scoreboard, Monte Carlo pass rate, Sharpe/Sortino ratios | Daily (`daily_council_committee.yml`) |

---

## ⚙️ TWO-STEP GITHUB SETUP

1. **Enable Workflow Write Permissions**:
   - In [github.com/manavmate53-jpg/Money](https://github.com/manavmate53-jpg/Money), navigate to **Settings → Actions → General → Workflow permissions**.
   - Select **Read and write permissions** and click **Save**. *(Enables the bot to persist `data/trading_state.db`, `config/macro_bias.json`, and `reports/` across CI/CD runs).*
2. **Repository Secret**:
   - Under **Settings → Secrets and variables → Actions**, confirm your repository secret is named **`GEMINI_API_KEY`**. *(Used automatically by all 4 workflows).*

---

## 🏛 ARCHITECTURE OVERVIEW

```
                                 4-HOUR CYCLE (UTC)
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     Playwright Chromium Headless                  WebSocket Protocol Client
 (https://charts.deriv.com/deriv)            (wss://ws.derivws.com/websockets/v3)
                 │                                               │
     Chart PNG Screenshots (H4/H1/M15)             Exact IEEE-754 OHLCV Arrays
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         ▼
                      ┌─────────────────────────────────────┐
                      │  MULTI-FRAMEWORK CONFLUENCE ENGINE  │
                      ├─────────────────────────────────────┤
                      │ • Engine A: ICT & Smart Money (SMC) │
                      │ • Engine B: Supply & Demand Zones   │
                      │ • Engine C: Pure Price Action & Reg │
                      └──────────────────┬──────────────────┘
                                         │
                                         ▼
                      ┌─────────────────────────────────────┐
                      │    SMALL-ACCOUNT ($100) GUARDRAIL   │
                      ├─────────────────────────────────────┤
                      │ Filters instruments by Min Lot risk. │
                      │ Bypasses high-risk V75 ($10/1k pts) │
                      │ Routes to nano-lots (V25 1s, Step)  │
                      └──────────────────┬──────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
    CLI Capital Advisor Plan                         Paper-Trading Simulator
(python main.py advise --capital 100)             (Trails SL to Break-Even @ +1R)
                                                                 │
                                                                 ▼
                                                    Resolved Trade (WIN / LOSS)
                                                                 │
                                                                 ▼
                                                  ┌─────────────────────────────┐
                                                  │    GEMINI-BACKED COUNCIL    │
                                                  ├─────────────────────────────┤
                                                  │ 1. Structure Auditor        │
                                                  │ 2. Risk & Timing Critic     │
                                                  │ 3. Strategist Weight Mutator│
                                                  └──────────────┬──────────────┘
                                                                 │
                                         ┌───────────────────────┴───────────────────────┐
                                         ▼                                               ▼
                             Episodic Journal Logged                        Two-Bucket Profit-Lock Vault
                             (data/COUNCIL_NOTES.md)                         50% Swept Home / 50% Reinvested
```

---

## ⚡ KEY QUANTITATIVE SAFEGUARDS

1. **Canvas Blindness Solved**: Deriv renders charts via an HTML5 `<canvas>` (SmartCharts), preventing DOM text parsing. This bot pairs **Playwright chart screenshots** (for visual structure auditing by Gemini Vision) with **WebSocket JSON-RPC packet extraction** (`wss://ws.derivws.com/websockets/v3`) for zero-error indicator calculation.
2. **Small-Account ($100) Protection & Jump Gap Shield**: Deriv's standard `Volatility 75` enforces a `0.01` minimum lot size, forcing oversized stop-loss risk on a `$100` account, while Jump Indices (`JD10`–`JD100`) experience discrete 20-minute news-style jumps (`1.50x` effective gap-slippage haircut in `PositionSizer`). During `TRAINING_CALIBRATION`, paper training runs up to **3 uncorrelated asset buckets** (`JUMP_FAMILY`, `VOL_1S_FAMILY`, `STEP_CLASSIC_FAMILY`, max 1 per bucket, `<= $4.50` combined risk) for faster statistical validation, while live `$100` advice in `LATEST_ADVICE.md` stays strictly locked to **`1` top setup + `1` uncorrelated backup**!
3. **The Two-Bucket Profit-Lock Vault ("Profit Comes Home With Us")**: Every time active capital grows by `+20%` (e.g. $100 $\to$ $120), **50% of the profit ($10) is swept permanently into the Locked Profit Vault** (profit brought home) and **50% ($10) compounds active trading bankroll**. Vaulted capital is immune to future trading drawdowns.
4. **Anti-Greed Session & In-Trade Rules**:
   - **Cover Risk to Zero**: Stop loss ratchets immediately to Break-Even (0R) once open profit reaches $+1.0R$ (World Cup Champion Fabio Valentini rule).
   - **Session Walk-Away**: Trading halts for the remainder of the session once cumulative realized gain reaches $+3.0R$ (Freddy Sarento rule).
5. **30-Day Graduation Gate**: The system remains in `TRAINING_CALIBRATION` status until 30 calendar days pass with **Profit Factor $\ge 1.50$**, **Max Drawdown $\le 10.0\%$**, and **$\ge 30$ resolved trades**.

---

## 🛠 INSTALLATION & SETUP

### 1. Clone & Enter Repository
```bash
git clone https://github.com/manavmate53-jpg/Money.git
cd Money
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure API Keys
```bash
cp .env.example .env
# Edit .env and insert your GEMINI_API_KEY
```

---

## 🚀 CLI USAGE

### 1. Personalized Capital Advisor (Interactive Blueprint)
Get an immediate, actionable trade ticket tailored to your account balance:
```bash
python main.py advise --capital 100
```
*Output includes: #1 Recommended Index, exact lot size, dollar risk, entry zone, SL, TP1, TP2, Break-Even trigger (+1R), hold duration, and the latest Council warning.*

### 2. Run a Single 4-Hour Scan Cycle
```bash
python main.py run-once
```
*With `--dry-run` to test without submitting paper orders:*
```bash
python main.py run-once --dry-run
```

### 3. Inspect System Status & Live Dashboard
View active paper positions, bankroll balance, locked vault profits, and calibration progress:
```bash
python main.py status
```

### 4. Generate Calibration Performance Report
```bash
python main.py report
```

### 5. Run Continuous Autonomous Daemon
Starts the automated 4-hour background scheduler (`00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC`):
```bash
python main.py daemon
```

---

## 🧪 AUTOMATED TEST SUITE

Run all quantitative unit tests:
```bash
pytest -v
```

Tests verify:
- **`test_pip_calculator.py`**: Deriv point values, lot stepping, and $100 account disqualification filters.
- **`test_bankroll_vault.py`**: Two-Bucket Profit Vault sweeping and bankruptcy auto-resets.
- **`test_technicals.py`**: Deterministic SMC/ICT swings, FVG imbalances, Supply/Demand zones, and One-Time Framing.
- **`test_council_loop.py`**: Gemini Council post-mortem weight mutations and 30-day readiness gate metrics.

---

## 📚 KNOWLEDGE BASE DISTILLATION

The system embeds algorithmic rules distilled from world champion traders and classic literature:
- `knowledge_base/champions_video_rules.json`: Rules from the [4 Trading Champions Live Session](https://youtu.be/DD73wDVvObI) (Fabio Valentini, Emir Gams, Patrick Nill, Freddy Sarento).
- `knowledge_base/ict_smc_primefx_rules.json`: Inner Circle Trader (ICT) & Smart Money Concepts (BOS, MSS/CHoCH, FVG, Order Blocks, Liquidity Sweeps, OTE).
- `knowledge_base/trading_books_principles.md`: Core principles from Vince Stanzione, Mark Douglas, Alexander Elder (2% rule, 6% monthly circuit breaker), Jack Schwager, Ralph Vince (fractional Kelly), and James Dalton (Auction Market Theory).

---

## 📄 LICENSE

MIT License. Designed and built by Antigravity Quantitative Systems.
