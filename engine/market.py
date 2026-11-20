"""Market index: every pool of every stock token on 4663 (DexScreener token-pairs, refreshed every 30 min, cached in data/pools.json) and USD
prices (router token list for 4663 every 5 min; DexScreener priceUsd as fallback). Read-only, no keys.

Pool record: {id (pool address or V4 pool id, lowercase), dex (venues.DEXES key), kind v3|v4|v2|other, token0, token1 (sorted, lowercase),
base, quote, symbol "NVDA/USDG", liqUsd}. token0 < token1 is the V3/V4 invariant, so no chain read is needed for the ordering."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import config as C
import venues as V
from chain import http_json

log = logging.getLogger("rhdex.market")
POOLS_FILE = os.path.join(C.DATA, "pools.json")
STOCKS = json.load(open(os.path.join(C.HERE, "stocks.json")))
try:                                     # stocks listed by stockscan after this build (data volume), behind the shipped list
    import stockscan as _SSC
    _have = {s["address"].lower() for s in STOCKS} | {s["symbol"] for s in STOCKS}
    STOCKS += [r for r in _SSC.listed_extra() if r["address"].lower() not in _have and r["symbol"] not in _have]
except Exception:                        # never block the engine on the extra list
    pass
STOCK_BY_ADDR = {s["address"].lower(): s for s in STOCKS}
STOCK_BY_SYM = {s["symbol"]: s for s in STOCKS}

_lock = threading.Lock()
POOLS: dict[str, dict] = {}
PRICES: dict[str, float] = {}           # lowercase address on 4663 -> USD
STATE = {"pools_at": 0.0, "prices_at": 0.0, "pools_err": "", "prices_err": "", "token_meta": {}}


def _load_cached():
    try:
        d = json.load(open(POOLS_FILE))
        with _lock:
            POOLS.update(d.get("pools") or {})
            PRICES.update({k: float(v) for k, v in (d.get("prices") or {}).items()})
            STATE["pools_at"] = d.get("at", 0)
            STATE["read_stocks"] = set(d.get("readStocks") or [])
    except Exception:
        pass


def _save():
    try:
        tmp = POOLS_FILE + ".tmp"
        with _lock:
            read = sorted(set(STATE.get("read_stocks") or ()) | set(STOCK_AT))
            json.dump({"at": STATE["pools_at"], "pools": POOLS, "prices": PRICES, "readStocks": read}, open(tmp, "w"))
        os.replace(tmp, POOLS_FILE)
    except Exception:
        pass


def _kind(dex: str, pid: str, labels) -> str:
    if len(pid) == 66:
        return "v4"
    ls = [str(x).lower() for x in labels or []]
    if "v2" in ls or dex == "uniswap-v2":
        return "v2"
    if dex in ("metric",):
        return "other"
    return "v3"


STOCK_AT: dict[str, float] = {}        # stock address -> last successful pool read (audit v2 S4: per-stock freshness)
FAILED: set[str] = set()


def _read_stock(s: dict):
    """(pools dict, ds price) of one stock, or None when DexScreener did not answer properly."""
    try:
        code, j, _ = http_json(f"https://api.dexscreener.com/token-pairs/v1/robinhood/{s['address']}", timeout=15)
    except Exception as e:
        STATE["pools_err"] = type(e).__name__
        return None
    if code != 200 or not isinstance(j, list):
        STATE["pools_err"] = f"dexscreener {code}"
        return None
    got, px = {}, None
    for p in j:
        pid = (p.get("pairAddress") or "").lower()
        if not pid:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        b, q = p["baseToken"]["address"].lower(), p["quoteToken"]["address"].lower()
        dex = V.dex_key(p.get("dexId"), p.get("labels"))
        if dex.startswith("0x"):
            dex = "other"
        t0, t1 = sorted([b, q])
        got[pid] = {"id": pid, "dex": dex, "kind": _kind(dex, pid, p.get("labels")), "token0": t0, "token1": t1, "base": b, "quote": q,
                    "symbol": f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}", "liqUsd": round(liq)}
        if b == s["address"].lower() and p.get("priceUsd") and liq >= C.POOL_MIN_LIQ_USD and px is None:
            px = float(p["priceUsd"])
    return got, px


def _merge(stock: str, got: dict):
    """Replace only this stock's pools; pools of other stocks (and of stocks whose read failed) stay as they were."""
    with _lock:
        for pid in [k for k, p in POOLS.items() if (p["base"] == stock or p["quote"] == stock) and k not in got]:
            # DexScreener dropped this pool from the stock's list: remove it, unless the other side is a stock whose read is failing
            other = (POOLS[pid]["quote"] if POOLS[pid]["base"] == stock else POOLS[pid]["base"])
            if not (other in STOCK_BY_ADDR and other in FAILED):    # keep it only while the other stock's read is failing
                POOLS.pop(pid, None)
        POOLS.update(got)
        STOCK_AT[stock] = time.time()


