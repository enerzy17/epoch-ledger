"""Turn the raw files into the derived tables and one metrics file.

Inputs  (data/raw):   epochs.csv, samples.csv, nearblocks_daily.csv, llama_*.csv
Outputs (data/derived): epochs_ledger.csv, daily.csv, yearly.csv, metrics.json

Nothing here touches the network. Given the same raw files the outputs are
byte-identical, which is what `python run.py --offline` relies on.
"""
import csv
import datetime as dt
import json
import os
from collections import OrderedDict, defaultdict

from . import model

YOCTO = 10**24
SERIES = ["llama_intents_volume", "llama_intents_fees", "llama_intents_revenue",
          "llama_intents_holders_revenue", "llama_chain_fees", "llama_stablecoins", "llama_tvl"]


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def day_of(ts_ns):
    return dt.datetime.fromtimestamp(int(ts_ns) / 1e9, dt.timezone.utc).strftime("%Y-%m-%d")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def epoch_ledger(epochs):
    """One row per epoch with exact minted and burnt NEAR, from header supply."""
    out, skipped = [], 0
    for prev, cur in zip(epochs, epochs[1:]):
        if not cur.get("first_supply"):
            skipped += 1
            continue
        first_burnt = int(cur["first_block_burnt"])
        minted = int(cur["first_supply"]) - int(prev["last_supply"]) + first_burnt
        burnt = int(cur["first_supply"]) - int(cur["last_supply"]) + first_burnt
        secs = (int(cur["last_ts_ns"]) - int(prev["last_ts_ns"])) / 1e9
        out.append(OrderedDict(
            date=day_of(cur["last_ts_ns"]), epoch_id=cur["epoch_id"],
            last_height=int(cur["last_height"]),
            blocks=int(cur["last_height"]) - int(prev["last_height"]),
            hours=round(secs / 3600, 4),
            minted_near=minted / YOCTO, burnt_near=burnt / YOCTO,
            supply_near=int(cur["last_supply"]) / YOCTO,
            protocol_version=int(cur["protocol_version"]),
        ))
    return out, skipped


def daily_table(ledger, raw_dir):
    days = defaultdict(lambda: {"minted_near": 0.0, "burnt_near": 0.0, "epochs": 0, "supply_near": None})
    for e in ledger:
        d = days[e["date"]]
        d["minted_near"] += e["minted_near"]
        d["burnt_near"] += e["burnt_near"]
        d["epochs"] += 1
        d["supply_near"] = e["supply_near"]
    for row in read_csv(os.path.join(raw_dir, "nearblocks_daily.csv")):
        d = days[row["date"]]
        d["price_usd"] = f(row["near_price"]) or None
        d["txns"] = f(row["txns"])
        d["active_accounts"] = f(row["active_accounts"])
        d["new_accounts"] = f(row["new_accounts"])
        fee = f(row["txn_fee"])
        d["fees_paid_near"] = fee / YOCTO if fee is not None else None
        sup = f(row["total_supply"])
        d["nb_supply_near"] = sup / YOCTO if sup else None
    for name in SERIES:
        col = name.replace("llama_", "") + "_usd"
        for row in read_csv(os.path.join(raw_dir, name + ".csv")):
            days[row["date"]][col] = f(row["usd"])
    table, supply = [], None
    for date in sorted(days):
        d = days[date]
        supply = d["supply_near"] or supply  # carry the last known supply across gaps
        d["supply_near"] = supply
        table.append(OrderedDict(date=date, **d))
    return table


