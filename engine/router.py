"""The route API client (server-side only — its URL and key never reach a public response; see venues.py for the public names).
Base URL = ROUTE_API_URL (no default: empty = routing disabled, every call raises NotConfigured, an Upstream error the callers already
degrade on). The key (ROUTE_API_KEY) is sent in the header named by ROUTE_API_KEY_HEADER.

Budget (measured 15.11, no key): /advanced/routes 75 per 2 h, /quote 75 per 2 h, /advanced/stepTransaction 50 per 2 h, each its own counter
(`ratelimit-limit / -remaining / -reset` headers, reset in seconds). The client:
  - reads those headers on every answer and keeps per-bucket state (persisted to data/router-budget.json, survives restarts);
  - refuses a call before sending it when the bucket is below the caller's reserve (priority "user" may spend to 0, "board" keeps half);
  - after a 429 leaves the bucket alone until its reset;
  - caches answers by request key for a short TTL and coalesces identical in-flight requests (one HTTP call, many waiters).
Every call is logged (no key, no address) to data/router-calls.log."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.parse
from concurrent.futures import Future

import config as C
from chain import http_json

BUDGET_FILE = os.path.join(C.DATA, "router-budget.json")
CALL_LOG = os.path.join(C.DATA, "router-calls.log")
RESERVE = {"user": 0.0, "verify": 0.0, "board": 0.5, "reach": 0.5}      # share of the bucket limit a priority must leave untouched


class Budget(Exception):
    """The bucket is empty or paused: the caller serves stale data or says so."""

    def __init__(self, bucket: str, reset_in: int):
        super().__init__(f"{bucket} budget exhausted, reset in {reset_in}s")
        self.bucket, self.reset_in = bucket, reset_in


class Upstream(Exception):
    def __init__(self, code: int, body):
        super().__init__(f"upstream {code}")
        self.code, self.body = code, body


class NotConfigured(Upstream):
    """ROUTE_API_URL is empty: routing is unavailable on this server."""

    def __init__(self):
        super().__init__(0, {"message": "routing unavailable: ROUTE_API_URL is not set"})


_lock = threading.Lock()
_buckets: dict[str, dict] = {}
_cache: dict[str, tuple[float, object]] = {}
_inflight: dict[str, Future] = {}
STATS = {"calls": 0, "cached": 0, "coalesced": 0, "refused": 0, "r429": 0, "errors": 0}
RECENT: "deque[tuple[float, bool]]" = __import__("collections").deque(maxlen=200)    # (ts, ok) of upstream answers, for degraded.router


def unhealthy() -> bool:
    """audit v2 minor 3: >= 3 failed answers (5xx, network, timeout) in the last 5 min and more than half of them failed."""
    now = time.time()
    xs = [ok for t, ok in list(RECENT) if now - t < 300]
    bad = xs.count(False)
    return bad >= 3 and bad * 2 > len(xs)


def _load():
    try:
        d = json.load(open(BUDGET_FILE))
        now = time.time()
        for k, v in d.items():
            if v.get("reset_at", 0) > now:
                _buckets[k] = v
    except Exception:
        pass


def _save():
    try:
        os.makedirs(C.DATA, exist_ok=True)
        tmp = BUDGET_FILE + ".tmp"
        json.dump(_buckets, open(tmp, "w"))
        os.replace(tmp, BUDGET_FILE)
    except Exception:
        pass


_load()


def _bucket_of(path: str) -> str:
    if path.startswith("advanced/routes"):
        return "routes"
    if path.startswith("advanced/stepTransaction"):
        return "step"
    if path.startswith("quote"):
        return "quote"
    if path.startswith("status"):
        return "status"
    return "other"


def budget_state() -> dict:
    now = time.time()
    out = {}
    with _lock:
        for k, v in _buckets.items():
            if v.get("reset_at", 0) <= now:
                continue
            out[k] = {"limit": v.get("limit"), "remaining": v.get("remaining"), "resetIn": int(v["reset_at"] - now),
                      "paused": v.get("paused_until", 0) > now}
    return out


def _check(bucket: str, prio: str):
    now = time.time()
    with _lock:
        b = _buckets.get(bucket)
        if not b or b.get("reset_at", 0) <= now:
            return
        if b.get("paused_until", 0) > now:
            raise Budget(bucket, int(b["paused_until"] - now))
        lim = b.get("limit") or 75
        if b.get("remaining", lim) <= RESERVE.get(prio, 0) * lim:
            raise Budget(bucket, int(b["reset_at"] - now))


def _note(bucket: str, code: int, hdr: dict):
    h = {k.lower(): v for k, v in (hdr or {}).items()}
    now = time.time()
    with _lock:
        b = _buckets.setdefault(bucket, {})
        try:
            if "ratelimit-limit" in h:
                b["limit"] = int(h["ratelimit-limit"])
            if "ratelimit-remaining" in h:
                b["remaining"] = int(h["ratelimit-remaining"])
            if "ratelimit-reset" in h:
                b["reset_at"] = now + int(h["ratelimit-reset"])
        except ValueError:
            pass
        if code == 429:
            ra = h.get("retry-after") or h.get("ratelimit-reset") or "60"
            try:
                b["paused_until"] = now + max(5, int(float(ra)))
            except ValueError:
                b["paused_until"] = now + 60
            b["remaining"] = 0
            b.setdefault("reset_at", b["paused_until"])
        _save()


def _log(rec: dict):
    _rotate_log()
    try:
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _headers() -> dict:
    return {C.ROUTE_API_KEY_HEADER: C.ROUTE_API_KEY} if C.ROUTE_API_KEY and C.ROUTE_API_KEY_HEADER else {}


USER_TIMEOUT_S = 10          # audit v1 P1-2: on a user's path the provider gets at most 10 s; the board may wait longer


def _rotate_log():
    try:
        if os.path.getsize(CALL_LOG) > 5_000_000:
            os.replace(CALL_LOG, CALL_LOG + ".1")
    except OSError:
        pass


def _do(method: str, path: str, params: dict | None, body, prio: str, timeout: int):
    if not C.ROUTE_API_URL:
        STATS["notConfigured"] = STATS.get("notConfigured", 0) + 1
        raise NotConfigured()
    bucket = _bucket_of(path)
    if prio not in ("board", "reach"):          # background catalog reads (reach.py) may wait longer, like the board
        timeout = min(timeout, USER_TIMEOUT_S)
    _check(bucket, prio)
    url = C.ROUTE_API_URL + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    t0 = time.time()
    STATS["calls"] += 1
    try:
        code, j, hdr = http_json(url, body=body, headers=_headers(), timeout=timeout, method=method)
    except Exception as e:
        STATS["errors"] += 1
        RECENT.append((time.time(), False))
        _log({"ts": int(t0), "path": path.split("?")[0], "bucket": bucket, "prio": prio, "err": type(e).__name__})
        raise Upstream(0, {"message": type(e).__name__})
    _note(bucket, code, hdr)
    RECENT.append((time.time(), code < 500))
    hl = {k.lower(): v for k, v in hdr.items()}
    _log({"ts": int(t0), "path": path.split("?")[0], "bucket": bucket, "prio": prio, "code": code, "sec": round(time.time() - t0, 2),
          "remaining": hl.get("ratelimit-remaining"), "reset": hl.get("ratelimit-reset")})
    if code == 429:
        STATS["r429"] += 1
        raise Budget(bucket, int(float(hl.get("retry-after") or hl.get("ratelimit-reset") or 60)))
    if code != 200:
        raise Upstream(code, j)
    return j


def call(method: str, path: str, params: dict | None = None, body=None, prio: str = "user", ttl: float = C.QUOTE_TTL_S, timeout: int = 30):
    """Cached + coalesced call. ttl 0 = no cache (still coalesced while in flight)."""
    key = hashlib.sha1(json.dumps([method, path, params, body], sort_keys=True, default=str).encode()).hexdigest()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and ttl and now - hit[0] < ttl:
            STATS["cached"] += 1
            return hit[1]
        fut = _inflight.get(key)
        owner = fut is None
        if owner:
            fut = Future()
            _inflight[key] = fut
        else:
            STATS["coalesced"] += 1
    if not owner:
        return fut.result(timeout=timeout + 10)
    try:
        res = _do(method, path, params, body, prio, timeout)
        with _lock:
            if ttl:
                _cache[key] = (time.time(), res)
                if len(_cache) > 2000:
                    for k in sorted(_cache, key=lambda k: _cache[k][0])[:500]:
                        _cache.pop(k, None)
        fut.set_result(res)
        return res
    except BaseException as e:
        fut.set_exception(e)
        raise
    finally:
        with _lock:
            _inflight.pop(key, None)


def last_good(method: str, path: str, params=None, body=None):
    """The most recent cached answer for this exact request regardless of age (for stale serving), or None."""
    key = hashlib.sha1(json.dumps([method, path, params, body], sort_keys=True, default=str).encode()).hexdigest()
    with _lock:
        hit = _cache.get(key)
    return hit if hit else None


def _t(timeout: float | None, default: float) -> float:
    """A per-quote deadline passes the seconds left; nothing left -> the call is not made (Upstream 0, 'deadline')."""
    if timeout is None:
        return default
    if timeout < 0.5:
        raise Upstream(0, {"message": "deadline"})
    return timeout


# ------------------------------------------------------------------ endpoints
def routes(from_chain: int, from_token: str, to_chain: int, to_token: str, amount: int, from_address: str | None, to_address: str | None = None,
           prio: str = "user", allow_exchanges: list[str] | None = None, order: str = "CHEAPEST", timeout: float | None = None):
    body = {"fromChainId": from_chain, "toChainId": to_chain, "fromTokenAddress": from_token, "toTokenAddress": to_token,
            "fromAmount": str(amount), "options": {"slippage": C.SLIPPAGE, "order": order, "allowSwitchChain": False}}
    if from_address:
        body["fromAddress"] = from_address
    if to_address:
        body["toAddress"] = to_address
    if allow_exchanges:
        body["options"]["exchanges"] = {"allow": allow_exchanges}
    return call("POST", "advanced/routes", body=body, prio=prio, timeout=_t(timeout, 45))


def step_transaction(step: dict, prio: str = "user", timeout: float | None = None):
    return call("POST", "advanced/stepTransaction", body=step, prio=prio, ttl=C.QUOTE_TTL_S, timeout=_t(timeout, 40))


def quote(from_chain: int, from_token: str, to_chain: int, to_token: str, amount: int, from_address: str, tool: str | None = None,
          bridge: str | None = None, prio: str = "user", ttl: float = C.QUOTE_TTL_S, to_address: str | None = None,
          timeout: float | None = None, slippage: float | None = None):
    p = {"fromChain": from_chain, "toChain": to_chain, "fromToken": from_token, "toToken": to_token, "fromAmount": str(amount),
         "fromAddress": from_address, "slippage": (f"{slippage:.6f}".rstrip("0").rstrip(".") if slippage is not None else str(C.SLIPPAGE))}
    if to_address:
        p["toAddress"] = to_address
    if tool:
        p["allowExchanges"] = tool
    if bridge:
        p["allowBridges"] = bridge
    return call("GET", "quote", params=p, prio=prio, ttl=ttl, timeout=_t(timeout, 40))


def status(tx_hash: str, from_chain: int | None, to_chain: int | None, bridge: str | None):
    p = {"txHash": tx_hash}
    if from_chain:
        p["fromChain"] = from_chain
    if to_chain:
        p["toChain"] = to_chain
    if bridge:
        p["bridge"] = bridge
    return call("GET", "status", params=p, ttl=8, timeout=20)


def tokens(chains: list[int], prio: str = "user", ttl: float = 300):
    return call("GET", "tokens", params={"chains": ",".join(str(c) for c in chains)}, prio=prio, ttl=ttl, timeout=40)


# ------------------------------------------------------------------ catalog (reach.py): chains, tools, connections — the "other" bucket
def chains(prio: str = "reach"):
    return call("GET", "chains", params={"chainTypes": "EVM"}, prio=prio, ttl=0, timeout=40)


def tools(chain: int, prio: str = "reach"):
    return call("GET", "tools", params={"chains": str(chain)}, prio=prio, ttl=0, timeout=40)


def connections(from_chain: int, to_chain: int, prio: str = "reach"):
    return call("GET", "connections", params={"fromChain": from_chain, "toChain": to_chain}, prio=prio, ttl=0, timeout=40)
