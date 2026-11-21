# MONEY: AUTONOMOUS DERIV SYNTHETIC INDICES TRADING BOT & GEMINI COUNCIL ADVISOR

[![Deriv Synthetic Trading](https://img.shields.io/badge/Deriv-Synthetic_Indices-00a8ff.svg)](https://deriv.com)
[![Gemini Council](https://img.shields.io/badge/Gemini_2.5-Multimodal_Council-4285F4.svg)](https://deepmind.google/technologies/gemini/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An autonomous, self-improving quantitative trading system and capital advisor designed for **Deriv Synthetic Indices** (`Volatility 10–100`, `1s` variants, `Boom/Crash`, `Step Index`). 

Driven by a **Multi-Framework Confluence Engine** (ICT/SMC, Supply & Demand, Auction Market Theory), **Playwright SmartCharts Canvas Automation**, **Live WebSocket Interception**, and a **Gemini 2.5-backed 3-Persona Council** that conducts episodic post-mortems and mutates strategy weights over a 30-day forward-testing calibration gate.

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
                      │ • Engine C: Auction Market Theory   │
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
2. **Small-Account ($100) Protection**: Deriv's standard `Volatility 75` enforces a `0.01` minimum lot size ($10 per 1,000 points), forcing an unsafe $2.50–$5.00 stop loss risk on a $100 account. The **Position Sizer** automatically disqualifies standard V75 and routes capital to nano-lot instruments (`Volatility 25 (1s)`, `Volatility 50 (1s)`, and `Step Index` with `0.005` lot granularity), strictly capping risk to $1.00–$1.50 (1.0%–1.5%).
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
