"""Price history of the network tokens for the /tokens table (24 h change + sparkline like /stocks).

Source, per token: its deepest pool (network_tokens: DexScreener's deepest pair) when that pool is quoted in USDG, ETH or WETH. The price is
read from the pool's own logs — never interpolated, never invented:
  * Uniswap V4 (32-byte pool id): PoolManager Swap -> sqrtPriceX96 after the swap; the pool's currency0/currency1 from its Initialize log
    (looked up once, kept in meta);
  * V3-style pool (address with slot0): Swap -> sqrtPriceX96;
  * V2-style pair (address with getReserves): Sync -> reserves after the swap.
A token quoted in ETH/WETH is turned into USDG with the ETH/USDG Uniswap V4 pool (fee 0.01 %, id 0x24107d15…) at the same moment, so ETH's own
move is in the line too. Rows live in pricehist's price_hist table under the key "t:<address>" (ETH itself under "t:eth"), in USDG; the read
multiplies by the USDG price like the stocks. Backfill ~25 h on start (after the token list exists), forward poll every 60 s, hourly re-check
for new pools. A token whose deepest pool is quoted in anything else has no chart («—»)."""
from __future__ import annotations

import json
import logging
import threading
import time

import config as C
import market as M
import pricehist as PH
import store as S
from chain import rpc, u, word_addr, words

log = logging.getLogger("rhdex.tokhist")
ETH_POOL = "0x24107d152f14a76d292123265ae3f3c71f863fc2f4ef7ba49d64e78d28ea379e"   # V4 {ETH, USDG, fee 100, tickSpacing 1, no hooks}
ETH_KEY = "t:eth"
TOPIC_SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
TOPIC_V4_INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
ETHS = {C.NATIVE, C.WETH}
STATE = {"backfilled": False, "tokens": 0, "charted": 0, "lastBlock": 0, "err": "", "tookS": None}
_done: set[str] = set()


def _call(to: str, data: str) -> str | None:
    try:
        r = rpc("eth_call", [{"to": to, "data": data}, "latest"])
        return r if r and r != "0x" else None
    except Exception:
        return None


def _pool_meta(pool: str) -> dict | None:
    """{kind: v4|v3|v2, t0, t1} of a pool, cached in meta for good (pools don't change tokens)"""
    k = "tokhist:pool:" + pool
    try:
        m = json.loads(S.meta_get(k) or "null")
    except ValueError:
        m = None
    if m is not None:
        return m if m.get("kind") else None
    m = {}
    if len(pool) == 66:
        head = u(rpc("eth_blockNumber", []))
        for a in range(0, head + 1, 10_000_000):
            try:
                lg = rpc("eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(min(a + 9_999_999, head)), "address": C.POOL_MANAGER,
                                          "topics": [TOPIC_V4_INIT, pool]}], timeout=40) or []
            except Exception:
                lg = []
            if lg:
                t = lg[0]["topics"]
                m = {"kind": "v4", "t0": word_addr(t[2]), "t1": word_addr(t[3])}
                break
    else:
        t0, t1 = _call(pool, "0x0dfe1681"), _call(pool, "0xd21220a7")      # token0(), token1()
        if t0 and t1:
            kind = "v3" if _call(pool, "0x3850c7bd") else ("v2" if _call(pool, "0x0902f1ac") else None)   # slot0() / getReserves()
            if kind:
                m = {"kind": kind, "t0": word_addr(t0[-64:]), "t1": word_addr(t1[-64:])}
    S.meta_set(k, m or {"kind": None})
    return m or None


def descriptors() -> dict[str, dict]:
    """key -> {pool, kind, t0, t1, token, dec, qdec, quote} for every charted token + ETH"""
    import network_tokens as NT
    out = {ETH_KEY: {"pool": ETH_POOL, "kind": "v4", "t0": C.NATIVE, "t1": C.USDG, "token": C.NATIVE, "dec": 18, "qdec": 6, "quote": "usdg"}}
    rows = (NT.doc() or {}).get("tokens") or []
    STATE["tokens"] = len(rows)
    for r in rows:
        pool, q = (r.get("pool") or "").lower(), (r.get("poolQuote") or "").lower()
        if not pool or q not in ETHS | {C.USDG}:
            continue
        m = _pool_meta(pool)
        if not m:
            continue
        a = r["address"].lower()
        if a not in (m["t0"], m["t1"]):
            continue
        other = m["t1"] if a == m["t0"] else m["t0"]
        if other not in ETHS | {C.USDG}:
            continue
        out["t:" + a] = {"pool": pool, **m, "token": a, "dec": int(r["decimals"]), "qdec": 6 if other == C.USDG else 18,
                         "quote": "usdg" if other == C.USDG else "eth"}
    return out


