"""Big-trades feed: Swap logs of every stock pool with >= $20k liquidity (market index), official RPC eth_getLogs (research §6).

  V3-style pools (Uniswap V3, Aero, Ramses, Up, Alandale, Giga, …): Swap topic 0xc42079f9 by address list; amounts are the pool's view
    (> 0 = into the pool). V4: PoolManager Swap 0x40e9cecb with topics[1] = pool ids; amounts are the swapper's view (flipped here).
  The stock leg sets size and side: stock out of the pool = buy. USD = |stock amount| x current USD price (router token list / DexScreener).
  Legs of one transaction for the same stock and side are summed (aggregators split one order over pools); >= TRADE_MIN_USD is kept.
  Per-minute volume by dex and stock is kept for /api/stats. Timestamps are interpolated between the chunk's first and last block (~0.1 s
  blocks), so they are approximate to a few seconds. Metric (prop-AMM) and RFQ makers emit other events and are not in this feed.
Backfill 24 h on first start (~1 min), then a poll every TRADE_POLL_S. Node errors back off exponentially; data is marked stale."""
from __future__ import annotations

import json
import logging
import threading
import time

import config as C
import market as M
import store as S
import venues as V
from chain import RpcError, rpc, s256, u, words

log = logging.getLogger("rhdex.trades")
STATE = {"head": 0, "scanned_to": 0, "last_ok": 0.0, "err": "", "backfilled": False, "logs": 0, "backoff": 0}
STEP = 29_000
BLOCKS_24H = 793_000
listeners: list = []          # fn(trade_dict) — alerts


def _block_ts(n: int) -> int:
    b = rpc("eth_getBlockByNumber", [hex(n), False])
    return u(b["timestamp"]) if b else int(time.time())


