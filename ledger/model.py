"""The holder's ledger as four pure functions. No I/O, no hidden inputs.

A NEAR holder is diluted by issuance and paid back by two flows:
gas fees that are burnt, and NEAR bought on the market with captured
NEAR Intents revenue. Everything is per day.

    issuance_near  = supply * issuance_rate / 365.25
    buyback_near   = intents_volume_usd * capture_rate / price
    coverage       = (burn_near + buyback_near) / issuance_near

`capture_rate` is captured revenue divided by Intents volume. `price` is USD
per NEAR. The dashboard runs the same formulas in the browser, and
tests/test_model.py pins them.
"""
import math

DAYS_PER_YEAR = 365.25


def issuance_near_per_day(supply_near, issuance_rate):
    return supply_near * issuance_rate / DAYS_PER_YEAR


def coverage(supply_near, issuance_rate, price_usd, burn_near_per_day,
             intents_volume_usd_per_day, capture_rate):
    """Share of daily issuance that is returned to holders (1.0 = no net dilution)."""
    issued = issuance_near_per_day(supply_near, issuance_rate)
    bought = intents_volume_usd_per_day * capture_rate / price_usd
    return (burn_near_per_day + bought) / issued


def breakeven_volume_usd_per_day(supply_near, issuance_rate, price_usd, burn_near_per_day, capture_rate):
    """Intents volume per day at which burn plus buyback equals issuance."""
    gap_near = issuance_near_per_day(supply_near, issuance_rate) - burn_near_per_day
    return max(gap_near, 0.0) * price_usd / capture_rate


def breakeven_price_usd(supply_near, issuance_rate, burn_near_per_day,
                        intents_volume_usd_per_day, capture_rate):
    """NEAR price at which today's volume would fully offset issuance.

    Issuance is fixed in NEAR, the buyback is fixed in USD, so a lower price
    buys back more NEAR. Above this price holders are net diluted.
    """
    gap_near = issuance_near_per_day(supply_near, issuance_rate) - burn_near_per_day
    if gap_near <= 0:
        return math.inf
    return intents_volume_usd_per_day * capture_rate / gap_near


def years_to_reach(current, target, annual_growth):
    """Years of compounding at `annual_growth` (0.5 = +50%/yr) to go from current to target."""
    if current >= target:
        return 0.0
    if annual_growth <= 0 or current <= 0:
        return math.inf
    return math.log(target / current) / math.log(1 + annual_growth)


def net_dilution_rate(issuance_rate, cover):
    """Annual supply growth a holder actually bears once burn and buyback are netted off."""
    return issuance_rate * (1 - cover)