def _px_quote(d: dict, lg: dict) -> float | None:
    """the token's price in its quote (USDG or ETH) from one Swap / Sync log of its pool"""
    w = words(lg["data"])
    dec0, dec1 = (d["dec"], d["qdec"]) if d["token"] == d["t0"] else (d["qdec"], d["dec"])
    if d["kind"] == "v2":
        if len(w) < 2 or not w[0] or not w[1]:
            return None
        r = w[1] / w[0]                                    # raw token1 per raw token0
    else:
        if len(w) < 3:
            return None
        sp = w[2] & ((1 << 160) - 1)
        if sp <= 0:
            return None
        r = (sp / 2 ** 96) ** 2
    p10 = r * 10 ** (dec0 - dec1)                          # token1 per token0
    if p10 <= 0:
        return None
    return p10 if d["token"] == d["t0"] else 1 / p10


def _fetch(a: int, b: int, ds: dict[str, dict]) -> list:
    import trades as TR
    v4 = {d["pool"] for d in ds.values() if d["kind"] == "v4"}
    v3 = {d["pool"] for d in ds.values() if d["kind"] == "v3"}
    v2 = {d["pool"] for d in ds.values() if d["kind"] == "v2"}
    got = []
    if v4:
        got += TR._logs(a, b, {"address": C.POOL_MANAGER, "topics": [C.TOPIC_V4_SWAP, sorted(v4)]})
    if v3:
        got += TR._logs(a, b, {"address": sorted(v3), "topics": [C.TOPIC_V3_SWAP]})
    if v2:
        got += TR._logs(a, b, {"address": sorted(v2), "topics": [TOPIC_SYNC]})
    got.sort(key=lambda l: (u(l["blockNumber"]), u(l.get("logIndex") or "0x0")))
    return got


_eth = {"px": None}