def _logs(a: int, b: int, flt: dict) -> list:
    try:
        return rpc("eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), **flt}], retries=5, timeout=40) or []
    except RpcError as e:
        if ("exceed" in str(e).lower() or "limit" in str(e).lower() or "too many" in str(e).lower()) and b > a:
            m = (a + b) // 2
            return _logs(a, m, flt) + _logs(m + 1, b, flt)
        raise


def _scan(a: int, b: int, v3: dict, v4: dict) -> int:
    if a > b:
        return 0
    got = []
    if v3:
        addrs = list(v3)
        for i in range(0, len(addrs), 200):
            got += _logs(a, b, {"address": addrs[i:i + 200], "topics": [C.TOPIC_V3_SWAP]})
    if v4:
        got += _logs(a, b, {"address": C.POOL_MANAGER, "topics": [C.TOPIC_V4_SWAP, list(v4)]})
    STATE["logs"] += len(got)
    if not got:
        return 0
    ta, tb = _block_ts(a), _block_ts(b)
    def ts_of(n):
        return int(ta + (tb - ta) * (n - a) / max(1, b - a))
    agg: dict[tuple, dict] = {}
    vol: dict[tuple, list] = {}
    import pricehist as PH
    ph_rows: list = []
    for l in got:
        tp = l["topics"]
        if tp[0] == C.TOPIC_V3_SWAP:
            p = v3.get(l["address"].lower())
            if not p:
                continue
            w = words(l["data"])
            a0, a1 = s256(w[0]), s256(w[1])
            pid = l["address"].lower()
        else:
            pid = tp[1].lower()
            p = v4.get(pid)
            if not p:
                continue
            w = words(l["data"])
            a0, a1 = -s256(w[0]), -s256(w[1])
        try:
            PH.note(l, pid, p, ts_of(u(l["blockNumber"])), ph_rows)      # the stock's price pool -> /api/price-history point
        except Exception as ex:
            log.debug("price note: %s", ex)
        stock = None
        for t, amt in ((p["token0"], a0), (p["token1"], a1)):
            s = M.STOCK_BY_ADDR.get(t)
            px = M.price(t) if s else None
            if s and px:
                usd = abs(amt) * px / 1e18
                if stock is None or usd > stock[2]:
                    stock = (s["symbol"], amt, usd)
        if not stock:
            continue
        sym, amt, usd = stock
        side = "buy" if amt < 0 else "sell"
        n = u(l["blockNumber"])
        k = (l["transactionHash"], sym, side)
        e = agg.setdefault(k, {"tx": l["transactionHash"], "stock": sym, "side": side, "block": n, "ts": ts_of(n), "usd": 0.0, "amount": 0,
                               "legs": 0, "venues": set(), "pools": []})
        e["usd"] += usd
        e["amount"] += abs(amt)
        e["legs"] += 1
        e["venues"].add(p["dex"])
        e["pools"].append({"pool": pid, "dex": p["dex"], "pair": p.get("symbol"), "usd": round(usd, 2)})
        mk = (ts_of(n) // 60 * 60, p["dex"], sym)
        vv = vol.setdefault(mk, [0.0, 0])
        vv[0] += usd
        vv[1] += 1
    try:
        PH._write(ph_rows)
    except Exception as ex:
        log.warning("price history write: %s", ex)
    con = S.db()
    con.execute("BEGIN")
    try:
        for (m, dex, sym), (usd, nsw) in vol.items():
            con.execute("INSERT INTO volume (minute, dex, stock, usd, swaps) VALUES (?,?,?,?,?) ON CONFLICT(minute, dex, stock) DO UPDATE SET "
                        "usd = usd + excluded.usd, swaps = swaps + excluded.swaps", (m, dex, sym, usd, nsw))
        new = []
        for e in agg.values():
            if e["usd"] < C.TRADE_MIN_USD:
                continue
            cur = con.execute("INSERT OR IGNORE INTO trades (tx, stock, side, ts, block, usd, amount, legs, venues, pools) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (e["tx"], e["stock"], e["side"], e["ts"], e["block"], round(e["usd"], 2), str(e["amount"]), e["legs"],
                               json.dumps(sorted(e["venues"])), json.dumps(e["pools"][:30])))
            if cur.rowcount:
                new.append(e)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if STATE["backfilled"]:
        for e in new:
            for fn in listeners:
                try:
                    fn(public(e))
                except Exception as ex:
                    log.warning("trade listener: %s", ex)
    return len(got)


def public(e: dict) -> dict:
    venues = e["venues"] if isinstance(e["venues"], (list, set)) else json.loads(e["venues"])
    pools = e["pools"] if isinstance(e["pools"], list) else json.loads(e["pools"])
    s = M.STOCK_BY_SYM.get(e["stock"]) or {}
    pools = pool_shares(pools)
    return {"tx": e["tx"], "stock": e["stock"], "token": s.get("address"), "logo": f"/assets/logos/stocks/{e['stock']}.png"
            if e["stock"] not in ("SPCX", "SKHY") else None, "side": e["side"], "usd": round(e["usd"], 2),
            "amount": {"raw": str(e["amount"]), "decimals": 18}, "ts": e["ts"], "block": e["block"], "legs": e["legs"],
            "venues": [V.dex(v) for v in sorted(venues)], "pools": pools,
            "explorer": f"https://robinhoodchain.blockscout.com/tx/{e['tx']}"}


def pool_shares(pools: list[dict]) -> list[dict]:
    """sharePct / shareBps of each leg in the trade (by its USD value; legs of one stock and side, display only). Bps are integer and sum to
    10,000 (the rounding remainder goes to the largest leg)."""
    tot = sum(float(p.get("usd") or 0) for p in pools)
    if tot <= 0:
        return [{**p, "shareBps": None, "sharePct": None} for p in pools]
    bps = [int(float(p.get("usd") or 0) * 10_000 // tot) for p in pools]
    if bps:
        bps[max(range(len(bps)), key=lambda i: float(pools[i].get("usd") or 0))] += 10_000 - sum(bps)
    return [{**p, "shareBps": b, "sharePct": b / 100} for p, b in zip(pools, bps)]


def _pool_sets():
    v3, v4 = {}, {}
    for p in M.stock_pools():
        if p["kind"] == "v4":
            if p["dex"] == "uniswap-v4":          # other V4-style dexes live on other PoolManagers
                v4[p["id"]] = p
        elif p["kind"] == "v3":
            v3[p["id"]] = p
    return v3, v4


def loop(stop: threading.Event):
    while not stop.is_set() and not M.POOLS:
        stop.wait(3)
    last_prune = 0.0
    while not stop.is_set():
        try:
            head = u(rpc("eth_blockNumber", []))
            STATE["head"] = head
            v3, v4 = _pool_sets()
            done = int(S.meta_get("trades_scanned_to", "0") or 0)
            if done < head - BLOCKS_24H:
                done = head - BLOCKS_24H
            if not STATE["backfilled"] and head - done > STEP:
                t0 = time.time()
                while done < head and not stop.is_set():
                    b = min(done + STEP, head)
                    _scan(done + 1, b, v3, v4)
                    done = b
                    S.meta_set("trades_scanned_to", str(done))
                    STATE["scanned_to"] = done
                log.info("trades backfill to %d in %.1fs", done, time.time() - t0)
            STATE["backfilled"] = True
            b = min(done + STEP, head)
            _scan(done + 1, b, v3, v4)
            S.meta_set("trades_scanned_to", str(b))
            STATE.update(scanned_to=b, last_ok=time.time(), err="", backoff=0)
            if time.time() - last_prune > 300:
                cut = int(time.time()) - C.TRADE_KEEP_S
                S.x("DELETE FROM trades WHERE ts < ?", (cut,))
                S.x("DELETE FROM volume WHERE minute < ?", (cut - 60,))
                import pricehist as PH
                PH.prune()
                S.x("DELETE FROM tg_outbox WHERE ts < ?", (int(time.time()) - 7 * 86400,))        # audit v1 P2: TTLs
                S.x("DELETE FROM alert_hits WHERE ts < ?", (int(time.time()) - 30 * 86400,))
                last_prune = time.time()
            stop.wait(C.TRADE_POLL_S)
        except Exception as e:
            STATE["err"] = str(e)[:160]
            STATE["backoff"] = min(300, max(5, STATE["backoff"] * 2))
            log.warning("trades loop: %s (backoff %ss)", e, STATE["backoff"])
            stop.wait(STATE["backoff"])


def stale() -> bool:
    return time.time() - STATE["last_ok"] > max(60, C.TRADE_POLL_S * 6)


def feed(limit: int = 50, before: int | None = None, stock: str | None = None, min_usd: float | None = None) -> list[dict]:
    sql, args = "SELECT * FROM trades WHERE ts >= ?", [int(time.time()) - C.TRADE_KEEP_S]
    if before:
        sql += " AND ts < ?"
        args.append(before)
    if stock:
        sql += " AND stock = ?"
        args.append(stock)
    if min_usd:
        sql += " AND usd >= ?"
        args.append(min_usd)
    sql += " ORDER BY ts DESC, usd DESC LIMIT ?"
    args.append(limit)
    return [public(r) for r in S.q(sql, args)]


def stats() -> dict:
    cut = int(time.time()) - C.TRADE_KEEP_S
    tot = S.q("SELECT COALESCE(SUM(usd),0) usd, COALESCE(SUM(swaps),0) n FROM volume WHERE minute >= ?", (cut,))[0]
    by_dex = S.q("SELECT dex, SUM(usd) usd FROM volume WHERE minute >= ? GROUP BY dex ORDER BY usd DESC", (cut,))
    by_stock = S.q("SELECT stock, SUM(usd) usd FROM volume WHERE minute >= ? GROUP BY stock ORDER BY usd DESC LIMIT 100", (cut,))   # every stock (premium-2: /stocks showed 54 of 64 «—» with the top 10 only)
    big = S.q("SELECT * FROM trades WHERE ts >= ? ORDER BY usd DESC LIMIT 1", (cut,))
    nbig = S.q("SELECT COUNT(*) n FROM trades WHERE ts >= ?", (cut,))[0]["n"]
    # premium-4: the landing counter «big buys of $5k+» counts buys of >= BIG_BUY_USD only (bigTrades24h = every kept trade, >= $1k, both sides)
    bb = S.q("SELECT COUNT(*) n FROM trades WHERE ts >= ? AND side = 'buy' AND usd >= ?", (cut, C.BIG_BUY_USD))[0]["n"]
    bbs = S.q("SELECT stock, COUNT(*) n FROM trades WHERE ts >= ? AND side = 'buy' AND usd >= ? GROUP BY stock ORDER BY n DESC LIMIT 8",
              (cut, C.BIG_BUY_USD))
    return {"volume24hUsd": round(tot["usd"], 2), "swaps24h": tot["n"], "bigTrades24h": nbig, "bigBuyMinUsd": C.BIG_BUY_USD,
            "bigBuys24h": bb, "bigBuysByStock": [{"stock": r["stock"], "n": r["n"]} for r in bbs],
            "byDex": [{**V.dex(r["dex"]), "usd": round(r["usd"], 2)} for r in by_dex],
            "byStock": [{"stock": r["stock"], "usd": round(r["usd"], 2)} for r in by_stock],
            "largestTrade24h": public(big[0]) if big else None}