def window(table, end_idx, n):
    """Sums and ratios over the n days ending at end_idx (inclusive)."""
    rows = table[max(0, end_idx - n + 1): end_idx + 1]

    def total(key):
        return sum(r.get(key) or 0.0 for r in rows)

    def usd(key):  # NEAR flow valued at each day's price
        return sum((r.get(key) or 0.0) * (r.get("price_usd") or 0.0) for r in rows)

    days = len(rows)
    minted, burnt = total("minted_near"), total("burnt_near")
    supply = [r["supply_near"] for r in rows if r.get("supply_near")]
    avg_supply = sum(supply) / len(supply) if supply else None  # None before the ledger starts
    vol, fees = total("intents_volume_usd"), total("intents_fees_usd")
    rev, hold = total("intents_revenue_usd"), total("intents_holders_revenue_usd")
    issuance_usd, burn_usd = usd("minted_near"), usd("burnt_near")
    paid = total("fees_paid_near")
    prices = [r["price_usd"] for r in rows if r.get("price_usd")]
    return OrderedDict(
        days=days, start=rows[0]["date"], end=rows[-1]["date"],
        minted_near=minted, burnt_near=burnt, net_issued_near=minted - burnt,
        issuance_rate=minted / avg_supply * model.DAYS_PER_YEAR / days if avg_supply else None,
        net_supply_growth_rate=(minted - burnt) / avg_supply * model.DAYS_PER_YEAR / days if avg_supply else None,
        burn_cover=burnt / minted if minted else None,
        issuance_usd=issuance_usd, burn_usd=burn_usd,
        issuance_usd_per_day=issuance_usd / days, burn_near_per_day=burnt / days,
        avg_price_usd=sum(prices) / len(prices) if prices else None,
        gas_fees_paid_near=paid, burn_share_of_gas_fees=burnt / paid if paid else None,
        intents_volume_usd=vol, intents_fees_usd=fees, intents_revenue_usd=rev,
        intents_holders_revenue_usd=hold,
        intents_volume_usd_per_day=vol / days,
        gross_fee_rate=fees / vol if vol else None,
        capture_rate=rev / vol if vol else None,
        buyback_cover=hold / issuance_usd if issuance_usd else None,
        revenue_cover=rev / issuance_usd if issuance_usd else None,
        gross_fee_cover=fees / issuance_usd if issuance_usd else None,
        total_cover=(burn_usd + hold) / issuance_usd if issuance_usd else None,
        txns_per_day=total("txns") / days, active_accounts_per_day=total("active_accounts") / days,
    )


def lst_apr(samples, col, end_ns, days):
    """Annualised growth of a liquid staking share price over the last `days`."""
    pts = [(int(s["ts_ns"]), int(s[col])) for s in samples if s.get(col) and int(s["ts_ns"]) <= end_ns]
    if len(pts) < 2:
        return None
    target = end_ns - days * 86400 * 10**9
    start = min(pts, key=lambda p: abs(p[0] - target))
    end = pts[-1]
    span_days = (end[0] - start[0]) / 1e9 / 86400
    if span_days < days * 0.8:
        return None
    return (end[1] / start[1]) ** (model.DAYS_PER_YEAR / span_days) - 1


WALLET_COLS = ("buybacks_wallet", "fe_wallet", "onecs_wallet")
BUYBACK_START = "2026-02-23"  # date DefiLlama's methodology gives for the start of market buybacks


def wallet_check(samples, table, end_ns):
    """NEAR held by the revenue accounts, against the third-party buyback series.

    Between the first weekly sample on or after BUYBACK_START and the latest
    one, compare the rise in the accounts' combined NEAR balance (read from
    chain) with the NEAR that DefiLlama's buyback revenue would have bought at
    each day's price. The two need not match: bought NEAR can be staked, moved
    or held elsewhere, and the accounts receive other flows. The ratio is
    reported, not assumed.
    """
    pts = [s for s in samples if int(s["ts_ns"]) <= end_ns and any(s.get(c) for c in WALLET_COLS)]
    pts = [s for s in pts if day_of(s["ts_ns"]) >= BUYBACK_START]
    if len(pts) < 2:
        return None
    start, end = pts[0], pts[-1]
    d0, d1 = day_of(start["ts_ns"]), day_of(end["ts_ns"])

    def held(s):
        return OrderedDict((c, int(s[c]) / YOCTO if s.get(c) else 0.0) for c in WALLET_COLS)

    h0, h1 = held(start), held(end)
    usd = near = 0.0
    for r in table:
        if d0 < r["date"] <= d1 and r.get("intents_holders_revenue_usd") and r.get("price_usd"):
            usd += r["intents_holders_revenue_usd"]
            near += r["intents_holders_revenue_usd"] / r["price_usd"]
    rise = sum(h1.values()) - sum(h0.values())
    return OrderedDict(
        start_date=d0, end_date=d1, start_height=int(start["height"]), end_height=int(end["height"]),
        start_near=h0, end_near=h1, start_total_near=sum(h0.values()), end_total_near=sum(h1.values()),
        balance_rise_near=rise, buyback_usd=usd, buyback_near_at_daily_price=near,
        rise_over_buyback=rise / near if near else None,
    )