def _process(logs: list, ds: dict[str, dict], ts_of) -> list:
    """rows (key, bucket ts, block, USDG price) in block order; ETH-quoted tokens use the ETH/USDG price of that moment"""
    by_pool: dict[str, list] = {}
    for k, d in ds.items():
        by_pool.setdefault(d["pool"], []).append(k)
    rows = []
    for lg in logs:
        pid = lg["topics"][1].lower() if lg["topics"][0] == C.TOPIC_V4_SWAP else lg["address"].lower()
        for k in by_pool.get(pid, []):
            d = ds[k]
            px = _px_quote(d, lg)
            if not px or px > 1e12:
                continue
            if k == ETH_KEY:
                _eth["px"] = px
            elif d["quote"] == "eth":
                if not _eth["px"]:
                    continue
                px = px * _eth["px"]
            blk = u(lg["blockNumber"])
            rows.append((k, ts_of(blk) // PH.BUCKET * PH.BUCKET, blk, px))
    return rows


def _eth_seed(before_block: int):
    """the ETH/USDG price just before a backfill window (the last swap of the pool in the ~2 h before it)"""
    import trades as TR
    lg = TR._logs(max(0, before_block - 72_000), before_block, {"address": C.POOL_MANAGER, "topics": [C.TOPIC_V4_SWAP, ETH_POOL]})
    if lg:
        _eth["px"] = _px_quote({"kind": "v4", "t0": C.NATIVE, "t1": C.USDG, "token": C.NATIVE, "dec": 18, "qdec": 6}, lg[-1])


def backfill(stop: threading.Event, ds: dict[str, dict]) -> None:
    import trades as TR
    cov = PH._covered_stocks()
    todo = {k: d for k, d in ds.items() if k not in _done and k not in cov}
    _done.update(k for k in ds if k in cov)
    if not todo:
        return
    if any(d["quote"] == "eth" for d in todo.values()) and ETH_KEY not in todo:
        todo[ETH_KEY] = ds[ETH_KEY]                      # ETH-quoted tokens need ETH's own line over the same window
    t0 = time.time()
    head = u(rpc("eth_blockNumber", []))
    a0 = head - int(TR.BLOCKS_24H * PH.BACKFILL_S / 86400)
    _eth["px"] = None
    _eth_seed(a0 - 1)
    n = 0
    for a in range(a0, head + 1, TR.STEP):
        if stop.is_set():
            return
        b = min(a + TR.STEP - 1, head)
        got = _fetch(a, b, todo)
        if not got:
            continue
        ta, tb = TR._block_ts(a), TR._block_ts(b)
        rows = _process(got, todo, lambda k: int(ta + (tb - ta) * (k - a) / max(1, b - a)))
        PH._write(rows)
        n += len(rows)
    _done.update(todo)
    STATE.update(lastBlock=max(STATE["lastBlock"], head), tookS=round(time.time() - t0, 1))
    log.info("token price backfill: %d points over %d pools in %.1fs", n, len(todo), time.time() - t0)


def forward(ds: dict[str, dict]) -> None:
    import trades as TR
    head = u(rpc("eth_blockNumber", []))
    a = STATE["lastBlock"] + 1 if STATE["lastBlock"] else head - 600
    if a > head:
        return
    got = _fetch(a, head, ds)
    if got:
        tb = TR._block_ts(head)
        ta = TR._block_ts(a) if head - a > 50 else tb
        PH._write(_process(got, ds, lambda k: int(ta + (tb - ta) * (k - a) / max(1, head - a))))
    STATE["lastBlock"] = head


def loop(stop: threading.Event) -> None:
    """after the market index and the token list: backfill, then a forward poll every 60 s and a check for new pools every 10 min.
    The thread never ends (/healthz counts every engine thread)."""
    t_start = time.time()
    while not stop.is_set() and not M.STATE.get("pools_at") and time.time() - t_start < 600:
        stop.wait(3)
    ds, ds_at = {}, 0.0
    while not stop.is_set():
        try:
            if time.time() - ds_at > 600 or not ds:          # the token list refreshes every 10 min: new pools get their backfill
                ds, ds_at = descriptors(), time.time()
                STATE["charted"] = len(ds) - 1
                backfill(stop, ds)
                STATE["backfilled"] = True
            forward(ds)
            STATE["err"] = ""
        except Exception as e:
            STATE["err"] = str(e)[:160]
            log.warning("token price history: %s", e)
        stop.wait(60)


_ov = {"at": 0.0, "doc": None}


def overview() -> dict:
    """/api/tokens-overview: per network token (and ETH / WETH) its 24 h change and 30-min sparkline from its own pool (see the module doc),
    plus the pools / venues count; null / [] where the engine has no real data. Cached 60 s."""
    now = time.time()
    if _ov["doc"] and now - _ov["at"] < 60:
        return _ov["doc"]
    import network_tokens as NT
    n = int(now)
    usdg = M.price(C.USDG) or 1.0
    PH._ensure()
    hist: dict[str, list] = {}
    for r in S.q("SELECT stock, ts, price FROM price_hist WHERE stock LIKE 't:%' AND ts >= ? ORDER BY ts", (n - PH.KEEP_S,)):
        hist.setdefault(r["stock"], []).append([r["ts"], round(r["price"] * usdg, 9)])
    d = NT.doc() or {}
    rows = []
    for r in (d.get("listed") or []) + (d.get("tokens") or []):
        a = r["address"].lower()
        key = ETH_KEY if a in ETHS else "t:" + a
        sr = PH._series(hist.get(key, []), n, bucket=1800)
        rows.append({"address": a, "symbol": r["symbol"], "price": float(f"{sr['last']:.6g}") if sr["last"] else None,
                     "change24hPct": sr["change24hPct"], "spark": [[t, float(f"{v:.6g}")] for t, v in sr["points"]], "sparkEnough": sr["enough"],
                     "pools": r.get("pools"), "venues": r.get("dexes")})
    doc_ = {"tokens": rows, "sparkBucketS": 1800, "poolsMinLiqUsd": __import__("network_tokens").POOL_COUNT_MIN_USD, "from": n - 24 * 3600,
            "to": n, "backfilled": STATE["backfilled"], "charted": STATE["charted"], "at": n}
    _ov.update(at=now, doc=doc_)
    return doc_
