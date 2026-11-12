"""Off-chain public series, pulled without any API key.

These are the only numbers in the project that do not come straight from a
NEAR node. Each one is named in the dashboard and README as third-party data.

* NearBlocks daily chart: NEAR/USD price, transactions, active accounts,
  total fees paid. Used for USD conversion and as an independent cross-check
  of the header-derived supply and burn.
* DefiLlama dimension adapters: NEAR Intents volume, gross fees and captured
  revenue, plus gas fees, stablecoin supply and TVL on NEAR.

Each series is saved as a small CSV (date,value...) so a diff is readable.
The only transformation is column selection and unix-day to ISO date.
"""
import csv
import datetime as dt
import json
import os
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (epoch-ledger; research script)", "Accept": "application/json"}

LLAMA = {
    "llama_intents_volume": "https://api.llama.fi/summary/dexs/near-intents?dataType=dailyVolume",
    "llama_intents_fees": "https://api.llama.fi/summary/fees/near-intents?dataType=dailyFees",
    "llama_intents_revenue": "https://api.llama.fi/summary/fees/near-intents?dataType=dailyRevenue",
    "llama_intents_holders_revenue": "https://api.llama.fi/summary/fees/near-intents?dataType=dailyHoldersRevenue",
    "llama_chain_fees": "https://api.llama.fi/summary/fees/near?dataType=dailyFees",
}
LLAMA_LISTS = {
    "llama_stablecoins": ("https://stablecoins.llama.fi/stablecoincharts/Near",
                          lambda r: (int(r["date"]), (r.get("totalCirculatingUSD") or {}).get("peggedUSD"))),
    "llama_tvl": ("https://api.llama.fi/v2/historicalChainTvl/Near", lambda r: (int(r["date"]), r.get("tvl"))),
}
NEARBLOCKS = "https://api.nearblocks.io/v1/charts"
NEARBLOCKS_COLS = ["near_price", "total_supply", "circulating_supply", "blocks", "txns",
                   "txn_fee", "txn_fee_usd", "new_accounts", "active_accounts", "active_contracts"]


def get_json(url, tries=5):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # network errors only; retried, then raised
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last!r}")


def iso(day_unix):
    return dt.datetime.fromtimestamp(int(day_unix), dt.timezone.utc).strftime("%Y-%m-%d")


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def pull(raw_dir, log=print):
    """Fetch every series. Returns {name: {"url":..., "rows":...}} for the manifest."""
    out = {}
    methodology = {}
    for name, url in LLAMA.items():
        j = get_json(url)
        rows = [(iso(t), v) for t, v in j.get("totalDataChart", []) if v is not None]
        write_csv(os.path.join(raw_dir, name + ".csv"), ["date", "usd"], rows)
        methodology[name] = {"url": url, "methodology": j.get("methodology"),
                             "methodologyURL": j.get("methodologyURL")}
        out[name] = {"url": url, "rows": len(rows)}
        log(f"  {name}: {len(rows)} days")
        time.sleep(0.4)
    for name, (url, pick) in LLAMA_LISTS.items():
        j = get_json(url)
        rows = [(iso(t), v) for t, v in map(pick, j) if v is not None]
        write_csv(os.path.join(raw_dir, name + ".csv"), ["date", "usd"], rows)
        out[name] = {"url": url, "rows": len(rows)}
        log(f"  {name}: {len(rows)} days")
        time.sleep(0.4)
    with open(os.path.join(raw_dir, "llama_methodology.json"), "w", newline="\n") as fh:
        json.dump(methodology, fh, indent=1, sort_keys=True)
        fh.write("\n")
    out["llama_methodology"] = {"url": "methodology fields of the DefiLlama responses above", "rows": len(methodology)}

    charts = get_json(NEARBLOCKS)["charts"]
    rows = [[c["date"][:10]] + [c.get(k, "") for k in NEARBLOCKS_COLS] for c in charts]
    write_csv(os.path.join(raw_dir, "nearblocks_daily.csv"), ["date"] + NEARBLOCKS_COLS, rows)
    out["nearblocks_daily"] = {"url": NEARBLOCKS, "rows": len(rows)}
    log(f"  nearblocks_daily: {len(rows)} days")
    return out