def yearly(table, as_of):
    years = OrderedDict()
    first = next((r["date"] for r in table if r.get("epochs")), None)  # first day the ledger covers
    for r in table:
        if r["date"] > as_of:
            break
        if first is None or r["date"] < first:
            continue  # no header data for this day, so no row of the year table may count it
        y = years.setdefault(r["date"][:4], defaultdict(float))
        price = r.get("price_usd") or 0.0
        y["minted_near"] += r.get("minted_near") or 0.0
        y["burnt_near"] += r.get("burnt_near") or 0.0
        y["issuance_usd"] += (r.get("minted_near") or 0.0) * price
        y["burn_usd"] += (r.get("burnt_near") or 0.0) * price
        for k in ("intents_volume_usd", "intents_fees_usd", "intents_revenue_usd", "intents_holders_revenue_usd"):
            y[k] += r.get(k) or 0.0
        y["days"] += 1
        y.setdefault("first_day", r["date"])
        y["supply_end_near"] = r["supply_near"] or y["supply_end_near"]
    rows = []
    for year, y in years.items():
        if not y["minted_near"]:
            continue
        rows.append(OrderedDict(
            year=year, first_day=y["first_day"], days=int(y["days"]), minted_near=y["minted_near"], burnt_near=y["burnt_near"],
            burn_cover=y["burnt_near"] / y["minted_near"], supply_end_near=y["supply_end_near"],
            issuance_usd=y["issuance_usd"], burn_usd=y["burn_usd"],
            intents_volume_usd=y["intents_volume_usd"], intents_revenue_usd=y["intents_revenue_usd"],
            intents_holders_revenue_usd=y["intents_holders_revenue_usd"],
            total_cover=(y["burn_usd"] + y["intents_holders_revenue_usd"]) / y["issuance_usd"]
            if y["issuance_usd"] else None,
        ))
    return rows


