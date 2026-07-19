import os
import io
import json
import time
import requests
import pandas as pd
import yfinance as yf
from tqdm import tqdm

# --- COLORS & STYLING ---
HEADER = '\033[95m'
BLUE = '\033[94m'
GREEN = '\033[92m'
WARNING = '\033[93m'
FAIL = '\033[91m'
ENDC = '\033[0m'
BOLD = '\033[1m'

# --- CONFIG ---
SNAPSHOT_DIR = "nifty_historical_snapshots"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

INDEX_CONFIG = {
    "NIFTY_50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "NIFTY_100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "NIFTY_250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
    "NIFTY_500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
}

WAYBACK_API = "http://archive.org/wayback/available"


def get_current_list(url):
    """Live CSV — used only as a last-resort fallback if no archive exists for a year."""
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        df = pd.read_csv(io.StringIO(r.text))
        return [f"{s}.NS" for s in df['Symbol'].dropna().unique()]
    except Exception:
        return []


def get_archived_list(url, year):
    """
    Fetch the constituent CSV as it looked closest to Dec 31 of `year`, via the
    Wayback Machine. Returns (tickers, snapshot_timestamp) or (None, None) if
    no archived snapshot exists for that period.
    """
    timestamp = f"{year}1231"
    try:
        resp = requests.get(
            WAYBACK_API,
            params={"url": url, "timestamp": timestamp},
            timeout=15,
        )
        data = resp.json()
        snapshot = data.get("archived_snapshots", {}).get("closest")
        if not snapshot or not snapshot.get("available"):
            return None, None

        archived_url = snapshot["url"]
        archived_ts = snapshot["timestamp"]  # actual date the crawl happened, e.g. 20171228

        r = requests.get(archived_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        df = pd.read_csv(io.StringIO(r.text))
        tickers = [f"{s}.NS" for s in df['Symbol'].dropna().unique()]
        return tickers, archived_ts
    except Exception:
        return None, None


def validate_and_report(index_name):
    url = INDEX_CONFIG[index_name]
    report = []

    print(f"\n{HEADER}{BOLD}🚀 STARTING HISTORICAL ENGINE: {index_name}{ENDC}")

    for year in range(2016, 2027):
        tickers, snapshot_ts = get_archived_list(url, year)
        source = "wayback"

        if tickers is None:
            # No archived snapshot found for this year — fall back to the
            # live list, but flag it clearly so you know that year's universe
            # is NOT a true historical reconstruction.
            print(f"{WARNING}  ⚠ No Wayback snapshot found for {index_name} {year} — "
                  f"falling back to CURRENT list (survivorship bias applies to this year).{ENDC}")
            tickers = get_current_list(url)
            source = "current_fallback"
            snapshot_ts = None

        valid = []
        skipped = []

        desc = f"{BLUE}{BOLD}Processing {year} ({source}){ENDC}"
        pbar = tqdm(tickers, desc=desc, unit="stk", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]")

        for ticker in pbar:
            try:
                df = yf.Ticker(ticker).history(start=f"{year}-01-01", end=f"{year}-12-31")
                if not df.empty and len(df) > 30:
                    valid.append(ticker)
                else:
                    skipped.append(ticker)
            except Exception:
                skipped.append(ticker)

            pbar.set_postfix({"Valid": len(valid), "Skip": len(skipped)})

        with open(os.path.join(SNAPSHOT_DIR, f"{index_name.lower()}_{year}.json"), 'w') as f:
            json.dump(valid, f)

        report.append({
            "Year": year,
            "Universe_Source": source,
            "Wayback_Snapshot_Date": snapshot_ts,
            "Total_In_Archived_List": len(tickers),
            "Active_With_Data": len(valid),
            "No_Data_Or_Delisted": len(skipped),
        })

        # Be polite to the Wayback API between years
        time.sleep(0.5)

    pd.DataFrame(report).to_csv(f"report_{index_name}.csv", index=False)
    print(f"{GREEN}{BOLD}✔ {index_name} complete. Report saved as 'report_{index_name}.csv'{ENDC}\n")


if __name__ == "__main__":
    for index in INDEX_CONFIG:
        validate_and_report(index)