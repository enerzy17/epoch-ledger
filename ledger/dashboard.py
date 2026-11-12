"""Render docs/index.html: one self-contained page, no CDN, no build step.

The page embeds the series it draws as JSON, so it opens from disk
(file://) and from any static host. Charts are plain SVG drawn by the small
script in dashboard_template.html. Every chart carries its takeaway, the
query behind it and a table of the plotted values.
"""
import json
import os

from . import build, claims, model

HERE = os.path.dirname(os.path.abspath(__file__))


def r(x, n=6):
    return None if x is None else round(x, n)


def weekly_points(table, idx, first_idx, step=7):
    pts = list(range(idx, first_idx - 1, -step))
    return sorted(pts)


def series(metrics, table, samples):
    as_of = metrics["as_of"]
    idx = next(i for i, row in enumerate(table) if row["date"] == as_of)
    led = [i for i, row in enumerate(table) if row["epochs"]]
    first_led = led[0]
    out = {}

    # 1. realised issuance rate, 30-day windows, weekly
    xs, rate, net = [], [], []
    for i in weekly_points(table, idx, first_led + 30):
        w = build.window(table, i, 30)
        xs.append(table[i]["date"])
        rate.append(r(w["issuance_rate"] * 100, 3))
        net.append(r(w["net_supply_growth_rate"] * 100, 3))
    out["issuance"] = {"x": xs, "issuance_rate": rate, "net_growth": net}

    # 2. monthly cover: what share of issuance came back through burn and buyback
    months = {}
    for row in table[first_led: idx + 1]:
        mo = months.setdefault(row["date"][:7], {"iss": 0.0, "burn": 0.0, "buy": 0.0, "days": 0})
        price = row.get("price_usd") or 0.0
        mo["iss"] += (row.get("minted_near") or 0.0) * price
        mo["burn"] += (row.get("burnt_near") or 0.0) * price
        mo["buy"] += row.get("intents_holders_revenue_usd") or 0.0
        mo["days"] += 1
    keys = [k for k in sorted(months) if months[k]["days"] >= 27 and months[k]["iss"] > 0][-30:]
    out["cover"] = {
        "x": keys,
        "burn": [r(months[k]["burn"] / months[k]["iss"] * 100, 3) for k in keys],
        "buyback": [r(months[k]["buy"] / months[k]["iss"] * 100, 3) for k in keys],
    }

    # 3. Intents volume against the volume that would offset issuance, 30-day windows
    xs, vol, need = [], [], []
    # Break-even only means something once revenue is used to buy NEAR, so the
    # series starts with the first full 30-day window after the first buyback day.
    first_rev = next((i for i, row in enumerate(table) if (row.get("intents_holders_revenue_usd") or 0) > 0), None)
    if first_rev is not None:
        for i in weekly_points(table, idx, max(first_led, first_rev) + 30):
            w = build.window(table, i, 30)
            if not w["intents_holders_revenue_usd"]:
                continue
            xs.append(table[i]["date"])
            vol.append(r(w["intents_volume_usd_per_day"] / 1e6, 2))
            gap = w["issuance_usd"] - w["burn_usd"]
            need.append(r(w["intents_volume_usd_per_day"] * gap / w["intents_holders_revenue_usd"] / 1e6, 2))
    out["volume"] = {"x": xs, "volume": vol, "breakeven": need}

    # 4. what a staker earned against net supply growth, 91-day windows at each weekly sample
    xs, apr, growth = [], [], []
    pts = [s for s in samples if (s.get("linear_price") or s.get("stnear_price"))]
    by_date = {row["date"]: i for i, row in enumerate(table)}
    for j, s in enumerate(pts):
        day = build.day_of(s["ts_ns"])
        if day > as_of or j < 13 or day not in by_date or by_date[day] - 91 < first_led:
            continue
        old = pts[j - 13]
        span = (int(s["ts_ns"]) - int(old["ts_ns"])) / 1e9 / 86400
        vals = []
        for col in ("linear_price", "stnear_price"):
            if s.get(col) and old.get(col):
                vals.append((int(s[col]) / int(old[col])) ** (model.DAYS_PER_YEAR / span) - 1)
        if not vals or not 80 < span < 100:
            continue
        w = build.window(table, by_date[day], 91)
        xs.append(day)
        apr.append(r(sum(vals) / len(vals) * 100, 3))
        growth.append(r(w["net_supply_growth_rate"] * 100, 3))
    out["staking"] = {"x": xs, "staker_apr": apr, "net_growth": growth}

    # 5. base-layer activity, 7-day averages (third-party indexer)
    xs, tx, acc = [], [], []
    start = max(0, idx - 7 * 156)
    for i in weekly_points(table, idx, start + 7):
        w = build.window(table, i, 7)
        xs.append(table[i]["date"])
        tx.append(r(w["txns_per_day"] / 1e6, 3))
        acc.append(r(w["active_accounts_per_day"] / 1e6, 3))
    out["activity"] = {"x": xs, "txns": tx, "accounts": acc}

    # validator set over time
    vs = [s for s in samples if s.get("validators") and build.day_of(s["ts_ns"]) <= as_of]
    out["validators"] = {"x": [build.day_of(s["ts_ns"]) for s in vs],
                         "nakamoto": [int(s["nakamoto_33"]) for s in vs],
                         "count": [int(s["validators"]) for s in vs]}
    return out


