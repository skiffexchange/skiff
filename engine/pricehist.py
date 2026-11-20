"""Price history of every stock token for the /stock/<ticker> chart (rule: never an empty or fake chart).

Source: the Swap logs of ONE pool per stock — its deepest pool against USDG (V3-style or Uniswap V4) from the market index. Every Swap log
carries the pool's sqrtPriceX96 after the swap, i.e. the real on-chain price at that block. Points are 5-minute buckets (the last swap of the
bucket wins). Nothing is interpolated: a bucket without a swap has no point.
  * forward: trades._scan hands every decoded Swap log to note, so the history grows with the big-trades poll (every few seconds);
  * backfill: on start, when the history does not reach back ~25 h, the last ~25 h of these pools' Swap logs are read from the official RPC
    in chunks (the same eth_getLogs path as trades.py) — so the chart is full on launch day.
Prices are USDG per stock token (USDG ~ $1; the API multiplies by the USDG price). change24hPct only when a real point ~24 h old exists."""
from __future__ import annotations

import logging
import threading
import time

import config as C
import market as M
import store as S
from chain import u, words

log = logging.getLogger("rhdex.pricehist")
BUCKET = 300
KEEP_S = 26 * 3600
BACKFILL_S = 25 * 3600
MIN_POINTS = 12                 # an honest line: at least 12 five-minute points ...
MIN_SPAN_S = 6 * 3600           # ... spanning at least 6 h
STATE = {"backfilled": False, "points": 0, "err": "", "tookS": None}
_pp = {"at": 0.0, "by_pool": {}, "by_stock": {}}
_lock = threading.Lock()
SCHEMA = ("CREATE TABLE IF NOT EXISTS price_hist (stock TEXT NOT NULL, ts INTEGER NOT NULL, block INTEGER NOT NULL, price REAL NOT NULL, "
          "PRIMARY KEY (stock, ts))")


def _ensure():
    S.x(SCHEMA)


def price_pools() -> tuple[dict, dict]:
    """(pool id -> stock symbol, stock symbol -> pool) for the deepest USDG pool of every stock (V3-style or Uniswap V4). Cached 10 min."""
    now = time.time()
    with _lock:
        if _pp["by_pool"] and now - _pp["at"] < 600:
            return _pp["by_pool"], _pp["by_stock"]
    best: dict[str, dict] = {}
    for p in M.stock_pools(0):
        if not ((p["kind"] == "v3") or (p["kind"] == "v4" and p["dex"] == "uniswap-v4")):
            continue
        t0, t1 = p["token0"], p["token1"]
        if C.USDG not in (t0, t1):
            continue
        st = M.STOCK_BY_ADDR.get(t1 if t0 == C.USDG else t0)
        if not st:
            continue
        cur = best.get(st["symbol"])
        if cur is None or (p.get("liqUsd") or 0) > (cur.get("liqUsd") or 0):
            best[st["symbol"]] = p
    by_pool = {p["id"]: s for s, p in best.items()}
    with _lock:
        # a fresh start: the market index is still loading (pools_at 0) -> keep this partial map for 30 s only, not 10 min
        _pp.update(at=now if M.STATE.get("pools_at") else now - 570, by_pool=by_pool, by_stock=best)
    return by_pool, best


def _price(p: dict, sqrt_p: int) -> float | None:
    """USDG per stock token from sqrtPriceX96 (token1 per token0 = (sqrtP / 2^96)^2 x 10^(dec0 - dec1)); stock 18 decimals, USDG 6"""
    if sqrt_p <= 0:
        return None
    r = (sqrt_p / 2 ** 96) ** 2           # raw token1 per raw token0
    if p["token0"] == C.USDG:              # token0 = USDG (6), token1 = stock (18): r = stock_raw per usdg_raw
        stock_per_usdg = r * 10 ** (6 - 18)
        return 1 / stock_per_usdg if stock_per_usdg > 0 else None
    return r * 10 ** (18 - 6)              # token0 = stock (18), token1 = USDG (6): USDG per stock


