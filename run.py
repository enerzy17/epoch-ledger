#!/usr/bin/env python3
"""Rebuild everything: pull raw data, compute the ledger, check the claims, render the dashboard.

    python run.py              pull fresh data, then build (first full pull takes about 90 minutes,
                               later pulls only fetch new epochs and take a minute or two)
    python run.py --offline    no network: rebuild every output from the committed data/raw snapshot
    python run.py --since 2025-10-01   bound how far back a fresh chain walk goes

Needs Python 3.9 or newer and nothing else. No API keys.
Exit code is 1 if any claim in ledger/claims.py no longer holds.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from ledger import build, claims, dashboard, epochs, report, samples, sources  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw")
DERIVED = os.path.join(ROOT, "data", "derived")
MANIFEST = os.path.join(RAW, "MANIFEST.json")
CHAIN = "NEAR mainnet block headers: archival JSON-RPC and neardata.xyz (ledger/rpc.py)"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows_in(path):
    with open(path, "rb") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def pull(since):
    os.makedirs(RAW, exist_ok=True)
    print("[1/5] chain: walking epochs from block headers")
    ep = epochs.update(os.path.join(RAW, "epochs.csv"), since=since)
    print("[2/5] chain: weekly validator and staking samples")
    samples.update(ep, os.path.join(RAW, "samples.csv"))
    print("[3/5] third-party series (NearBlocks, DefiLlama)")
    info = sources.pull(RAW)
    info["epochs"] = {"url": CHAIN}
    info["samples"] = {"url": "validators RPC; ft_price on linear-protocol.near; get_st_near_price on meta-pool.near; view_account on the three revenue accounts"}
    files = {}
    for name, meta in info.items():
        fname = name + (".json" if name == "llama_methodology" else ".csv")
        path = os.path.join(RAW, fname)
        files[fname] = {"url": meta["url"], "sha256": sha256(path),
                        "rows": meta.get("rows") if fname.endswith(".json") else rows_in(path)}
    manifest = {"pulled_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    with open(MANIFEST, "w", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return manifest


def check_snapshot(manifest):
    """Offline runs refuse to build from files that do not match the manifest."""
    bad = [name for name, meta in manifest["files"].items()
           if sha256(os.path.join(RAW, name)) != meta["sha256"]]
    if bad:
        sys.exit(f"data/raw does not match MANIFEST.json: {', '.join(bad)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip the network, rebuild from data/raw")
    ap.add_argument("--since", help="YYYY-MM-DD, oldest day a fresh chain walk should reach")
    args = ap.parse_args()

    if args.offline:
        with open(MANIFEST) as fh:
            manifest = json.load(fh)
        check_snapshot(manifest)
        print(f"[1-3/5] offline: using the snapshot pulled {manifest['pulled_at']} (hashes match)")
    else:
        manifest = pull(args.since)

    print("[4/5] building the ledger and metrics")
    metrics, table, ledger, sample_rows = build.build(RAW, DERIVED)
    cl, kills = claims.claims(metrics), claims.kill_criteria(metrics)
    with open(os.path.join(DERIVED, "claims.json"), "w", newline="\n") as fh:
        json.dump({"as_of": metrics["as_of"], "claims": cl, "kill_criteria": kills}, fh, indent=1)
        fh.write("\n")

    print("[5/5] writing RESULTS.md, THESIS.md, README block and docs/index.html")
    report.write_all(ROOT, metrics, cl, kills, manifest)
    dashboard.render(metrics, table, sample_rows, manifest, os.path.join(ROOT, "docs", "index.html"))

    print(f"\nData through {metrics['as_of']}. {len(ledger):,} epochs in the ledger.\n")
    for c in cl:
        print(f"  {'PASS' if c['holds'] else 'FAIL'}  {c['id']}  {c['claim']}")
    print()
    for k in kills:
        print(f"  {'TRIPPED    ' if k['tripped'] else 'not tripped'}  {k['id']}  {k['criterion']}  (now: {k['now']})")
    failed = [c["id"] for c in cl if not c["holds"]]
    if failed:
        print(f"\n{len(failed)} claim(s) no longer hold: {', '.join(failed)}")
        return 1
    print(f"\nAll {len(cl)} claims hold. Open docs/index.html.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
