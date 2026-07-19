import os
import json
import math
import statistics
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

# --- CONFIGURATION ---
CACHE_FOLDER = "market_data_cache"
SNAPSHOT_FOLDER = "nifty_historical_snapshots"
MIN_TURNOVER = 150_000_000
FRICTION = 0.0020          # round-trip cost, applied per trade
TOP_N = 1                  # how many "highest gaining" stocks to buy each day
INITIAL_CAPITAL = 100_000.0  # reset to this at the START of every single year
TRADING_DAYS_PER_YEAR = 252   # for annualizing the daily/trade-level Sharpe

console = Console()


def run_btst_strategy():
    console.print(Panel("[bold cyan]BTST STRATEGY ENGINE: PRODUCTION MODE[/bold cyan]", border_style="green"))
    indices = ["nifty_50", "nifty_100", "nifty_250", "nifty_500"]
    all_results = []

    years = [str(y) for y in range(2016, 2026)]
    jobs = [(index, year) for index in indices for year in years]

    progress_columns = (
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[stage]}[/bold cyan]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )

    with Progress(*progress_columns, console=console) as progress:
        task_id = progress.add_task("backtest", total=len(jobs), stage="Starting...")

        for index, year in jobs:
            progress.update(task_id, stage=f"{index.upper()} · {year} — loading snapshot")

            path = os.path.join(SNAPSHOT_FOLDER, f"{index}_{year}.json")
            if not os.path.exists(path):
                progress.advance(task_id)
                continue

            with open(path, 'r') as f:
                tickers = json.load(f)

            df_list = []
            for i, ticker in enumerate(tickers, 1):
                if i % 25 == 0 or i == len(tickers):
                    progress.update(
                        task_id,
                        stage=f"{index.upper()} · {year} — reading tickers ({i}/{len(tickers)})",
                    )

                cache_path = os.path.join(CACHE_FOLDER, f"{ticker}_{year}.parquet")
                if not os.path.exists(cache_path):
                    continue

                df = pd.read_parquet(cache_path)

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                # Date lives in the index after yf.download/to_parquet round-trip.
                # Bring it out so we can group by calendar day across tickers.
                df = df.reset_index()
                date_col = 'Date' if 'Date' in df.columns else df.columns[0]
                df = df.rename(columns={date_col: 'Date'})

                df['Open'] = pd.to_numeric(df['Open'], errors='coerce')
                df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
                df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
                df = df.sort_values('Date')

                # "Highest gaining stock" ranking signal: today's close-over-close gain.
                df['DayGain'] = df['Close'].pct_change()

                # BTST = buy at today's close, sell at the NEXT trading day's open.
                df['NextOpen'] = df['Open'].shift(-1)

                df['Ticker'] = ticker
                df_list.append(df)

            if not df_list:
                progress.advance(task_id)
                continue

            progress.update(task_id, stage=f"{index.upper()} · {year} — ranking & simulating trades")

            master_df = pd.concat(df_list, ignore_index=True)
            master_df['Turnover'] = master_df['Close'] * master_df['Volume']

            # Liquidity filter + drop rows where we can't compute a real trade
            valid = master_df[
                (master_df['Turnover'] > MIN_TURNOVER)
                & master_df['DayGain'].notna()
                & master_df['NextOpen'].notna()
            ].copy()

            if valid.empty:
                progress.advance(task_id)
                continue

            # Each day, rank the liquid universe by today's gain and keep the top N.
            valid = valid.sort_values(['Date', 'DayGain'], ascending=[True, False])
            picks = valid.groupby('Date', group_keys=False).head(TOP_N).copy()

            if picks.empty:
                progress.advance(task_id)
                continue

            # Trade return: close -> next day's open, minus friction, per trade.
            picks['TradeReturn'] = (picks['NextOpen'] - picks['Close']) / picks['Close'] - FRICTION

            # Capital is RESET to INITIAL_CAPITAL here, at the start of every
            # (index, year) pass. It compounds only across this year's trades
            # and is discarded before the next year starts — nothing carries over.
            capital = INITIAL_CAPITAL
            for r in picks.sort_values('Date')['TradeReturn']:
                capital *= (1 + r)

            compounded_return_pct = (capital / INITIAL_CAPITAL - 1) * 100

            all_results.append({
                "Index": index.upper(),
                "Year": year,
                "Trades": len(picks),
                "StartCapital": INITIAL_CAPITAL,
                "EndCapital": capital,
                "Return": compounded_return_pct,
                # Kept for the trade-level (daily) annualized Sharpe at report time —
                # this is ~200-250 data points per year vs. 1 annual number.
                "TradeReturns": picks['TradeReturn'].tolist(),
            })

            progress.advance(task_id)

    render_report(all_results)