def build(raw_dir, out_dir, today=None):
    epochs = read_csv(os.path.join(raw_dir, "epochs.csv"))
    samples = read_csv(os.path.join(raw_dir, "samples.csv"))
    ledger, skipped = epoch_ledger(epochs)
    table = daily_table(ledger, raw_dir)

    # as_of: the last day every source has, never the current (incomplete) day.
    today = today or day_of(epochs[-1]["last_ts_ns"])
    last = {k: max((r["date"] for r in table if r.get(k) is not None), default=None)
            for k in ("price_usd", "intents_volume_usd", "intents_revenue_usd", "txns")}
    last_full_epoch_day = max(r["date"] for r in table if r["epochs"] and r["date"] < today)
    as_of = min([last_full_epoch_day] + [v for v in last.values() if v])
    idx = next(i for i, r in enumerate(table) if r["date"] == as_of)
    end_ns = int(dt.datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() + 86400) * 10**9

    w = {f"d{n}": window(table, idx, n) for n in (7, 30, 90, 365)}
    prior90 = window(table, idx - 90, 90)
    prior_year90 = window(table, idx - 365, 90)
    row = table[idx]
    supply, price = row["supply_near"], row["price_usd"]
    d90 = w["d90"]

    latest = [s for s in samples if s.get("total_stake") and int(s["ts_ns"]) <= end_ns][-1]
    stake = int(latest["total_stake"]) / YOCTO
    staked_share = stake / supply
    staking = OrderedDict(
        sample_height=int(latest["height"]), sample_date=day_of(latest["ts_ns"]),
        validators=int(latest["validators"]), total_stake_near=stake, staked_share=staked_share,
        nakamoto_33=int(latest["nakamoto_33"]),
        top10_share=int(latest["top10_stake"]) / int(latest["total_stake"]),
        gross_staker_apr=d90["issuance_rate"] / staked_share,
    )
    for col, name in (("linear_price", "linear"), ("stnear_price", "stnear")):
        for n in (30, 90, 365):
            staking[f"{name}_apr_{n}d"] = lst_apr(samples, col, end_ns, n)
    realized = [staking[k] for k in ("linear_apr_90d", "stnear_apr_90d") if staking.get(k) is not None]
    staking["realized_staker_apr_90d"] = sum(realized) / len(realized) if realized else None
    if realized:
        staking["real_staker_yield_90d"] = staking["realized_staker_apr_90d"] - d90["net_supply_growth_rate"]

    args = dict(supply_near=supply, issuance_rate=d90["issuance_rate"], burn_near_per_day=d90["burn_near_per_day"],
                capture_rate=d90["capture_rate"])
    be_vol = model.breakeven_volume_usd_per_day(price_usd=price, **args)
    vol_now = d90["intents_volume_usd_per_day"]
    yoy = vol_now / prior_year90["intents_volume_usd_per_day"] - 1 if prior_year90["intents_volume_usd"] else None
    qoq = vol_now / prior90["intents_volume_usd_per_day"] - 1 if prior90["intents_volume_usd"] else None
    mcap = supply * price
    thesis = OrderedDict(
        price_usd=price, supply_near=supply, market_cap_usd=mcap,
        coverage_90d=model.coverage(price_usd=price, intents_volume_usd_per_day=vol_now, **args),
        breakeven_volume_usd_per_day=be_vol,
        breakeven_multiple=be_vol / vol_now,
        breakeven_price_usd=model.breakeven_price_usd(intents_volume_usd_per_day=vol_now, **args),
        intents_volume_growth_yoy=yoy, intents_volume_growth_qoq=qoq,
        years_to_breakeven_at_yoy=model.years_to_reach(vol_now, be_vol, yoy) if yoy is not None else None,
        years_to_breakeven_at_half_yoy=model.years_to_reach(vol_now, be_vol, yoy / 2) if yoy is not None else None,
        mcap_to_annualised_buyback=mcap / (d90["intents_holders_revenue_usd"] / 90 * model.DAYS_PER_YEAR)
        if d90["intents_holders_revenue_usd"] else None,
        mcap_to_annualised_gross_fees=mcap / (d90["intents_fees_usd"] / 90 * model.DAYS_PER_YEAR)
        if d90["intents_fees_usd"] else None,
        net_dilution_rate_90d=model.net_dilution_rate(d90["issuance_rate"], d90["total_cover"]),
    )

    nb = row.get("nb_supply_near")
    llama_gas = sum(r.get("chain_fees_usd") or 0.0 for r in table[idx - 89: idx + 1])
    nb_gas = sum((r.get("fees_paid_near") or 0.0) * (r.get("price_usd") or 0.0) for r in table[idx - 89: idx + 1])
    checks = OrderedDict(
        epochs_total=len(epochs), epochs_in_ledger=len(ledger), epochs_without_first_block=skipped,
        first_epoch_date=ledger[0]["date"], last_epoch_date=ledger[-1]["date"],
        header_supply_near=supply, nearblocks_supply_near=nb,
        supply_rel_diff=abs(supply - nb) / supply if nb else None,
        sum_minted_minus_burnt_near=sum(e["minted_near"] - e["burnt_near"] for e in ledger),
        supply_change_over_ledger_near=ledger[-1]["supply_near"] - int(epochs[0]["last_supply"]) / YOCTO,
        llama_gas_fees_usd_90d=llama_gas, nearblocks_gas_fees_usd_90d=nb_gas,
        gas_fee_sources_ratio=llama_gas / nb_gas if nb_gas else None,
    )

    metrics = OrderedDict(as_of=as_of, windows=w, prior_90d=prior90, prior_year_90d=prior_year90,
                          staking=staking, thesis=thesis, checks=checks,
                          wallets=wallet_check(samples, table, end_ns), yearly=yearly(table, as_of))

    os.makedirs(out_dir, exist_ok=True)
    write_table(os.path.join(out_dir, "epochs_ledger.csv"), ledger)
    cols = ["date", "epochs", "minted_near", "burnt_near", "supply_near", "price_usd", "txns", "active_accounts",
            "new_accounts", "fees_paid_near", "intents_volume_usd", "intents_fees_usd", "intents_revenue_usd",
            "intents_holders_revenue_usd", "chain_fees_usd", "stablecoins_usd", "tvl_usd"]
    write_table(os.path.join(out_dir, "daily.csv"), [r for r in table if r["date"] <= as_of], cols)
    write_table(os.path.join(out_dir, "yearly.csv"), metrics["yearly"])
    with open(os.path.join(out_dir, "metrics.json"), "w", newline="\n") as fh:
        json.dump(metrics, fh, indent=1)
        fh.write("\n")
    return metrics, table, ledger, samples


def write_table(path, rows, cols=None):
    cols = cols or list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in cols])


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(round(v, 6))
    return v
