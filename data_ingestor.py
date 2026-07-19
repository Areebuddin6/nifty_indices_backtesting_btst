import os
import time
import json
import pandas as pd
import yfinance as yf
from retrying import retry
from rich.console import Console

# --- CONFIGURATION ---
SNAPSHOT_FOLDER = "nifty_historical_snapshots"
CACHE_FOLDER = "market_data_cache"
os.makedirs(CACHE_FOLDER, exist_ok=True)

# Define YEARS here to fix the UndefinedVariable error
YEARS = [
    ("2016-01-01", "2016-12-31", "2016"), ("2017-01-01", "2017-12-31", "2017"),
    ("2018-01-01", "2018-12-31", "2018"), ("2019-01-01", "2019-12-31", "2019"),
    ("2020-01-01", "2020-12-31", "2020"), ("2021-01-01", "2021-12-31", "2021"),
    ("2022-01-01", "2022-12-31", "2022"), ("2023-01-01", "2023-12-31", "2023"),
    ("2024-01-01", "2024-12-31", "2024"), ("2025-01-01", "2025-12-31", "2025")
]

console = Console()

@retry(stop_max_attempt_number=5, wait_exponential_multiplier=5000)
def fetch_ticker_data(ticker, start, end):
    """Downloads data for a single ticker with retry logic."""
    df = yf.download(ticker, start=start, end=end, progress=False)
    if df.empty: return None
    return df

def ingest_data():
    console.print("[bold blue]Starting Production Data Ingestion Pipeline...[/bold blue]")
    indices = ["nifty_50", "nifty_100", "nifty_250", "nifty_500"]
    
    for index in indices:
        for year_start, year_end, year_lbl in YEARS:
            path = os.path.join(SNAPSHOT_FOLDER, f"{index}_{year_lbl}.json")
            if not os.path.exists(path): 
                console.print(f"[yellow]Skipping: {path} not found.[/yellow]")
                continue
            
            with open(path, 'r') as f: 
                tickers = json.load(f)
            
            for ticker in tickers:
                cache_path = os.path.join(CACHE_FOLDER, f"{ticker}_{year_lbl}.parquet")
                
                # Check if file exists and is valid
                if os.path.exists(cache_path):
                    continue
                
                console.print(f"[cyan]Downloading {ticker} ({year_lbl})...[/cyan]")
                df = fetch_ticker_data(ticker, year_start, year_end)
                
                if df is not None:
                    df.to_parquet(cache_path)
                
                # Throttling to prevent IP bans
                time.sleep(0.5) 

if __name__ == "__main__":
    ingest_data()
    console.print("[bold green]Ingestion Pipeline Complete.[/bold green]")