def compute_consistency_stats(returns, trade_returns=None):
    """
    `returns`: list of annual % returns for ONE index (one number per year).
    `trade_returns`: pooled list of every individual trade's decimal return
    for that index across ALL years — used only for the daily Sharpe, which
    needs many more samples than 10 annual numbers to be statistically stable.
    """
    n = len(returns)
    mean = statistics.mean(returns)
    median = statistics.median(returns)
    stdev = statistics.stdev(returns) if n > 1 else 0.0
    win_rate = sum(1 for r in returns if r > 0) / n * 100

    # Sharpe-style ratio: return earned per unit of year-to-year volatility.
    # Risk-free rate treated as 0 — this is a relative consistency score for
    # comparing indices against EACH OTHER, not an investable Sharpe ratio.
    # Only 10 data points here, so treat this as a rough read (see the daily
    # Sharpe below for a statistically sturdier version of the same idea).
    sharpe = mean / stdev if stdev > 0 else float('nan')

    # Sortino-style ratio: only downside (losing) years count as "risk".
    # A strategy with big upside swings but no losing years scores very high here.
    downside = [r for r in returns if r < 0]
    if len(downside) > 1:
        downside_dev = statistics.stdev(downside)
    elif len(downside) == 1:
        downside_dev = abs(downside[0])
    else:
        downside_dev = 0.0
    sortino = mean / downside_dev if downside_dev > 0 else float('nan')

    # Max drawdown: chain the annual returns into one illustrative equity
    # curve (this is NOT the real capital, which resets every year per your
    # spec) purely to answer "how bad would a losing streak have compounded
    # to if you'd stayed invested?" — a standard tail-risk read.
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in returns:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak)

    # Consolidated compounded return: chain the annual returns end-to-end as
    # if you'd stayed invested through the whole 2016-2025 span (this is a
    # summary READ, distinct from EndCapital in the table, which resets
    # every year per your spec — this number is % only, no rupee figure).
    consolidated_compounded_pct = (equity - 1) * 100

    # Trade-level (daily) annualized Sharpe: pools every individual trade
    # across all years for this index — hundreds of samples instead of 10 —
    # so it's a much sturdier consistency read than the annual Sharpe above.
    daily_sharpe = float('nan')
    if trade_returns and len(trade_returns) > 1:
        daily_mean = statistics.mean(trade_returns)
        daily_std = statistics.stdev(trade_returns)
        if daily_std > 0:
            daily_sharpe = (daily_mean / daily_std) * math.sqrt(TRADING_DAYS_PER_YEAR)

    return {
        "mean": mean,
        "median": median,
        "stdev": stdev,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd * 100,
        "consolidated_compounded": consolidated_compounded_pct,
        "daily_sharpe": daily_sharpe,
        "n_trades_pooled": len(trade_returns) if trade_returns else 0,
    }


def render_report(results):
    if not results:
        console.print("[bold red]No data found for the specified years/indices.[/bold red]")
        return

    df = pd.DataFrame(results)
    console.print(Panel("[bold white]Consolidated BTST Backtest Results[/bold white]", border_style="magenta"))

    index_order = df["Index"].drop_duplicates().tolist()

    for idx_name in index_order:
        group = df[df["Index"] == idx_name].sort_values("Year")

        table = Table(title=f"[bold white]{idx_name}[/bold white]", header_style="bold magenta", expand=True)
        table.add_column("Year", style="white")
        table.add_column("Trades", justify="right", style="yellow")
        table.add_column("Start Capital (₹)", justify="right", style="white")
        table.add_column("End Capital (₹)", justify="right", style="white")
        table.add_column("Annual Return (%)", justify="right", style="green")

        for _, row in group.iterrows():
            color = "green" if row['Return'] >= 0 else "red"
            table.add_row(
                row["Year"],
                f"{int(row['Trades']):,}",
                f"{row['StartCapital']:,.0f}",
                f"[{color}]{row['EndCapital']:,.2f}[/{color}]",
                f"[{color}]{row['Return']:.4f}%[/{color}]",
            )

        console.print(table)

        pooled_trade_returns = [r for lst in group["TradeReturns"] for r in lst]
        stats = compute_consistency_stats(group["Return"].tolist(), pooled_trade_returns)

        stats_table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        stats_table.add_column(style="white")
        stats_table.add_column(justify="right", style="bold white")

        def fmt(x, suffix="%"):
            return "N/A" if isinstance(x, float) and math.isnan(x) else f"{x:.2f}{suffix}"

        stats_table.add_row("Mean Annual Return", fmt(stats["mean"]))
        stats_table.add_row("Median Annual Return", fmt(stats["median"]))
        stats_table.add_row(
            f"Consolidated Compounded Return ({group['Year'].min()}\u2013{group['Year'].max()})",
            fmt(stats["consolidated_compounded"]),
        )
        stats_table.add_row("Std Dev (year-to-year volatility)", fmt(stats["stdev"]))
        stats_table.add_row("Win Rate (% of profitable years)", fmt(stats["win_rate"]))
        stats_table.add_row("Sharpe-style Ratio (10 annual points)", fmt(stats["sharpe"], ""))
        stats_table.add_row("Sortino-style Ratio (mean / downside dev)", fmt(stats["sortino"], ""))
        stats_table.add_row(
            f"Sharpe (daily trades, annualized, n={stats['n_trades_pooled']:,})",
            fmt(stats["daily_sharpe"], ""),
        )
        stats_table.add_row("Max Drawdown (chained equity, illustrative)", fmt(stats["max_dd"]))

        console.print(Panel(
            stats_table,
            title=f"[bold yellow]{idx_name} — Consistency & Reliability[/bold yellow]",
            border_style="yellow",
            expand=False,
        ))

        # Visual gap between one index's block and the next.
        console.print("\n" * 3)


if __name__ == "__main__":
    run_btst_strategy()