def refresh_pools(only: list[dict] | None = None) -> int:
    ok = 0
    for s in only or STOCKS:
        a = s["address"].lower()
        r = _read_stock(s)
        if r is None:
            FAILED.add(a)
            continue
        got, px = r
        FAILED.discard(a)
        _merge(a, got)
        if px:
            with _lock:
                PRICES.setdefault(a, px)
        ok += 1
        time.sleep(0.25)                       # 64 requests, well under DexScreener's 300/min
    if ok:
        with _lock:
            STATE["pools_at"] = time.time()
        _save()
    return ok


def add_stock(row: dict) -> None:
    """a stock listed while the engine runs (stockscan): into the list and both maps at once, then its pools"""
    a = row["address"].lower()
    with _lock:
        if a in STOCK_BY_ADDR or row["symbol"] in STOCK_BY_SYM:
            return
        STOCKS.append(row)
        STOCK_BY_ADDR[a] = row
        STOCK_BY_SYM[row["symbol"]] = row
    refresh_pools([row])


def stock_stale(addr: str) -> bool:
    return addr.lower() in FAILED or time.time() - STOCK_AT.get(addr.lower(), STATE["pools_at"]) > 3600


def refresh_prices() -> int:
    import router
    try:
        j = router.tokens([C.CHAIN])
    except Exception as e:
        STATE["prices_err"] = str(e)[:120]
        return 0
    n = 0
    meta = {}
    for t in (j.get("tokens") or {}).get(str(C.CHAIN), []):
        a = t["address"].lower()
        try:
            px = float(t.get("priceUSD") or 0)
        except ValueError:
            px = 0
        meta[a] = {"symbol": t.get("symbol"), "decimals": t.get("decimals"), "name": t.get("name")}
        if px > 0:
            with _lock:
                PRICES[a] = px
            n += 1
    with _lock:
        if C.NATIVE in PRICES:
            PRICES.setdefault(C.WETH, PRICES[C.NATIVE])
        STATE["prices_at"] = time.time()
        STATE["token_meta"] = meta
    return n


def price(addr: str) -> float | None:
    a = (addr or "").lower()
    if a == C.WETH and a not in PRICES:
        a = C.NATIVE
    if a in STOCK_BY_ADDR:
        # premium-4: one price per stock on the whole site = its deepest USDG pool's on-chain price (the /stock chart's last point);
        # the router's index price only when that pool has no swap in the last 26 h. A pool price only moves on a swap, so the last swap's
        # price is the pool's price now. (DJT 17.11: index $4.53 vs pool $4.11.)
        try:
            import pricehist
            pp = pricehist.recent_price(STOCK_BY_ADDR[a]["symbol"])
        except Exception:
            pp = None
        if pp:
            usdg = PRICES.get(C.USDG) or 1.0
            return pp * usdg
    return PRICES.get(a)


def usd(amount_raw: int, decimals: int, addr: str) -> float | None:
    """USD value of an integer token amount. Float only for the USD figure (display), never for the token amount itself."""
    px = price(addr)
    if px is None:
        return None
    return round(amount_raw * px / 10 ** decimals, 2)


def pool(pid: str) -> dict | None:
    return POOLS.get((pid or "").lower())


def pools_for_pair(a: str, b: str) -> list[dict]:
    x, y = sorted([a.lower(), b.lower()])
    with _lock:
        return [p for p in POOLS.values() if p["token0"] == x and p["token1"] == y]


def stock_pools(min_liq: float = C.POOL_MIN_LIQ_USD) -> list[dict]:
    with _lock:
        return [p for p in POOLS.values() if p["liqUsd"] >= min_liq and (p["base"] in STOCK_BY_ADDR or p["quote"] in STOCK_BY_ADDR)]


def stale() -> dict:
    now = time.time()
    return {"pools": now - STATE["pools_at"] > 3600 or bool(FAILED), "prices": now - STATE["prices_at"] > 900,
            "stocksFailed": sorted(STOCK_BY_ADDR[a]["symbol"] for a in FAILED if a in STOCK_BY_ADDR)}


def loop(stop: threading.Event):
    _load_cached()
    last_pools = STATE["pools_at"]
    last_px = 0.0
    last_retry = 0.0
    seen = STATE.get("read_stocks") or set()                 # stocks whose own pool list the cached index never read (a grown list): read now
    fresh = [s for s in STOCKS if s["address"].lower() not in seen]
    if fresh:
        try:
            refresh_pools(fresh)
        except Exception as e:
            log.warning("market: new stocks' pools: %s", e)
    STATE["fresh_done"] = True                           # pricehist's first backfill waits for this (new stocks' price pools)
    while not stop.is_set():
        now = time.time()
        try:
            if now - last_px > 300:
                refresh_prices()
                last_px = now
            if now - last_pools > 1800:
                n = refresh_pools()
                log.info("pools refreshed: %d of %d stocks", n, len(STOCKS))
                last_pools = now if n else now - 1500    # retry in 5 min on a full failure
                last_retry = now
            elif FAILED and now - last_retry > 120:      # stocks whose read failed: again every 2 min
                last_retry = now
                refresh_pools([x for x in STOCKS if x["address"].lower() in FAILED])
        except Exception as e:                           # never let the loop die
            log.warning("market loop: %s", e)
        stop.wait(15)


_load_cached()
