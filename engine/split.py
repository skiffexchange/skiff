"""Pool split of a simulated route: where the output token actually came from, decoded from the simulation's logs (research 2.4).

  V3-style Swap (Uniswap V3, Aero, Ramses, Up, Alandale, Giga… same event): pool address -> market index (or token0/token1/factory read
    once through Multicall3 for a pool the index does not know). amount < 0 = paid out of the pool.
  V4 Swap at the PoolManager: topics[1] = pool id (= DexScreener pairAddress). Amounts are the swapper's deltas: > 0 = paid to the swapper.
  Transfer of the output token FROM a known non-V3/V4 pool (Metric prop-AMM): counted for that pool's dex.
  Output that none of those explain (RFQ makers, prop-AMMs without a known pool) = "other".
Shares are integer basis points of the output amount; the token amounts stay integers."""
from __future__ import annotations

import threading

import config as C
import market as M
import venues as V
from chain import multicall, s256, u, word_addr, words

_extra: dict[str, dict | None] = {}
_xlock = threading.Lock()


def _resolve(addrs: list[str]):
    need = [a for a in addrs if a not in _extra]
    if not need:
        return
    calls = []
    for a in need:
        calls += [(a, "0x0dfe1681"), (a, "0xd21220a7"), (a, "0xc45a0155")]
    try:
        res = multicall(calls)
    except Exception:
        return
    with _xlock:
        for i, a in enumerate(need):
            t0, t1, f = res[3 * i:3 * i + 3]
            if t0[0] and t1[0] and len(t0[1]) >= 66 and len(t1[1]) >= 66:
                fac = word_addr(f[1][:66]) if f[0] and len(f[1]) >= 66 else ""
                _extra[a] = {"id": a, "dex": V.FACTORIES.get(fac, "other"), "kind": "v3", "token0": word_addr(t0[1][:66]),
                             "token1": word_addr(t1[1][:66]), "symbol": "", "liqUsd": None}
            else:
                _extra[a] = None


def _pool(a: str) -> dict | None:
    return M.pool(a) or _extra.get(a)


def decode(logs: list[dict], to_token: str, received: int) -> dict:
    """{"split": [{dex,name,logo,pool,pair,amount,shareBps,sharePct}], "via": [{dex,pool,pair}], "attributedBps"}"""
    T = to_token.lower()
    if T == C.NATIVE:
        T = C.WETH
    unknown = sorted({l["address"].lower() for l in logs if l.get("topics") and l["topics"][0] == C.TOPIC_V3_SWAP
                      and not M.pool(l["address"])})
    if unknown:
        _resolve(unknown[:40])
    legs: dict[tuple[str, str], int] = {}
    via: dict[str, dict] = {}
    first: dict[str, int] = {}          # pool -> position of its first Swap/Transfer log in the simulation = execution order (audit v2 M11)
    for n, l in enumerate(logs):
        tp = l.get("topics") or []
        if not tp:
            continue
        a = l["address"].lower()
        if tp[0] == C.TOPIC_V3_SWAP:
            p = _pool(a)
            if not p:
                continue
            w = words(l["data"])
            if len(w) < 2:
                continue
            a0, a1 = s256(w[0]), s256(w[1])
            out = (-a0 if p["token0"] == T and a0 < 0 else 0) + (-a1 if p["token1"] == T and a1 < 0 else 0)
            first.setdefault(a, n)
            if out > 0:
                legs[(p["dex"], a)] = legs.get((p["dex"], a), 0) + out
            else:
                via[a] = p
        elif tp[0] == C.TOPIC_V4_SWAP and a == C.POOL_MANAGER and len(tp) > 1:
            pid = tp[1].lower()
            p = M.pool(pid) or {"id": pid, "dex": "uniswap-v4", "token0": "", "token1": "", "symbol": ""}
            w = words(l["data"])
            if len(w) < 2:
                continue
            a0, a1 = s256(w[0]), s256(w[1])
            t0, t1 = p["token0"], p["token1"]
            # native ETH pools are currency0 = 0x0: treat WETH as the same asset for attribution
            out = (a0 if a0 > 0 and (t0 == T or (T == C.WETH and t0 == C.NATIVE)) else 0) + (a1 if a1 > 0 and t1 == T else 0)
            first.setdefault(pid, n)
            if out > 0:
                legs[(p["dex"], pid)] = legs.get((p["dex"], pid), 0) + out
            else:
                via[pid] = p
        elif tp[0] == C.TOPIC_TRANSFER and a == T and len(tp) > 2:
            src = word_addr(tp[1])
            p = M.pool(src)
            if p and p["kind"] not in ("v3", "v4"):
                first.setdefault(src, n)
                legs[(p["dex"], src)] = legs.get((p["dex"], src), 0) + u(l["data"][:66])
    attributed = sum(legs.values())
    total = max(received, attributed)
    rows = []
    for (dex, pid), amt in sorted(legs.items(), key=lambda x: -x[1]):
        p = _pool(pid) or {}
        bps = amt * 10_000 // total if total else 0
        rows.append({**V.dex(dex), "pool": pid, "pair": p.get("symbol") or "", "amount": str(amt), "shareBps": bps, "sharePct": bps / 100})
    rest = total - attributed
    if total and rest * 10_000 // total >= 1:
        bps = rest * 10_000 // total
        rows.append({**V.dex("other"), "pool": None, "pair": "", "amount": str(rest), "shareBps": bps, "sharePct": bps / 100})
    # merge into per-dex totals as well (UI line "60% Uniswap V3 · 40% Aero")
    by_dex: dict[str, int] = {}
    for r in rows:
        by_dex[r["dex"]] = by_dex.get(r["dex"], 0) + int(r["amount"])
    dex_rows = [{**V.dex(d), "amount": str(a), "shareBps": a * 10_000 // total if total else 0,
                 "sharePct": (a * 10_000 // total) / 100 if total else 0} for d, a in sorted(by_dex.items(), key=lambda x: -x[1])]
    share = {r["pool"]: r for r in rows if r["pool"]}
    path = []
    for i, (pid, _) in enumerate(sorted(first.items(), key=lambda x: x[1])):
        p = _pool(pid) or (M.pool(pid) or {})
        dex = p.get("dex") or "other"
        fin = share.get(pid)
        path.append({"order": i + 1, **V.dex(dex), "pool": pid, "pair": p.get("symbol") or "", "role": "final" if fin else "via",
                     "shareBps": fin["shareBps"] if fin else None, "sharePct": fin["sharePct"] if fin else None})
    return {"split": rows, "byDex": dex_rows, "path": path,
            "via": [{**V.dex(p["dex"]), "pool": k, "pair": p.get("symbol") or ""} for k, p in via.items()][:30],
            "attributedBps": attributed * 10_000 // total if total else 0}
