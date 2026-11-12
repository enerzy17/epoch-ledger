"""Minimal NEAR chain reader. Standard library only, no API key needed.

Three free public services are used, each paced on its own clock because
their free tiers allow roughly one request per second and answer HTTP 429
(with a short block) when pushed harder:

* archival JSON-RPC at fastnear.com: blocks by hash or height, validators, view calls
* archival JSON-RPC at tatum.io: slow second source, used when the first is resting
* neardata.xyz: block headers by height, straight from FastNEAR's block archive

Environment (all optional):
  NEAR_RPC_URLS      comma-separated archival RPC endpoints, replaces the defaults
  NEAR_RPC_INTERVAL  seconds between requests to one host (default 1.15)
  FASTNEAR_API_KEY   sent as a Bearer token to fastnear.com and neardata.xyz
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENDPOINTS = [
    "https://archival-rpc.mainnet.fastnear.com",
    "https://near-mainnet.gateway.tatum.io",
]
NEARDATA = "https://mainnet.neardata.xyz"
SLOW_HOSTS = {"near-mainnet.gateway.tatum.io": 6.0, "mainnet.neardata.xyz": 4.0}
CHAIN_FACTS = ("UNKNOWN_BLOCK", "UNKNOWN_EPOCH", "UNKNOWN_CHUNK", "GARBAGE_COLLECTED_BLOCK",
               "UNKNOWN_ACCOUNT", "NO_CONTRACT_CODE", "CONTRACT_EXECUTION_ERROR")
PATIENCE_SECONDS = 1800  # how long one call keeps waiting for a provider before giving up

_lock = threading.Lock()
_free_at = {}  # host -> monotonic time of its next free slot


class RpcError(Exception):
    """The node answered, and the answer is a fact about the chain (for example UNKNOWN_BLOCK)."""


def endpoints():
    env = os.environ.get("NEAR_RPC_URLS")
    return [u.strip() for u in env.split(",") if u.strip()] if env else DEFAULT_ENDPOINTS


def _interval(host):
    return SLOW_HOSTS.get(host, float(os.environ.get("NEAR_RPC_INTERVAL", "1.15")))


def _reserve(urls):
    """Book the earliest free slot among `urls`. Returns (url, seconds to wait)."""
    with _lock:
        now = time.monotonic()
        best = min(urls, key=lambda u: max(_free_at.get(urllib.parse.urlsplit(u).netloc, 0.0), now))
        host = urllib.parse.urlsplit(best).netloc
        slot = max(_free_at.get(host, 0.0), now)
        _free_at[host] = slot + _interval(host)
        return best, slot - now


def _rest(url, seconds):
    host = urllib.parse.urlsplit(url).netloc
    with _lock:
        _free_at[host] = max(_free_at.get(host, 0.0), time.monotonic() + seconds)


def _send(urls, build_request, timeout=40):
    """Send to whichever provider is free first; rest a provider that pushes back."""
    deadline = time.monotonic() + PATIENCE_SECONDS
    last = None
    while time.monotonic() < deadline:
        url, wait = _reserve(urls)
        if wait > 0:
            time.sleep(wait)
        req = build_request(url)
        key = os.environ.get("FASTNEAR_API_KEY")
        if key and ("fastnear.com" in url or "neardata.xyz" in url):
            req.add_header("Authorization", "Bearer " + key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return url, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = exc
            _rest(url, 75 if exc.code == 429 else 10)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError) as exc:
            last = exc
            _rest(url, 5)
    raise RuntimeError(f"no provider answered within {PATIENCE_SECONDS}s: {last!r}")


def call(method, params, urls=None):
    body = json.dumps({"jsonrpc": "2.0", "id": "epoch-ledger", "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "epoch-ledger/1.0"}
    last = None
    for attempt in range(8):
        url, out = _send(urls or endpoints(), lambda u: urllib.request.Request(u, data=body, headers=headers))
        if "error" in out:
            err = out["error"]
            name = (err.get("cause") or {}).get("name", "")
            if name in CHAIN_FACTS:
                raise RpcError(name)
            last = json.dumps(err)[:300]
            _rest(url, 3 * (attempt + 1))
            continue
        result = out["result"]
        if isinstance(result, dict) and "error" in result and "result" not in result:
            raise RpcError(str(result["error"])[:200])  # view call failed inside the contract
        return result
    raise RuntimeError(f"RPC {method} kept failing: {last}")


def call_each(method, params):
    """Like `call`, but a "the chain does not have this" answer counts only if every provider gives it.

    Archival nodes differ in how much history they keep for validator sets and
    old state, so one node saying UNKNOWN_EPOCH is not yet a fact.
    """
    last = None
    for url in endpoints():
        try:
            return call(method, params, urls=[url])
        except RpcError as exc:
            last = exc
    raise last


def block(block_id=None):
    """block_id: int height, str hash, or None for the latest final block."""
    params = {"finality": "final"} if block_id is None else {"block_id": block_id}
    return call("block", params)


def block_by_height(height):
    """Header and chunk headers of the block at `height`, or None if that height was skipped.

    Asked of whichever is free first: neardata.xyz (GET) or an archival RPC
    node (`block` by height). Both return the same `header` and `chunks`.
    """
    height = int(height)
    body = json.dumps({"jsonrpc": "2.0", "id": "epoch-ledger", "method": "block",
                       "params": {"block_id": height}}).encode()
    neardata = f"{NEARDATA}/v0/block/{height}/headers"

    def request(url):
        if url == neardata:
            return urllib.request.Request(url, headers={"User-Agent": "epoch-ledger/1.0"})
        return urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                               "User-Agent": "epoch-ledger/1.0"})

    last = None
    for attempt in range(8):
        url, out = _send([neardata] + endpoints(), request)
        if url == neardata:
            return out  # a block, or null for a skipped height
        if "error" in out:
            name = (out["error"].get("cause") or {}).get("name", "")
            if name == "UNKNOWN_BLOCK":
                return None
            last = json.dumps(out["error"])[:300]
            _rest(url, 3 * (attempt + 1))
            continue
        return out["result"]
    raise RuntimeError(f"block {height} kept failing: {last}")


def validators(block_id=None):
    return call_each("validators", [None] if block_id is None else {"block_id": block_id})


def protocol_config(block_id=None):
    params = {"finality": "final"} if block_id is None else {"block_id": block_id}
    return call("EXPERIMENTAL_protocol_config", params)


def account_balance(account_id, block_id=None):
    """Liquid plus locked NEAR of an account in yoctoNEAR, or None if it did not exist at that block."""
    params = {"request_type": "view_account", "account_id": account_id}
    if block_id is None:
        params["finality"] = "final"
    else:
        params["block_id"] = block_id
    try:
        res = call_each("query", params)
    except RpcError:
        return None
    return int(res["amount"]) + int(res["locked"])


def view(account_id, method_name, args=None, block_id=None):
    """Read-only contract call. Returns the decoded JSON result."""
    params = {
        "request_type": "call_function",
        "account_id": account_id,
        "method_name": method_name,
        "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode(),
    }
    if block_id is None:
        params["finality"] = "final"
    else:
        params["block_id"] = block_id
    res = call_each("query", params)
    return json.loads(bytes(res["result"]).decode())