def payload(metrics, table, samples, manifest):
    d90, st, th = metrics["windows"]["d90"], metrics["staking"], metrics["thesis"]
    d365 = metrics["windows"]["d365"]
    cl = claims.claims(metrics)
    return {
        "as_of": metrics["as_of"],
        "pulled_at": manifest.get("pulled_at"),
        "tiles": [
            {"label": "Issuance, annual rate", "value": claims.pct(d90["issuance_rate"]),
             "note": "minted over 90 days, from block headers"},
            {"label": "Offset by gas burn", "value": claims.pct(d90["burn_cover"]),
             "note": "share of issuance burnt as fees"},
            {"label": "Offset by Intents buyback", "value": claims.pct(d90["buyback_cover"], 1),
             "note": "captured revenue used to buy NEAR"},
            {"label": "Net dilution", "value": claims.pct(th["net_dilution_rate_90d"]),
             "note": "annual, after burn and buyback"},
            {"label": "Break-even multiple", "value": f"{th['breakeven_multiple']:.1f}x",
             "note": "Intents volume needed, against today"},
            {"label": "Staker real yield", "value": claims.pct(st.get("real_staker_yield_90d")),
             "note": "liquid staking growth less net supply growth"},
        ],
        "model": {
            "supply": r(th["supply_near"], 0), "issuance_rate": r(d90["issuance_rate"], 6),
            "price": r(th["price_usd"], 4), "burn_per_day": r(d90["burn_near_per_day"], 2),
            "volume_per_day": r(d90["intents_volume_usd_per_day"], 0), "capture_rate": r(d90["capture_rate"], 8),
            "yoy": r(th["intents_volume_growth_yoy"], 4),
        },
        "claims": cl,
        "kills": claims.kill_criteria(metrics),
        "series": series(metrics, table, samples),
        "text": {
            "issuance": f"NEAR now mints {claims.pct(d90['issuance_rate'])} of supply a year, and burnt gas "
                        f"takes back only {claims.pct(d90['burn_cover'])} of that.",
            "cover": f"In the last 90 days {claims.pct(d90['total_cover'], 1)} of issuance came back to holders, "
                     f"almost all of it through the Intents buyback, not gas.",
            "volume": f"Over 90 days Intents ran at {claims.usd(d90['intents_volume_usd_per_day'])} a day; "
                      f"{claims.usd(th['breakeven_volume_usd_per_day'])} a day would offset all issuance, "
                      f"{th['breakeven_multiple']:.1f} times as much. The lines below use 30-day windows, "
                      f"so their last points differ from the 90-day figures.",
            "staking": f"Stakers earned {claims.pct(st['realized_staker_apr_90d'])} a year after pool fees, "
                       f"{claims.pct(st.get('real_staker_yield_90d'))} more than supply grew. "
                       f"Holders who do not stake lose {claims.pct(th['net_dilution_rate_90d'])} a year.",
            "activity": f"The base layer averaged {d90['txns_per_day'] / 1e6:.2f}M transactions a day from "
                        f"{d90['active_accounts_per_day'] / 1e3:,.0f}K active accounts over 90 days, against "
                        f"{d365['txns_per_day'] / 1e6:.2f}M and {d365['active_accounts_per_day'] / 1e3:,.0f}K "
                        f"over 365 days. Intents volume does not depend on base-layer transaction counts.",
        },
        "sources": manifest.get("files", {}),
    }


def render(metrics, table, samples, manifest, out_path):
    with open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8") as fh:
        html = fh.read()
    data = json.dumps(payload(metrics, table, samples, manifest), separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("__PAYLOAD__", data)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return out_path