def note(log_: dict, pid: str, p: dict, ts: int, rows: list | None = None) -> None:
    """one decoded Swap log of a stock pool (from trades._scan or the backfill); keeps it if the pool is the stock's price pool"""
    by_pool, _ = price_pools()
    sym = by_pool.get(pid)
    if not sym:
        return
    w = words(log_["data"])
    if len(w) < 3:
        return
    px = _price(p, w[2] & ((1 << 160) - 1))
    if not px or px <= 0 or px > 1e9:
        return
    row = (sym, ts // BUCKET * BUCKET, u(log_["blockNumber"]), px)
    if rows is not None:
        rows.append(row)
    else:
        _write([row])


def _write(rows: list) -> None:
    if not rows:
        return
    _ensure()
    con = S.db()
    con.execute("BEGIN")
    try:
        for sym, ts, blk, px in rows:
            con.execute("INSERT INTO price_hist (stock, ts, block, price) VALUES (?,?,?,?) ON CONFLICT(stock, ts) DO UPDATE SET "
                        "block = excluded.block, price = excluded.price WHERE excluded.block >= price_hist.block", (sym, ts, blk, px))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


_DONE: set[str] = set()          # pool ids backfilled (or found covered) by this process


def _covered_stocks() -> set[str]:
    """stocks whose stored history already reaches back 24 h"""
    _ensure()
    cut = int(time.time()) - 24 * 3600 + 3600
    return {r["stock"] for r in S.q("SELECT stock, MIN(ts) a FROM price_hist GROUP BY stock") if r["a"] is not None and r["a"] <= cut}


def loop(stop: threading.Event) -> None:
    """the backfill once the market index is complete, then hourly: only price pools not filled yet (a new deepest pool, a stock added).
    The thread stays alive: /healthz counts every engine thread, so the backfill never makes the health check fail."""
    t0 = time.time()
    while not stop.is_set() and not (M.STATE.get("pools_at") and M.STATE.get("fresh_done")) and time.time() - t0 < 600:   # wait for the full
        # pool index (a fresh volume) and for the pools of stocks the cached index never read (stocks added since)
        stop.wait(3)
    while not stop.is_set():
        backfill(stop)
        stop.wait(3600)


def backfill(stop: threading.Event) -> None:
    """~25 h of the price pools' Swap logs, chunked like trades.py, for every price pool whose stock is not covered 24 h back yet"""
    import trades as TR
    from chain import rpc
    while not stop.is_set() and not M.POOLS:
        stop.wait(3)
    try:
        with _lock:
            _pp["at"] = 0.0                       # rebuild the price-pool map from the current index
        by_pool, best = price_pools()
        cov = _covered_stocks()
        todo = {s: p for s, p in best.items() if p["id"] not in _DONE and s not in cov}
        _DONE.update(p["id"] for s, p in best.items() if s in cov)
        if not todo:
            STATE["backfilled"] = True
            return
        t0 = time.time()
        v3 = {p["id"]: p for p in todo.values() if p["kind"] == "v3"}
        v4 = {p["id"]: p for p in todo.values() if p["kind"] == "v4"}
        head = u(rpc("eth_blockNumber", []))
        span = int(TR.BLOCKS_24H * BACKFILL_S / 86400)
        a0 = head - span
        n = 0
        for a in range(a0, head + 1, TR.STEP):
            if stop.is_set():
                return
            b = min(a + TR.STEP - 1, head)
            got = []
            if v3:
                got += TR._logs(a, b, {"address": list(v3), "topics": [C.TOPIC_V3_SWAP]})
            if v4:
                got += TR._logs(a, b, {"address": C.POOL_MANAGER, "topics": [C.TOPIC_V4_SWAP, list(v4)]})
            if not got:
                continue
            ta, tb = TR._block_ts(a), TR._block_ts(b)
            rows: list = []
            for l in got:
                pid = (l["address"].lower() if l["topics"][0] == C.TOPIC_V3_SWAP else l["topics"][1].lower())
                p = v3.get(pid) or v4.get(pid)
                if not p:
                    continue
                k = u(l["blockNumber"])
                note(l, pid, p, int(ta + (tb - ta) * (k - a) / max(1, b - a)), rows)
            _write(rows)
            n += len(rows)
        _DONE.update(p["id"] for p in todo.values())
        STATE.update(backfilled=True, points=STATE.get("points", 0) + n, tookS=round(time.time() - t0, 1), err="", pools=len(_DONE))
        log.info("price history backfill: %d swaps over %d pools (%d stocks already covered) in %.1fs", n, len(todo), len(cov), time.time() - t0)
    except Exception as e:
        STATE["err"] = str(e)[:160]
        log.warning("price history backfill: %s", e)


def prune() -> None:
    _ensure()
    S.x("DELETE FROM price_hist WHERE ts < ?", (int(time.time()) - KEEP_S,))


def _series(pts: list, now: int, bucket: int = 0) -> dict:
    """the 24 h step series of one stock from its stored points [[ts, usd], …] (kept 26 h back).
    premium-3: a pool's price only moves on a swap, so the price between two swaps IS the earlier one. The line starts at now-24 h with the
    pool price of that moment (the last swap before it) and ends at «now» with the current pool price: a step line over a fixed 24 h axis,
    nothing interpolated, nothing invented. M-7: the change only next to a drawn chart;: the price 24 h ago = the last swap before now-24 h.
    bucket > 0 (the /stocks sparkline): only the LAST real swap price of every bucket is kept — fewer points, every one of them real."""
    t0 = now - 24 * 3600
    in24 = [x for x in pts if x[0] >= t0]
    before = [x for x in pts if x[0] < t0]
    span = (in24[-1][0] - in24[0][0]) if len(in24) > 1 else 0
    enough = len(in24) >= MIN_POINTS and span >= MIN_SPAN_S
    last = in24[-1][1] if in24 else (before[-1][1] if before else None)
    line = []
    if enough:
        body = in24
        if bucket:
            keep: dict[int, list] = {}
            for x in in24:
                keep[x[0] // bucket] = x
            body = list(keep.values())
        line = ([[t0, before[-1][1]]] if before else []) + body
        if line[-1][0] < now:
            line.append([now, line[-1][1]])
    chg = round((last / before[-1][1] - 1) * 100, 2) if (enough and before and last) else None
    return {"points": line, "count": len(in24), "enough": enough, "last": last, "lastSwapAt": (in24[-1][0] if in24 else None),
            "change24hPct": chg, "from": t0, "to": now}


def doc(sym: str) -> dict | None:
    """/api/price-history?stock=NVDA"""
    st = M.STOCK_BY_SYM.get(sym)
    if not st:
        return None
    _ensure()
    _, best = price_pools()
    p = best.get(sym)
    now = int(time.time())
    usdg = M.price(C.USDG) or 1.0
    rows = S.q("SELECT ts, price FROM price_hist WHERE stock = ? AND ts >= ? ORDER BY ts", (sym, now - KEEP_S))
    sr = _series([[r["ts"], round(r["price"] * usdg, 6)] for r in rows], now)
    return {"stock": sym, "pool": p and {"id": p["id"], "dex": p["dex"], "pair": p.get("symbol")}, "step": True, **sr,
            "price": M.price(st["address"]), "backfilled": STATE["backfilled"], "at": now}


_ov = {"at": 0.0, "doc": None}


def overview() -> dict:
    """/api/stocks-overview: one row per stock for the /stocks table. Every value from the engine, null where it
    does not exist: price, 24 h change + a 30-min sparkline (real swap prices, see _series), liquidity of its pools, pools / venues count,
    volume 24 h and big buys (buys >= BIG_BUY_USD) 24 h — the same definitions as /api/stats and the landing counters. Cached 60 s."""
    now = time.time()
    if _ov["doc"] and now - _ov["at"] < 60:
        return _ov["doc"]
    import trades as TR
    _ensure()
    n = int(now)
    usdg = M.price(C.USDG) or 1.0
    hist: dict[str, list] = {}
    for r in S.q("SELECT stock, ts, price FROM price_hist WHERE ts >= ? ORDER BY ts", (n - KEEP_S,)):
        hist.setdefault(r["stock"], []).append([r["ts"], round(r["price"] * usdg, 6)])
    cut = n - C.TRADE_KEEP_S
    vol = {r["stock"]: r["usd"] for r in S.q("SELECT stock, SUM(usd) usd FROM volume WHERE minute >= ? GROUP BY stock", (cut,))}
    big = {r["stock"]: r["n"] for r in S.q("SELECT stock, COUNT(*) n FROM trades WHERE ts >= ? AND side = 'buy' AND usd >= ? GROUP BY stock",
                                            (cut, C.BIG_BUY_USD))}
    pools: dict[str, list] = {}
    for p in M.stock_pools():
        a = p["base"] if p["base"] in M.STOCK_BY_ADDR else p["quote"]
        pools.setdefault(M.STOCK_BY_ADDR[a]["symbol"], []).append(p)
    rows = []
    for st in M.STOCKS:
        sym = st["symbol"]
        sr = _series(hist.get(sym, []), n, bucket=1800)
        ps = pools.get(sym, [])
        rows.append({"stock": sym, "price": M.price(st["address"]), "change24hPct": sr["change24hPct"],
                     "spark": sr["points"], "sparkEnough": sr["enough"],
                     "liqUsd": round(sum(p.get("liqUsd") or 0 for p in ps), 2) if ps else None,
                     "deepestLiqUsd": round(max((p.get("liqUsd") or 0) for p in ps), 2) if ps else None,
                     "pools": len(ps), "venues": len({p["dex"] for p in ps}),
                     "volume24hUsd": round(vol[sym], 2) if sym in vol else None, "bigBuys24h": big.get(sym, 0)})
    doc_ = {"stocks": rows, "bigBuyMinUsd": C.BIG_BUY_USD, "sparkBucketS": 1800, "from": n - 24 * 3600, "to": n,
            "backfilled": STATE["backfilled"] and TR.STATE.get("backfilled", True), "at": n}
    _ov.update(at=now, doc=doc_)
    return doc_


_recent = {"at": 0.0, "px": {}}


def recent_price(sym: str) -> float | None:
    """the stock's deepest USDG pool price in USDG: the last swap's price within the kept 26 h (a pool price only moves on a swap)"""
    now = time.time()
    if now - _recent["at"] > 60:
        try:
            _ensure()
            rows = S.q("SELECT stock, price, ts FROM price_hist p WHERE ts = (SELECT MAX(ts) FROM price_hist WHERE stock = p.stock) AND ts >= ?",
                       (int(now) - KEEP_S,))
            _recent.update(at=now, px={r["stock"]: r["price"] for r in rows})
        except Exception:
            _recent["at"] = now
    return _recent["px"].get(sym)
