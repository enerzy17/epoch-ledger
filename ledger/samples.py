"""Weekly on-chain samples: the validator set and liquid-staking share prices.

For one epoch per week (and always the latest), at that epoch's last block:

* `validators` RPC: number of validators, total stake, the share of the ten
  largest, and the Nakamoto coefficient (fewest validators that together hold
  more than one third of stake, the amount that can stop finality).
* `ft_price` on linear-protocol.near and `get_st_near_price` on meta-pool.near:
  NEAR per share of the two largest liquid staking tokens. The growth of that
  price is the yield a staker actually received, after pool fees.
* `view_account` on the three accounts that revenue.near.org lists as holding
  captured NEAR Intents revenue (from 2025 on). Their NEAR balance is an
  on-chain cross-check of the third-party revenue series.

Output: data/raw/samples.csv. Cached by epoch_id like epochs.csv. A sample with
an empty column means the node did not answer that question for that block
(for example the staking contract did not exist yet).
"""
import csv
import os

from . import rpc

FIELDS = ["epoch_id", "height", "ts_ns", "validators", "total_stake", "top10_stake",
          "nakamoto_33", "linear_price", "stnear_price",
          "buybacks_wallet", "fe_wallet", "onecs_wallet"]
# The three accounts revenue.near.org lists as holding captured Intents revenue.
REVENUE_WALLETS = (("buybacks_wallet", "buybacks.multisignature.near"),
                   ("fe_wallet", "fefundsadmin.sputnik-dao.near"),
                   ("onecs_wallet", "1csfundsadmin.sputnik-dao.near"))
WALLETS_SINCE_NS = 1735689600 * 10**9  # 2025-01-01, before any Intents fee existed
WEEK_NS = 7 * 86400 * 10**9


def pick(epochs):
    """One epoch per 7 days, oldest first, plus the newest epoch."""
    chosen, next_ts = [], 0
    for e in epochs:
        ts = int(e["last_ts_ns"])
        if ts >= next_ts:
            chosen.append(e)
            next_ts = ts + WEEK_NS
    if epochs and chosen[-1] is not epochs[-1]:
        chosen.append(epochs[-1])
    return chosen


def nakamoto(stakes, fraction=1 / 3):
    total, run = sum(stakes), 0
    for n, s in enumerate(sorted(stakes, reverse=True), 1):
        run += s
        if run > total * fraction:
            return n
    return len(stakes)


def sample(epoch):
    h = int(epoch["last_height"])
    row = {"epoch_id": epoch["epoch_id"], "height": str(h), "ts_ns": epoch["last_ts_ns"]}
    try:
        stakes = [int(v["stake"]) for v in rpc.validators(h)["current_validators"]]
        row.update(validators=len(stakes), total_stake=sum(stakes),
                   top10_stake=sum(sorted(stakes, reverse=True)[:10]), nakamoto_33=nakamoto(stakes))
    except (rpc.RpcError, RuntimeError):
        pass  # the node does not keep this epoch's validator info
    for col, acct, method in (("linear_price", "linear-protocol.near", "ft_price"),
                              ("stnear_price", "meta-pool.near", "get_st_near_price")):
        try:
            row[col] = rpc.view(acct, method, block_id=h)
        except (rpc.RpcError, RuntimeError, KeyError, ValueError):
            pass  # contract not deployed yet at this height
    if int(epoch["last_ts_ns"]) >= WALLETS_SINCE_NS:
        for col, acct in REVENUE_WALLETS:
            bal = rpc.account_balance(acct, h)
            if bal is not None:
                row[col] = str(bal)
    return row


def update(epochs, path, log=print):
    have = {}
    if os.path.exists(path):
        with open(path, newline="") as fh:
            have = {r["epoch_id"]: r for r in csv.DictReader(fh)}
    # A cached row without a validator count is asked again: one provider may have lacked that epoch.
    todo = [e for e in pick(epochs) if not have.get(e["epoch_id"], {}).get("validators")]
    log(f"  sampling {len(todo)} epochs ({len(have)} cached), newest first")

    def flush():
        rows = sorted(have.values(), key=lambda r: int(r["height"]))
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        return rows

    for n, epoch in enumerate(reversed(todo), 1):
        row = sample(epoch)
        have[row["epoch_id"]] = row
        if n % 25 == 0:
            flush()
            log(f"  samples {n}/{len(todo)}")
    return flush()
