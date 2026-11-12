"""Every number the thesis leans on, as a claim that is re-checked on each run.

A claim has an id, a sentence with the live value filled in, the test that
must hold for the sentence to stay true, and where the inputs come from.
`python run.py` fails (exit code 1) if any claim stops holding, so the
written thesis cannot quietly drift away from the data.

Kill criteria are different: they are the results that would make the author
drop the thesis. A tripped criterion does not fail the run. It is reported.
"""
from collections import OrderedDict

HEADERS = "NEAR block headers via archival RPC (data/raw/epochs.csv)"
SAMPLES = "validators RPC and staking pool view calls (data/raw/samples.csv)"
LLAMA = "DefiLlama near-intents adapter (data/raw/llama_intents_*.csv), third party"
WALLETS = "view_account on the accounts revenue.near.org lists (data/raw/samples.csv)"
NEARBLOCKS = "NearBlocks daily chart (data/raw/nearblocks_daily.csv), third party"


def pct(x, digits=2):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def usd(x):
    if x is None:
        return "n/a"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:,.2f}{unit}"
    return f"${x:,.2f}"


def near(x):
    if x is None:
        return "n/a"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"{x / div:,.2f}{unit} NEAR"
    return f"{x:,.0f} NEAR"


def _change(x):
    if x is None:
        return "with no figure for"
    return f"{abs(x) * 100:.0f}% {'above' if x >= 0 else 'below'}"


def claims(m):
    d90, d365, st, th, ck = m["windows"]["d90"], m["windows"]["d365"], m["staking"], m["thesis"], m["checks"]
    out = []

    def add(cid, text, ok, source, value):
        out.append(OrderedDict(id=cid, holds=bool(ok), claim=text, value=value, source=source))

    add("C01", f"Over the 90 days to {m['as_of']} the protocol minted {near(d90['minted_near'])}, "
               f"an annual issuance rate of {pct(d90['issuance_rate'])} of supply. "
               f"The protocol maximum is 2.5% a year.",
        0.015 < d90["issuance_rate"] < 0.03, HEADERS, d90["issuance_rate"])
    add("C02", f"Gas fees burnt in the same 90 days were {near(d90['burnt_near'])}, "
               f"which offsets {pct(d90['burn_cover'])} of issuance. Gas burn alone does not fund security.",
        d90["burn_cover"] < 0.05, HEADERS, d90["burn_cover"])
    add("C03", f"The ledger covers {ck['epochs_in_ledger']:,} epochs from {ck['first_epoch_date']} to "
               f"{ck['last_epoch_date']} with {ck['epochs_without_first_block']} epochs missing, and the header "
               f"supply differs from the NearBlocks supply figure by {pct(ck['supply_rel_diff'], 4)}.",
        ck["epochs_without_first_block"] == 0 and ck["supply_rel_diff"] is not None and ck["supply_rel_diff"] < 0.001,
        HEADERS + "; " + NEARBLOCKS, ck["supply_rel_diff"])
    add("C04", f"Of the gas fees users paid over 90 days ({near(d90['gas_fees_paid_near'])} per NearBlocks), "
               f"{pct(d90['burn_share_of_gas_fees'], 1)} left the supply according to block headers.",
        d90["burn_share_of_gas_fees"] is not None and 0.5 < d90["burn_share_of_gas_fees"] < 1.05,
        HEADERS + "; " + NEARBLOCKS, d90["burn_share_of_gas_fees"])
    add("C05", f"NEAR Intents settled {usd(d90['intents_volume_usd_per_day'])} a day over 90 days, "
               f"{_change(th['intents_volume_growth_yoy'])} the same 90 days a year earlier.",
        th["intents_volume_growth_yoy"] is not None and th["intents_volume_growth_yoy"] > 0, LLAMA,
        d90["intents_volume_usd_per_day"])
    add("C06", f"Users paid {pct(d90['gross_fee_rate'], 3)} of Intents volume in fees. NEAR captured "
               f"{pct(d90['capture_rate'], 3)} of volume as revenue; the rest stayed with solvers and front ends.",
        d90["capture_rate"] is not None and 0 < d90["capture_rate"] < d90["gross_fee_rate"], LLAMA,
        d90["capture_rate"])
    add("C07", f"Captured Intents revenue used to buy NEAR was {usd(d90['intents_holders_revenue_usd'])} over 90 days, "
               f"{pct(d90['buyback_cover'], 1)} of the USD value of issuance. That is "
               f"{d90['buyback_cover'] / d90['burn_cover']:.0f} times what gas burn returned.",
        d90["buyback_cover"] > d90["burn_cover"], LLAMA + "; " + HEADERS, d90["buyback_cover"])
    add("C08", f"Burn plus buyback returned {pct(d90['total_cover'], 1)} of issuance, so holders were still diluted "
               f"at a net {pct(th['net_dilution_rate_90d'])} a year.",
        0 < d90["total_cover"] < 1, HEADERS + "; " + LLAMA, d90["total_cover"])
    add("C09", f"At the 90-day capture rate and a NEAR price of {usd(th['price_usd'])}, Intents volume of "
               f"{usd(th['breakeven_volume_usd_per_day'])} a day would offset all issuance. "
               f"That is {th['breakeven_multiple']:.1f} times the current run rate.",
        th["breakeven_multiple"] > 1, "ledger/model.py: breakeven_volume_usd_per_day", th["breakeven_multiple"])
    add("C10", f"Read the other way: today's Intents volume fully offsets issuance only if NEAR trades at "
               f"{usd(th['breakeven_price_usd'])} or lower. The market price is {usd(th['price_usd'])}.",
        th["breakeven_price_usd"] < th["price_usd"], "ledger/model.py: breakeven_price_usd",
        th["breakeven_price_usd"])
    add("C11", f"{pct(st['staked_share'], 1)} of supply is staked. Liquid staking share prices grew at "
               f"{pct(st['realized_staker_apr_90d'])} a year over 90 days (after pool fees), which is "
               f"{pct(st.get('real_staker_yield_90d'))} once net supply growth is taken off.",
        st.get("real_staker_yield_90d") is not None and st["real_staker_yield_90d"] > 0, SAMPLES,
        st.get("real_staker_yield_90d"))
    add("C12", f"{st['validators']} validators secure the chain. The {st['nakamoto_33']} largest hold more than a "
               f"third of stake and the ten largest hold {pct(st['top10_share'], 1)}.",
        st["nakamoto_33"] >= 1, SAMPLES, st["nakamoto_33"])
    add("C13", f"NEAR's market value is {usd(th['market_cap_usd'])}: "
               f"{th['mcap_to_annualised_buyback']:.0f} times annualised buyback revenue and "
               f"{th['mcap_to_annualised_gross_fees']:.0f} times annualised gross Intents fees.",
        th["mcap_to_annualised_buyback"] > th["mcap_to_annualised_gross_fees"],
        HEADERS + "; " + NEARBLOCKS + "; " + LLAMA, th["mcap_to_annualised_buyback"])
    add("C14", f"Base-layer activity over 90 days averaged {d90['txns_per_day']:,.0f} transactions and "
               f"{d90['active_accounts_per_day']:,.0f} active accounts a day; the 365-day averages are "
               f"{d365['txns_per_day']:,.0f} and {d365['active_accounts_per_day']:,.0f}.",
        d90["txns_per_day"] > 0, NEARBLOCKS, d90["txns_per_day"])
    wl = m.get("wallets")
    if wl:
        ratio = wl["rise_over_buyback"]
        add("C15", f"The three accounts that revenue.near.org lists for captured Intents revenue held "
                   f"{near(wl['start_total_near'])} on {wl['start_date']} and {near(wl['end_total_near'])} on "
                   f"{wl['end_date']}, read from chain. The rise of {near(wl['balance_rise_near'])} is "
                   f"{'n/a' if ratio is None else format(ratio, '.2f')} times the NEAR that DefiLlama's buyback "
                   f"revenue for the same days ({usd(wl['buyback_usd'])}) buys at daily prices.",
            ratio is not None and 0.7 <= ratio <= 1.3, WALLETS + "; " + LLAMA, ratio)
    else:
        add("C15", "The revenue accounts could not be compared with the buyback series: fewer than two weekly "
                   "samples since buybacks began.", False, WALLETS, None)
    return out


