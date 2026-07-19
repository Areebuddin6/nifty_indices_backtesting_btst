# Nifty Indices Backtesting — BTST Strategy

A three-stage Python pipeline that reconstructs historical Nifty index
constituents (Nifty 50 / 100 / 250 / 500), downloads their daily OHLCV data,
and backtests a simple **Buy Today, Sell Tomorrow (BTST)** momentum strategy
on each index, year by year, from 2016 through 2025 (2026 partial for
snapshotting).

The strategy each day: rank the day's liquid stocks by that day's close-over-
close gain, buy the top gainer(s) at today's close, and sell at the next
trading day's open.

## How it works

The pipeline runs in three stages, each producing the input the next stage
needs:

1. **`generate_snapshots.py`** — Historical universe reconstruction
   For each index (Nifty 50, 100, 250, 500) and each year from 2016–2026, it
   fetches the constituent list as it looked around December 31 of that year
   by querying the [Wayback Machine](https://archive.org/wayback/available)
   for an archived copy of the official NSE constituent CSV
   (`niftyindices.com`). If no archived snapshot exists for a given year, it
   falls back to the **current** live constituent list for that year and
   flags it (this introduces survivorship bias for that year — see
   Limitations below). Each ticker is then validated against Yahoo Finance
   (must have >30 trading days of data in that year) and written to
   `nifty_historical_snapshots/{index}_{year}.json`. A CSV report
   (`report_{INDEX}.csv`) summarizing source/valid/skipped counts per year is
   also produced.

2. **`data_ingestor.py`** — Price data download
   Reads the ticker snapshots produced above and downloads daily OHLCV data
   for each ticker/year pair from Yahoo Finance (`yfinance`), with retry logic
   and throttling to avoid rate limits. Data is cached as Parquet files in
   `market_data_cache/{TICKER}_{YEAR}.parquet` and downloads are skipped for
   tickers/years already cached, so the script is safe to re-run.

3. **`backtest_engine.py`** — Strategy simulation & report
   For each (index, year) pair, loads all cached price data, applies a
   liquidity filter (`Close * Volume > ₹150,000,000` turnover), ranks the
   day's liquid stocks by daily gain, and picks the top `TOP_N` stock(s) each
   day. Each trade's return is `(NextOpen - Close) / Close - FRICTION`
   (0.20% round-trip friction). Capital starts at ₹100,000 at the beginning
   of **every** (index, year) pass and compounds only within that year (it
   does not carry over between years). Results are rendered as rich console
   tables per index, with year-by-year capital/returns plus consolidated
   statistics: mean/median annual return, standard deviation, win rate,
   Sharpe-style and Sortino-style ratios (annual and daily/trade-level), a
   chained consolidated compounded return, and an illustrative max drawdown.

## Requirements

- Python 3.10+ (project was built against a recent CPython; `numpy==2.4.6`,
  `pandas==3.0.3`, etc. require modern Python)
- Internet access (for `generate_snapshots.py` and `data_ingestor.py` — both
  hit the Wayback Machine and Yahoo Finance)
- Dependencies pinned in `requirements.txt`. Key ones:
  - `yfinance` — historical price data
  - `pandas`, `numpy`, `pyarrow` — data handling / Parquet storage
  - `requests`, `beautifulsoup4` — fetching and parsing constituent lists
  - `tqdm` — progress bars in `generate_snapshots.py`
  - `rich` — formatted console tables/progress in `backtest_engine.py`
  - `retrying` — retry logic in `data_ingestor.py`
  - `openpyxl`, `statsmodels`, `scipy`, `matplotlib`, `alpaca-py`, `pykalman`,
    `nsepython` and others are also pinned but not directly imported by the
    three scripts above — they may be leftovers from a broader environment or
    used for optional/future analysis.

> **Note:** `requirements.txt` in the repository is currently saved as
> **UTF-16** text. Some tools (e.g. plain `pip install -r requirements.txt`
> on certain platforms) may fail to parse it as-is. If you hit an encoding
> error, re-save/convert it to UTF-8 first (see step 2 below).

## Project structure

```
nifty_indices_backtesting_btst/
├── generate_snapshots.py      # Stage 1: build historical index constituent lists
├── data_ingestor.py            # Stage 2: download & cache OHLCV data
├── backtest_engine.py          # Stage 3: run the BTST backtest & print report
├── requirements.txt             # Pinned dependencies (UTF-16 encoded)
├── .gitignore                   # Ignores cache/snapshot/report outputs
└── README.md

# Generated at runtime (not committed):
nifty_historical_snapshots/      # {index}_{year}.json ticker lists
market_data_cache/               # {ticker}_{year}.parquet price data
report_NIFTY_50.csv              # snapshot-generation reports
report_NIFTY_100.csv
report_NIFTY_250.csv
report_NIFTY_500.csv
```

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Areebuddin6/nifty_indices_backtesting_btst.git
cd nifty_indices_backtesting_btst
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

If `pip install -r requirements.txt` fails with an encoding/parse error
(because the file is UTF-16), convert it to UTF-8 first:

```bash
iconv -f UTF-16 -t UTF-8 requirements.txt -o requirements_utf8.txt
pip install -r requirements_utf8.txt
```

Otherwise, install directly:

```bash
pip install -r requirements.txt
```

## Running the backtest — step by step

Run the three stages **in order**. Each stage writes files that the next
stage reads, so don't skip ahead.

### Step 1 — Build the historical index snapshots

```bash
python generate_snapshots.py
```

- Creates `nifty_historical_snapshots/` and populates it with one JSON file
  per index/year (e.g. `nifty_50_2020.json`), plus `report_NIFTY_50.csv`,
  `report_NIFTY_100.csv`, `report_NIFTY_250.csv`, `report_NIFTY_500.csv` in
  the project root.
- This step iterates 4 indices × ~11 years and validates every constituent
  against Yahoo Finance, so it is the slowest stage — expect it to take a
  while depending on your connection and index size (Nifty 500 in
  particular).
- Watch the console output for `⚠ No Wayback snapshot found...` warnings —
  these indicate a year fell back to the current constituent list rather
  than a true historical reconstruction.

### Step 2 — Download and cache price data

```bash
python data_ingestor.py
```

- Reads the JSON snapshots from Step 1 and downloads daily OHLCV data for
  every ticker/year via `yfinance`, saving each as a Parquet file in
  `market_data_cache/`.
- Already-cached ticker/year files are skipped automatically, so you can
  safely interrupt and re-run this step.
- Includes retry logic (5 attempts, exponential backoff) and a 0.5s delay
  between downloads to reduce the chance of being rate-limited.

### Step 3 — Run the backtest

```bash
python backtest_engine.py
```

- Loads the cached Parquet data, applies the liquidity filter, simulates the
  BTST strategy for every (index, year) pair from 2016–2025, and prints a
  formatted report to the console: a results table per index (trades, start/
  end capital, annual return) followed by a consistency/reliability panel
  (mean/median return, std dev, win rate, Sharpe/Sortino, daily annualized
  Sharpe, and illustrative max drawdown).
- This step is local/offline once Steps 1–2 have populated the cache — no
  network access needed.

## Configuration

Key parameters can be tuned at the top of `backtest_engine.py`:

| Constant | Default | Meaning |
|---|---|---|
| `MIN_TURNOVER` | `150,000,000` | Minimum `Close * Volume` (₹) for a stock to be considered liquid enough to trade that day |
| `FRICTION` | `0.0020` | Round-trip transaction cost applied per trade (0.20%) |
| `TOP_N` | `1` | Number of top daily gainers bought each day |
| `INITIAL_CAPITAL` | `100,000.0` | Starting capital, reset at the beginning of every (index, year) run |
| `TRADING_DAYS_PER_YEAR` | `252` | Used to annualize the trade-level (daily) Sharpe ratio |

`generate_snapshots.py` and `data_ingestor.py` also define the year range
(`2016`–`2026` for snapshots, `2016`–`2025` for ingestion/backtest) and the
index list near the top of each file if you want to narrow the scope (e.g.
backtest only Nifty 50) or extend the years.

## Limitations & caveats

- **Survivorship / lookback bias in fallback years:** when no Wayback
  Machine snapshot is available for a given index/year, `generate_snapshots.py`
  falls back to the *current* constituent list for that year. Any year using
  this fallback is not a true point-in-time reconstruction and is flagged in
  the console output and `Universe_Source` column of the generated reports.
- **Data source:** all price data comes from Yahoo Finance via `yfinance`,
  which can have gaps, adjustments, or delisted-ticker issues for Indian
  equities.
- **Capital does not compound across years** by design — each (index, year)
  backtest resets to `INITIAL_CAPITAL`. The "Consolidated Compounded Return"
  in the report is a separate, illustrative figure that chains the annual
  *percentage* returns together as if reinvested continuously; it does not
  correspond to the `EndCapital` column.
- This is a research/backtesting tool, not investment advice — past
  simulated performance does not guarantee future results.

## License

No license file is currently included in the repository. Check with the
repository owner before reuse or redistribution.