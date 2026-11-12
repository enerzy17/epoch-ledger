"""Walk every NEAR epoch backwards from the chain tip and record supply at its edges.

Method (all facts read from block headers, no indexer):

* A header carries `total_supply`, `epoch_id` and `next_epoch_id`.
* The id of an epoch is the hash of the last block of the epoch two before it,
  so for a block in epoch e, `next_epoch_id` is the hash of the last block of
  epoch e-1. One request per epoch walks the whole chain.
* Rewards for epoch e-1 are minted in the first block of epoch e. Inside an
  epoch the supply only falls, by the gas fees burnt in each block.

So for an epoch e, with L(e) its last block and F(e) its first block:

    minted_at_start(e) = supply(F(e)) - supply(L(e-1)) + burnt_in(F(e))
    burnt_in_epoch(e)  = supply(F(e)) - supply(L(e))   + burnt_in(F(e))

`burnt_in(F(e))` is the sum of `balance_burnt` over the chunks included in
that block. tests/test_live_identity.py checks the per-block identity this
relies on against the live chain.

Output: data/raw/epochs.csv, one row per completed epoch, oldest first.
The file is a cache and the walk is resumable: a later run fetches only the
epochs newer than the newest row, and older ones only when asked (--since).
"""
import calendar
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import rpc

FIELDS = [
    "epoch_id", "prev_epoch_last_hash", "last_height", "last_ts_ns", "last_supply",
    "first_height", "first_ts_ns", "first_supply", "first_block_burnt",
    "protocol_version",
]
ZERO_HASH = "11111111111111111111111111111111"


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return sorted(csv.DictReader(fh), key=lambda r: int(r["last_height"]))


def save(path, rows):
    rows = sorted(rows, key=lambda r: int(r["last_height"]))
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)
    return rows


def _last_row(header):
    return {
        "epoch_id": header["epoch_id"],
        "prev_epoch_last_hash": header["next_epoch_id"],
        "last_height": str(header["height"]),
        "last_ts_ns": str(header["timestamp"]),
        "last_supply": header["total_supply"],
        "protocol_version": str(header["latest_protocol_version"]),
    }


def walk(pointer, known_ids, stop_before_ns=0, limit=None):
    """Follow `pointer` (hash of an epoch's last block) backwards, yielding rows."""
    n = 0
    while pointer and pointer != ZERO_HASH:
        try:
            header = rpc.block(pointer)["header"]
        except rpc.RpcError:
            return  # older than anything the archival node serves
        if header["epoch_id"] in known_ids or header["timestamp"] < stop_before_ns:
            return
        yield _last_row(header)
        n += 1
        if limit and n >= limit:
            return
        pointer = header["next_epoch_id"]


def block_burnt(blk):
    """NEAR burnt by the chunks newly included in this block, in yoctoNEAR."""
    h = blk["header"]["height"]
    return sum(int(c["balance_burnt"]) for c in blk["chunks"] if c["height_included"] == h)


def fetch_first_block(prev_last_height, epoch_id):
    """First block of the epoch that follows the block at prev_last_height."""
    for step in range(1, 40):
        blk = rpc.block_by_height(prev_last_height + step)
        if blk is None:
            continue  # skipped height
        h = blk["header"]
        if h["epoch_id"] != epoch_id:
            return None
        return {
            "first_height": str(h["height"]), "first_ts_ns": str(h["timestamp"]),
            "first_supply": h["total_supply"], "first_block_burnt": str(block_burnt(blk)),
        }
    return None


DEFAULT_DAYS = 500  # how far back a first run goes when --since is not given


def update(path, since=None, limit=None, log=print):
    """Bring epochs.csv up to date.

    With a cache present, only epochs newer than the newest row are fetched,
    unless `since` (YYYY-MM-DD) asks for older history than the cache holds.
    With no cache, the walk goes back to `since`, or DEFAULT_DAYS days.

    The backward walk (one request per epoch) and the first-block reads (one
    more per epoch) run side by side.
    """
    rows = load(path)
    if since:
        stop_ns = calendar.timegm(time.strptime(since, "%Y-%m-%d")) * 10**9
    elif rows:
        stop_ns = int(rows[0]["last_ts_ns"])  # nothing older than the cache
    else:
        stop_ns = int(time.time() - DEFAULT_DAYS * 86400) * 10**9
    by_id = {r["epoch_id"]: r for r in rows}
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=3)
    pending = []

    def want_first(newer, older):
        """Queue the first-block read for `newer`, the epoch right after `older`."""
        if newer is not None and not newer.get("first_height"):
            pending.append(pool.submit(
                lambda: newer.update(fetch_first_block(int(older["last_height"]), newer["epoch_id"]) or {})))

    ordered = sorted(by_id.values(), key=lambda r: int(r["last_height"]))
    for older, newer in zip(ordered, ordered[1:]):
        if newer["prev_epoch_last_hash"] and not newer.get("first_height"):
            want_first(newer, older)

    def run(pointer, newer, label):
        added = 0
        for row in walk(pointer, by_id, stop_ns, limit):
            by_id[row["epoch_id"]] = row
            want_first(newer, row)
            newer = row
            added += 1
            if added % 100 == 0:
                save(path, by_id.values())
                day = time.strftime("%Y-%m-%d", time.gmtime(int(row["last_ts_ns"]) / 1e9))
                log(f"  {label}: {added} epochs, back to {day} ({time.time() - t0:.0f}s)")
        return added, newer

    tip = rpc.block()["header"]
    new_head, last_new = run(tip["next_epoch_id"], None, "newer")
    if ordered and last_new is not None:
        want_first(last_new, ordered[-1])  # stitch the new rows onto the cached ones
    new_tail = 0
    if ordered and not limit and int(ordered[0]["last_ts_ns"]) > stop_ns:
        new_tail, _ = run(ordered[0]["prev_epoch_last_hash"], ordered[0], "older")
    log(f"  epochs: {len(by_id)} total, {new_head} newer and {new_tail} older fetched; "
        f"waiting for {sum(not p.done() for p in pending)} first-block reads")
    for n, p in enumerate(pending, 1):
        p.result()
        if n % 200 == 0:
            save(path, by_id.values())
    pool.shutdown()
    return save(path, by_id.values())


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "raw", "epochs.csv")
    since = sys.argv[2] if len(sys.argv) > 2 else None
    got = update(out, since=since, limit=int(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(f"{len(got)} epochs in {out}")