def kill_criteria(m):
    d90, st, th = m["windows"]["d90"], m["staking"], m["thesis"]
    p90 = m["prior_90d"]
    out = []

    def add(kid, text, tripped, value):
        out.append(OrderedDict(id=kid, tripped=bool(tripped), criterion=text, now=value))

    add("K1", "Intents volume over 90 days falls below the same 90 days a year earlier.",
        (th["intents_volume_growth_yoy"] or 0) < 0, _change(th["intents_volume_growth_yoy"]) + " a year earlier")
    add("K2", "The captured revenue rate falls below 0.02% of volume (fee competition takes the margin).",
        (d90["capture_rate"] or 0) < 0.0002, pct(d90["capture_rate"], 3))
    add("K3", "Buyback revenue falls below 10% of issuance for a 90-day window.",
        d90["buyback_cover"] < 0.10, pct(d90["buyback_cover"], 1))
    add("K4", "Realised issuance rises back above 3% a year (the 2025 inflation cut is undone).",
        d90["issuance_rate"] > 0.03, pct(d90["issuance_rate"]))
    add("K5", "The break-even multiple is higher than it was 90 days ago (volume is losing ground to issuance).",
        _multiple(d90) > _multiple(p90), f"{_multiple(d90):.1f}x now, {_multiple(p90):.1f}x in the prior 90 days")
    add("K6", "Fewer than 7 validators can hold a third of stake.",
        st["nakamoto_33"] < 7, str(st["nakamoto_33"]))
    return out


def _multiple(w):
    """Break-even multiple inside one window: issuance not covered by burn, over captured revenue."""
    gap = w["issuance_usd"] - w["burn_usd"]
    return gap / w["intents_revenue_usd"] if w["intents_revenue_usd"] else float("inf")
