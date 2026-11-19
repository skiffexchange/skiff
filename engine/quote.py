"""Quotes — the product core: every route the router offers for a swap, each simulated on the same pinned block (eth_simulateV1) and ranked
by what the wallet would actually receive; plus the best single pool as a baseline (Uniswap V3 QuoterV2, all fee tiers, same block).

Pipeline (same-chain 4663):
  1. ONE /advanced/routes call -> routes from several tools (one budget unit).
  2. In parallel: the baseline (one Multicall3 eth_call of QuoterV2 x 4 fee tiers) and the user's balance/allowance (one Multicall3).
  3. Per route: its transaction (advanced/stepTransaction; /quote as fallback bucket), then eth_simulateV1 [approve, swap] from the sender
     at the pinned block. Balance is overridden only when the sender is the neutral probe or does not hold the amount (flagged).
     Intent routes (the router's own solver tool) only escrow the input in the transaction — they are not simulated; ranked by their
     guaranteed minimum.
  4. Output = Transfer logs of the out token to the receiver (ETH: traceTransfers). Pool split decoded from the swap's logs (split.py).
Without a router key the tx fetches are capped (TX_CAP_NOKEY) to the best-quoted routes; the rest stay "quoted" (not simulated).
Cross-chain: routes with bridge steps; status "quoted" (the destination leg runs inside the bridge, not simulable here).
Every token amount is an integer string in raw units + decimals. USD figures are floats rounded to cents (display only)."""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import config as C
import market as M
import router
import split as SP
import tokens as TK
import venues as V
from chain import RpcError, allowance_data, approve_data, balance_of_data, balance_override, block_number, multicall, pad, simulate, u, word_addr

log = logging.getLogger("rhdex.quote")
POOL = ThreadPoolExecutor(12, thread_name_prefix="quote")
TX_CAP_NOKEY = int(__import__("os").environ.get("TX_CAP_NOKEY") or 3)   # recording replays: TX_CAP_NOKEY=1 saves budget
GAP_TOOLS = ["kyberswap", "sushiswap"]
FEE_TIERS = (100, 500, 3000, 10000)
ETH_PSEUDO = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"     # eth_simulateV1 traceTransfers: native moves appear as Transfer logs from here

_routes_mem: dict[str, dict] = {}          # routeId -> server-side record (tool key, step, request) for /api/build, 10 min
_last: dict[str, tuple[float, dict]] = {}   # request key -> last good final result (stale serving)
_inflight: dict[str, "Job"] = {}
_lock = threading.Lock()
STATS = {"quotes": 0, "cached": 0, "coalesced": 0, "stale_served": 0, "busy": 0, "sims": 0, "sim_fail": 0}


class BadRequest(Exception):
    code = "bad_request"


class UnknownToken(BadRequest):
    """audit v4 works C-11: a toToken that is neither listed nor looked up (GET /api/token/<addr>) is never quoted — no budget spent on a
    random address"""
    code = "unknown_token"


class Busy(Exception):
    """Too many different quotes computing at once (audit v2 S1): 429, try again in a few seconds."""


MAX_ACTIVE = int(__import__("os").environ.get("RHDEX_MAX_QUOTES") or 24)
QUOTE_DEADLINE_S = 20        # audit v2 minor 6: a quote that is not done by then is answered chain_unavailable / timeout
ROUTER_BUDGET_S = 10         # audit v2 minor 5 (D10): all route-provider calls of one user quote share one 10 s deadline


class ChainUnavailable(Exception):
    """The chain node does not answer: the API says 503 chain_unavailable (never a 200 with a misleading text)."""


def _amt(raw: int, dec: int, usd: float | None) -> dict:
    return {"raw": str(raw), "decimals": dec, "usd": usd}


def _is_evm(a: str) -> bool:
    return isinstance(a, str) and a.startswith("0x") and len(a) == 42


def normalize(q: dict) -> dict:
    try:
        fc = int(q.get("fromChain") or C.CHAIN)
        tc = int(q.get("toChain") or C.CHAIN)
        amount = int(str(q.get("amount") or "0"))
    except ValueError:
        raise BadRequest("fromChain/toChain/amount must be integers (amount in raw token units)")
    if amount <= 0 or amount >= 1 << 200:
        raise BadRequest("amount must be a positive raw integer")
    ft, tt = str(q.get("fromToken") or ""), str(q.get("toToken") or "")
    if tc != C.CHAIN:
        raise BadRequest("toChain must be 4663")
    if fc not in C.PAY_CHAIN_IDS:
        raise BadRequest("fromChain is not a Pay-from network: see GET /api/tokens chains[]")
    if not _is_evm(tt):
        raise BadRequest("toToken must be a 0x address on 4663")
    if fc != C.SOLANA and not _is_evm(ft):
        raise BadRequest("fromToken must be a 0x address")
    ft = ft.lower() if ft.startswith("0x") else ft
    tt = tt.lower()
    if fc == tc and ft == tt:
        raise BadRequest("fromToken and toToken are the same")
    fa = q.get("fromAddress") or None
    if fa:
        if fc == C.SOLANA:
            if not (32 <= len(fa) <= 44):
                raise BadRequest("fromAddress is not a Solana address")
        elif not _is_evm(fa):
            raise BadRequest("fromAddress must be a 0x address")
        else:
            fa = fa.lower()
    ta = q.get("toAddress") or None
    if ta and not _is_evm(ta):
        raise BadRequest("toAddress must be a 0x address on 4663")
    fmeta = TK.find(fc, ft) or {}
    tmeta = TK.find(tc, tt) or {}
    if not tmeta:
        import tokenmeta
        cm = tokenmeta.cached(tt)
        if not cm:
            raise UnknownToken("toToken is not a known token on 4663: look it up with GET /api/token/<address> first")
        tmeta = {k: cm.get(k) for k in ("symbol", "name", "decimals", "kind", "logo")}
    fdec = q.get("fromDecimals") if q.get("fromDecimals") not in (None, "") else fmeta.get("decimals")
    tdec = q.get("toDecimals") if q.get("toDecimals") not in (None, "") else tmeta.get("decimals")
    return {"fromChain": fc, "fromToken": ft, "toChain": tc, "toToken": tt, "amount": amount, "fromAddress": fa,
            "toAddress": (ta or "").lower() or None, "fromDecimals": int(fdec) if fdec is not None else None,
            "toDecimals": int(tdec) if tdec is not None else None, "fromMeta": fmeta, "toMeta": tmeta}


def req_key(r: dict) -> str:
    k = [r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], r["fromAddress"] or "", r["toAddress"] or ""]
    return hashlib.sha1(json.dumps(k).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ baseline: best single pool
_fee_cache: dict[str, int | None] = {}
MIN_LIQ_X = 2          # a comparable single pool holds at least 2 x the trade size in liquidity (coordinator 16.11)


def pool_fees(pools: list[str], block: str = "latest") -> dict[str, int | None]:
    need = [p for p in pools if p not in _fee_cache]
    if need:
        res = multicall([(p, "0xddca3f43") for p in need], block=block)     # fee()
        for p, (ok, ret) in zip(need, res):
            _fee_cache[p] = u(ret[:66]) if ok and len(ret) >= 66 else None
    return {p: _fee_cache.get(p) for p in pools}


def comparable_pools(tin: str, tout: str, in_usd: float | None) -> tuple[list[dict], list[dict]]:
    """(eligible, all) Uniswap V3 pools of the direct pair; eligible = liquidity >= MIN_LIQ_X x trade size (DexScreener liquidity)."""
    allp = [p for p in M.pools_for_pair(tin, tout) if p["dex"] == "uniswap-v3"]
    if in_usd is None:
        return [], allp
    return [p for p in allp if (p["liqUsd"] or 0) >= MIN_LIQ_X * in_usd], allp


def baseline(from_token: str, to_token: str, amount: int, block: str, to_dec: int | None, from_dec: int | None = None) -> dict | None:
    """Best SINGLE pool among pools deep enough to compare with (liquidity >= 2 x trade). Uniswap V3 via QuoterV2 (fee read from each
    pool); Uniswap V4 single pools are not quoted yet (no pool keys / V4 Quoter answers 0 here). No comparable pool -> available false,
    reason no_comparable_pool (vsSinglePool is then null). `thin` stays for debugging: the deepest direct pool is shallower than 2 x."""
    tin = C.WETH if from_token == C.NATIVE else from_token
    tout = C.WETH if to_token == C.NATIVE else to_token
    in_usd = M.usd(amount, from_dec, tin) if (from_dec is not None and M.price(tin)) else None
    elig, allp = comparable_pools(tin, tout, in_usd)
    deepest = max(allp, key=lambda p: p["liqUsd"] or 0) if allp else None
    dbg = {"kind": "single-pool", "minLiqUsd": round(MIN_LIQ_X * in_usd, 2) if in_usd else None,
           "deepestPoolLiqUsd": deepest["liqUsd"] if deepest else None, "thin": not elig,
           "scope": "uniswap-v3 direct pools; uniswap-v4 single pools not included yet"}
    if not elig:
        return {**dbg, "available": False, "reason": "no_comparable_pool" if in_usd is not None else "no_price"}
    try:
        fees = pool_fees([p["id"] for p in elig])
        cand = [(p, f) for p in elig if (f := fees.get(p["id"]))]
        res = multicall([(C.QUOTER_V2, "0xc6a5026a" + pad(tin) + pad(tout) + pad(amount) + pad(f) + pad(0)) for _, f in cand], block=block)
    except Exception as e:
        return {**dbg, "available": False, "reason": "chain_unavailable", "error": str(e)[:120]}
    best = None
    for (p, f), (ok, ret) in zip(cand, res):
        out = u(ret[:66]) if ok and len(ret) >= 66 else 0
        if out and (best is None or out > best[2]):
            best = (p, f, out)
    if not best:
        return {**dbg, "available": False, "reason": "no_comparable_pool"}
    p, f, out = best
    return {**dbg, "available": True, "reason": None, **V.dex("uniswap-v3"), "feeTier": f, "feePct": f / 10_000, "pool": p["id"],
            "pair": p["symbol"], "poolLiqUsd": p["liqUsd"], "thin": False, "candidates": len(cand),
            "out": _amt(out, to_dec or 18, M.usd(out, to_dec or 18, tout)), "method": "QuoterV2.quoteExactInputSingle"}


def _scrub(b: dict) -> dict:
    """No internal error strings in public answers (audit v1 P1-5): chain failures become reason chain_unavailable."""
    b = {k: v for k, v in b.items() if k != "error"}
    for x in b.get("skipped") or []:
        rs = str(x.get("reason") or "")
        if "unavailable" in rs or "Error" in rs or "rpc" in rs.lower() or "historical state" in rs:
            x["reason"] = "chain_unavailable"
    return b


def _base(*a):
    import singlepool
    try:
        return _scrub(singlepool.baseline(*a))
    except Exception:
        return {"kind": "single_pool", "available": False, "reason": "chain_unavailable"}


def vs_single(best_raw: int | None, base: dict | None, to_token: str, tdec: int, same: bool, best_ok: bool) -> dict:
    """vsSinglePool / vsSinglePoolUsd / vsSinglePoolReason / vsSinglePoolDebug. Rule (coordinator 16.11): always given when a simulated best
    route and a base exist — the UI shows it only when > 0 — except when the base is below the best route by >= VS_POOL_MAX_GAP_PCT %
    (pools too thin to be a fair comparison): then null with reason pools_too_thin and the raw difference only in vsSinglePoolDebug."""
    if not same:
        return {"vsSinglePool": None, "vsSinglePoolUsd": None, "vsSinglePoolReason": "cross_chain", "vsSinglePoolDebug": None}
    if not (best_ok and best_raw):
        return {"vsSinglePool": None, "vsSinglePoolUsd": None, "vsSinglePoolReason": "no_simulated_route", "vsSinglePoolDebug": None}
    if not (isinstance(base, dict) and base.get("available") and base.get("out")):
        return {"vsSinglePool": None, "vsSinglePoolUsd": None, "vsSinglePoolReason": (base or {}).get("reason") or "no_pool", "vsSinglePoolDebug": None}
    b = int(base["out"]["raw"])
    d = best_raw - b
    px = M.price(to_token)
    gap = d * 100 / best_raw
    vs = {"raw": str(d), "decimals": tdec, "usd": round(d * px / 10 ** tdec, 2) if px else None, "pct": round(d * 100 / b, 4) if b else None,
          "gapPctOfBest": round(gap, 4)}
    if gap >= C.VS_POOL_MAX_GAP_PCT:
        return {"vsSinglePool": None, "vsSinglePoolUsd": None, "vsSinglePoolReason": "pools_too_thin", "vsSinglePoolDebug": vs}
    return {"vsSinglePool": vs, "vsSinglePoolUsd": vs["usd"], "vsSinglePoolReason": None, "vsSinglePoolDebug": None}


# ------------------------------------------------------------------ simulation of one route tx
def sim_route(tx: dict, approval: str | None, r: dict, sender: str, override_balance: bool, block: str) -> dict:
    ft, tt = r["fromToken"], r["toToken"]
    recv = (r["toAddress"] or sender).lower()
    amount = r["amount"]
    ov: dict = {}
    if override_balance:
        o = balance_override(ft, sender, amount)
        if o is None:
            return {"ok": False, "error": "cannot override the input balance for simulation (unknown token layout)"}
        for k, v in o.items():
            ov.setdefault(k, {}).update(v)
    ov.setdefault(sender, {}).setdefault("balance", hex(10 ** 18)) if override_balance else None
    calls = []
    if ft != C.NATIVE and approval:
        calls.append({"from": sender, "to": ft, "data": approve_data(approval, amount), "gas": hex(150_000)})
    calls.append({"from": sender, "to": tx["to"], "data": tx["data"], "value": tx.get("value") or "0x0", "gas": hex(30_000_000)})
    STATS["sims"] += 1
    try:
        req_calls = calls
        res = _simulate(req_calls, ov, block, trace=(tt == C.NATIVE))
    except RpcError as e:
        STATS["sim_fail"] += 1
        return {"ok": False, "error": str(e)[:160], "rpc": True}
    c = res["calls"][-1]
    if len(calls) == 2 and res["calls"][0].get("status") != "0x1":
        return {"ok": False, "error": "approve reverted", "block": res["block"]}
    if c.get("status") != "0x1":
        return {"ok": False, "error": ((c.get("error") or {}).get("message") or "reverted")[:160], "block": res["block"],
                "gasUsed": u(c.get("gasUsed"))}
    logs = c.get("logs") or []
    want = ETH_PSEUDO if tt == C.NATIVE else tt
    got = 0
    for l in logs:
        tp = l.get("topics") or []
        if l["address"].lower() == want and len(tp) > 2 and tp[0] == C.TOPIC_TRANSFER and word_addr(tp[2]) == recv:
            got += u(l["data"][:66])
    return {"ok": got > 0, "out": got, "block": res["block"], "gasUsed": u(c.get("gasUsed")), "logs": logs,
            "error": None if got > 0 else "no output reached the receiver"}


def _simulate(calls, ov, block, trace=False):
    req = [{"blockStateCalls": [{"stateOverrides": ov, "calls": calls}], "validation": False, "traceTransfers": bool(trace)}, block]
    from chain import rpc
    try:
        res = rpc("eth_simulateV1", req, retries=2, timeout=15)
    except RpcError as e:
        if "unavailable" not in str(e):
            raise
        req[1] = "latest"                      # the fallback node may lag the pinned block by a few blocks
        res = rpc("eth_simulateV1", req, url=C.RPC_FALLBACK, retries=1, timeout=15)
    b = res[0]
    return {"block": u(b.get("number")), "calls": b.get("calls") or []}


# ------------------------------------------------------------------ route -> public shape
def unavailable_reason(e: BaseException) -> str:
    """why a source gave no answer: rate_limited (our budget / the provider's 429), timeout, or provider_error — never «no route»"""
    if isinstance(e, router.Budget):
        return "rate_limited"
    if isinstance(e, router.Upstream):
        if e.code == 429:
            return "rate_limited"
        if e.code in (400, 404, 422):
            return "no_route"
        return "timeout" if e.code == 0 and "timeout" in str(e.body).lower() else "provider_error"
    if isinstance(e, TimeoutError) or "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower():
        return "timeout"
    return "provider_error"


def tool_code_state(code) -> tuple[str, str | None]:
    """a tool the routes answer lists as failed: a timeout / rate limit / unknown error there is «did not answer» (unavailable + reason),
    a real refusal (no route, amount too high or low, tool not allowed, liquidity) is no_route"""
    c = str(code or "").upper()
    if "RATE" in c or c == "429":
        return "unavailable", "rate_limited"
    if "TIMEOUT" in c:
        return "unavailable", "timeout"
    if c in ("UNKNOWN_ERROR", "RPC_ERROR", "TOOL_SPECIFIC_ERROR_TIMEOUT", "SERVER_ERROR"):
        return "unavailable", "provider_error"
    return "no_route", None


def _fees(steps: list[dict], in_usd: float | None) -> dict:
    route_usd, route_pct, route_items, bridge, venue = 0.0, 0.0, [], [], []
    gas_usd, gas_limit = 0.0, 0
    src_chain = ((steps[0].get("action") or {}).get("fromChainId")) if steps else None
    native_fee, native_meta, native_usd = 0, None, 0.0   # fees paid ON TOP, in the source network's coin: the `value` of a token swap
    for s in steps:
        e = s.get("estimate") or {}
        cross = any(i.get("type") == "cross" for i in s.get("includedSteps") or []) or s.get("type") == "cross"
        for f in e.get("feeCosts") or []:
            tk = f.get("token") or {}
            try:
                fu = float(f.get("amountUSD") or 0)
                fp = float(f.get("percentage") or 0)
            except ValueError:
                fu, fp = 0.0, 0.0
            if not f.get("included") and str(tk.get("address") or "").lower() in (C.NATIVE, ETH_PSEUDO) and \
                    (tk.get("chainId") is None or src_chain is None or int(tk.get("chainId")) == int(src_chain)):
                try:
                    native_fee += int(str(f.get("amount") or "0"))
                    native_meta = native_meta or {"symbol": tk.get("symbol"), "decimals": tk.get("decimals")}
                    native_usd += fu
                    continue     # audit v4 works C-8: shown once, as nativeFee — not again inside the bridge / venue fee rows
                except ValueError:
                    pass
            item = {"usd": round(fu, 2), "pct": round(fp * 100, 4), "included": bool(f.get("included")),
                    "amount": str(f.get("amount") or "0"), "token": (f.get("token") or {}).get("symbol"),
                    "tokenDecimals": (f.get("token") or {}).get("decimals")}
            if f.get("feeSplit") is not None or V.FORBIDDEN.search(str(f.get("name") or "")):
                if f.get("included"):
                    route_usd += fu
                    route_pct += fp * 100
                route_items.append(item)
            elif cross:
                bridge.append({**item, "label": V.sanitize(str(f.get("name") or "Bridge fee"))})
            else:
                venue.append({**item, "label": V.sanitize(str(f.get("name") or "Fee"))})
        for g in e.get("gasCosts") or []:
            try:
                gas_usd += float(g.get("amountUSD") or 0)
                gas_limit += int(g.get("limit") or 0)
            except ValueError:
                pass
    return {"ours": {"pct": 0, "usd": 0.0},
            "route": {"pct": round(route_pct, 4), "usd": round(route_usd, 2), "items": route_items},
            "bridge": bridge, "venue": venue,
            "gas": {"usd": round(gas_usd, 4), "limit": gas_limit},
            "totalUsd": round(route_usd + sum(b["usd"] for b in bridge if b["included"]) + sum(v["usd"] for v in venue if v["included"]), 2),
            "nativeFee": ({"raw": str(native_fee), "decimals": native_meta.get("decimals"), "symbol": native_meta.get("symbol"),
                           "usd": round(native_usd, 2), "chainId": int(src_chain) if src_chain is not None else None}
                          if native_fee > 0 and native_meta else None)}


def _steps(steps: list[dict]) -> list[dict]:
    out = []
    for s in steps:
        for i in s.get("includedSteps") or [s]:
            if i.get("tool") in V.HIDDEN or i.get("type") == "protocol":
                continue
            a = i.get("action") or {}
            out.append({"type": i.get("type") if i.get("type") in ("swap", "cross") else "swap", **V.tool(i.get("tool")),
                        "fromChain": a.get("fromChainId"), "toChain": a.get("toChainId"),
                        "fromToken": (a.get("fromToken") or {}).get("symbol"), "toToken": (a.get("toToken") or {}).get("symbol"),
                        "durationS": (i.get("estimate") or {}).get("executionDuration")})
    return out


def _main_tool(steps: list[dict]) -> str:
    swaps = [i for s in steps for i in s.get("includedSteps") or [] if i.get("type") == "swap"]
    cross = [i for s in steps for i in s.get("includedSteps") or [] if i.get("type") == "cross"]
    if cross:
        return cross[0].get("tool") or steps[0].get("tool")
    if swaps:
        return swaps[-1].get("tool")
    return steps[0].get("tool")


class Job:
    """One quote computation; listeners (SSE) get events as they happen, the final result is a Future."""

    def __init__(self, r: dict, prio: str):
        self.r, self.prio = r, prio
        self.fut: Future = Future()
        self.events: list[tuple[str, dict]] = []
        self.timeline: list[tuple[int, str, dict]] = []     # (ms since start, event, data) — what replay.py records
        self.t0 = time.time()
        self.router_deadline = self.t0 + (ROUTER_BUDGET_S if prio != "board" else 90)
        self.subs: list[queue.Queue] = []
        self.asubs: list = []                                   # (loop, asyncio.Queue) — SSE without holding a worker thread
        self.lock = threading.Lock()

    def rt(self) -> float:
        """seconds left for route-provider calls of this quote"""
        return self.router_deadline - time.time()

    def emit(self, ev: str, data: dict):
        data = V.sanitize(data)
        with self.lock:
            self.events.append((ev, data))
            self.timeline.append((int((time.time() - self.t0) * 1000), ev, data))
            subs = list(self.subs)
        for q in subs:
            q.put((ev, data))
        for loop, aq in list(self.asubs):
            loop.call_soon_threadsafe(aq.put_nowait, (ev, data))

    def source(self, key: str, name: str, logo: str | None, kind: str, state: str, out: dict | None = None, route_id: str | None = None,
               **extra):
        """SSE `source` (NEW-PITCH 16.11): one source we really asked / that really answered, with the ms since the quote started."""
        d = {"key": key, "name": name, "logo": logo, "kind": kind, "state": state, "ms": int((time.time() - self.t0) * 1000)}
        if out is not None:
            d["out"] = out
        if route_id:
            d["routeId"] = route_id
        d.update({k: v for k, v in extra.items() if v is not None})
        self.emit("source", d)

    def emit_pool_source(self, tag: str, b: dict | None, _o=None, route_id: str | None = None):
        """the one-pool base (`baseline`, kind pool) or the direct Uniswap option (`direct`, kind dex) after its simulation"""
        kind = "pool" if tag == "baseline" else "dex"
        if isinstance(b, dict) and b.get("available") and b.get("out"):
            d = V.dex(b.get("dex") or "other")
            self.source(tag, d["name"], d["logo"], kind, "simulated", b["out"], route_id, dex=b.get("dex"), pair=b.get("pair"),
                        pool=b.get("pool"), block=b.get("block"))
        else:
            self.source(tag, "One pool" if tag == "baseline" else "Uniswap", V.dex("other" if tag == "baseline" else "uniswap-v3")["logo"], kind,
                        "no_route", reason=(b or {}).get("reason") or "no_pool")

    def subscribe_async(self, loop):
        import asyncio
        aq: asyncio.Queue = asyncio.Queue()
        with self.lock:
            for e in self.events:
                aq.put_nowait(e)
            if self.fut.done():
                aq.put_nowait(("_end", {}))
            else:
                self.asubs.append((loop, aq))
        return aq

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            for e in self.events:
                q.put(e)
            self.subs.append(q)
            if self.fut.done():
                q.put(("_end", {}))
        return q

    def finish(self, res: dict | None, err: Exception | None = None):
        with self.lock:
            subs = list(self.subs)
        if err:
            self.fut.set_exception(err)
        else:
            self.fut.set_result(res)
        for q in subs:
            q.put(("_end", {}))
        for loop, aq in list(self.asubs):
            loop.call_soon_threadsafe(aq.put_nowait, ("_end", {}))


def _run(job: Job):
    r = job.r
    t0 = time.time()
    same = r["fromChain"] == C.CHAIN
    sender = r["fromAddress"] if (r["fromAddress"] and _is_evm(r["fromAddress"])) else (C.PROBE if r["fromChain"] != C.SOLANA else None)
    probe = sender == C.PROBE
    fdec = r["fromDecimals"]
    tdec = r["toDecimals"] or 18
    in_px = TK.ext_price(r["fromChain"], r["fromToken"])
    in_usd = round(r["amount"] * in_px / 10 ** fdec, 2) if (in_px and fdec is not None) else None
    notes = []
    # pinned block; then baseline, direct pool, balance and the routes call all in parallel. meta goes out at once, baseline and the
    # direct row as soon as each is ready — none of them waits for the route provider (audit v1 P1-1/P1-2)
    try:
        blk = block_number() if same else None
    except Exception:
        raise ChainUnavailable()
    B = hex(blk) if blk else "latest"
    f_routes = POOL.submit(lambda: router.routes(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], sender or r["fromAddress"],
                                                 r["toAddress"], job.prio, timeout=job.rt()))
    f_base = POOL.submit(_base, r["fromToken"], r["toToken"], r["amount"], B, tdec, fdec) if same else None
    f_dir = POOL.submit(_direct, r["fromToken"], r["toToken"], r["amount"], B, tdec, fdec) if same else None
    f_bal = None
    if same and not probe and sender:
        calls = [(r["fromToken"], balance_of_data(sender))] if r["fromToken"] != C.NATIVE else []
        f_bal = POOL.submit(lambda: (multicall(calls, block=B) if calls else None, _native_balance(sender, B)))
    job.emit("meta", {"request": _pub_req(r), "block": blk, "amountIn": _amt(r["amount"], fdec, in_usd) if fdec is not None else None,
                      "routesFound": None, "probeSender": probe})
    sent, sent_lock = set(), threading.Lock()

    def _once(tag, fn):
        """baseline / direct go out as soon as ready (callback) — or from _run before `done`, whichever comes first, exactly once"""
        with sent_lock:                 # held while emitting: _run cannot pass to `done` while a callback is still sending
            if tag in sent:
                return
            sent.add(tag)
            fn()

    px_out = M.price(r["toToken"])

    def _o(raw):
        return _amt(raw, tdec, round(raw * px_out / 10 ** tdec, 2) if px_out else None)

    def _emit_base(f):
        def go():
            b = f.result()
            job.emit("baseline", {"baseline": b})
            job.emit_pool_source("baseline", b, _o)
        _once("baseline", go)

    def _emit_direct(f):
        def go():
            raw = f.result()
            dd = direct_entry(raw, r, tdec, in_usd)
            if dd:
                job.emit("route", {"route": dd, "group": "routes"})
            if dd:
                job.emit_pool_source("direct", raw, _o, route_id=dd["id"])
            else:          # no Uniswap pool, or one that is not an executable option here (native ETH in/out)
                job.emit_pool_source("direct", {"available": False, "reason": (raw or {}).get("reason") if not (raw or {}).get("available")
                                                else "erc20_only"})
        _once("direct", go)
    if same:
        job.source("baseline", "One pool", V.dex("other")["logo"], "pool", "asked")
        job.source("direct", "Uniswap", V.dex("uniswap-v3")["logo"], "dex", "asked")
    if f_base:
        f_base.add_done_callback(_emit_base)
    if f_dir:
        f_dir.add_done_callback(_emit_direct)
    routes_unavailable = None
    try:
        rr = f_routes.result(timeout=max(1.0, job.rt() + 1) if job.prio != "board" else 90)
    except router.Budget as e:
        if not same:
            raise
        rr, routes_unavailable = {}, {"reason": "rate_limited", "retryInS": e.reset_in}
    except router.Upstream as e:
        if not same:
            msg = (e.body or {}).get("message") if isinstance(e.body, dict) else None
            raise BadRequest(V.sanitize(f"no routes ({e.code}): {msg or 'provider error'}")) if e.code in (400, 404, 422) else e
        reason = "not_configured" if isinstance(e, router.NotConfigured) else "no_routes" if e.code in (400, 404, 422) else "provider_error"
        rr, routes_unavailable = {}, {"reason": reason, "retryInS": 30}
    except Exception:
        if not same:
            raise router.Upstream(0, {"message": "timeout"})
        rr, routes_unavailable = {}, {"reason": "timeout", "retryInS": 30}
    if routes_unavailable:
        notes.append("routing is not configured (ROUTE_API_URL): showing the direct pool only" if routes_unavailable["reason"] == "not_configured"
                     else "routes temporarily unavailable: showing the direct pool only")
    routes = rr.get("routes") or []
    unavailable, seen_un = [], set()
    for x in (rr.get("unavailableRoutes") or {}).get("failed", []) or []:
        for v in (x.get("subpaths") or {}).values():
            for f in v:
                t = V.tool(f.get("tool"))
                if t["displayKey"] in seen_un:                 # audit v1 P1-5 (swap-ux): one row per venue
                    continue
                seen_un.add(t["displayKey"])
                unavailable.append({**t, "code": f.get("code")})
    # every tool that answered the routes call, as it answered (NEW-PITCH: source rows)
    for rt in routes:
        steps = rt.get("steps") or []
        t = V.tool(_main_tool(steps))
        job.source(t["displayKey"], t["name"], t["logo"], "aggregator" if same else "bridge", "quoted", _o(int(rt.get("toAmount") or 0)),
                   _rid({"rt": rt, "tool": _main_tool(steps)}, r), venueKind=t["kind"],
                   min=_o(int(rt.get("toAmountMin") or 0)) if rt.get("toAmountMin") else None)   # audit v5 C-1: «≥» only before min
    for x in unavailable:
        st, why = tool_code_state(x.get("code"))
        job.source(x["displayKey"], x["name"], x["logo"], "aggregator" if same else "bridge", st, code=x.get("code"), reason=why,
                   venueKind=x["kind"])
    # sender balance
    override = probe
    insufficient = False
    if f_bal is not None:
        try:
            bal_res, nat = f_bal.result(timeout=20)
            have = nat if r["fromToken"] == C.NATIVE else (u(bal_res[0][1][:66]) if bal_res and bal_res[0][0] else 0)
            if have < r["amount"]:
                insufficient, override = True, True
                notes.append("sender balance below amount: simulated with a balance override")
        except Exception:
            override = True
            notes.append("balance read failed: simulated with a balance override")
    job.emit("routes_found", {"routesFound": len(routes), "insufficientBalance": insufficient, "routesUnavailable": routes_unavailable})
    # candidates
    cands = []
    for rt in routes:
        steps = rt.get("steps") or []
        tool = _main_tool(steps)
        intent = V.is_intent(tool)
        cands.append({"rt": rt, "steps": steps, "tool": tool, "intent": intent, "quoted": int(rt.get("toAmount") or 0),
                      "min": int(rt.get("toAmountMin") or 0)})
    # gap fill: venues the routes answer left out (KyberSwap often is, research 2.1) via /quote allowExchanges — only with a key or a
    # healthy quote bucket (> half left), never at board priority
    have = {c["tool"] for c in cands}
    gap_unavailable: list[str] = []
    gaps = [t for t in GAP_TOOLS if t not in have] if same and job.prio != "board" else []
    qb = router.budget_state().get("quote") or {}
    if gaps and (C.ROUTE_KEYED or (qb.get("remaining") is None or qb.get("remaining", 0) > (qb.get("limit") or 75) / 2)):
        for t in gaps:
            v = V.tool(t)
            job.source(v["displayKey"], v["name"], v["logo"], "aggregator", "asked", venueKind=v["kind"])

        def gap(t):
            try:
                return router.quote(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], sender, tool=t, prio=job.prio,
                                    to_address=r["toAddress"], timeout=job.rt())
            except Exception as e:
                return {"_unavailable": unavailable_reason(e)}
        for t, st in zip(gaps, POOL.map(gap, gaps)):
            v = V.tool(t)
            if st and st.get("_unavailable"):     # audit v4 M15: no answer (429 / timeout / provider error) is not «no route»
                gap_unavailable.append(st["_unavailable"])
                job.source(v["displayKey"], v["name"], v["logo"], "aggregator", "unavailable", reason=st["_unavailable"], venueKind=v["kind"])
                continue
            if not (st and st.get("transactionRequest") and st.get("tool") == t):
                job.source(v["displayKey"], v["name"], v["logo"], "aggregator", "no_route", venueKind=v["kind"])
            if st and st.get("transactionRequest") and st.get("tool") == t:
                e = st.get("estimate") or {}
                job.source(v["displayKey"], v["name"], v["logo"], "aggregator", "quoted", _o(int(e.get("toAmount") or 0)),
                           _rid({"rt": {"id": st.get("id") or t}, "tool": t}, r), venueKind=v["kind"],
                           min=_o(int(e.get("toAmountMin") or 0)) if e.get("toAmountMin") else None)
                cands.append({"rt": {"id": st.get("id") or t}, "steps": [st], "tool": t, "intent": False, "quoted": int(e.get("toAmount") or 0),
                              "min": int(e.get("toAmountMin") or 0), "tx": st["transactionRequest"], "approval": e.get("approvalAddress"),
                              "gapFill": True})
    cands.sort(key=lambda c: -c["quoted"])
    to_sim = [c for c in cands if same and not c["intent"] and len(c["steps"]) == 1]
    cap = len(to_sim) if C.ROUTE_KEYED else (2 if job.prio == "board" else _nokey_cap(len(to_sim)))
    for c in to_sim[cap:]:
        c["skip"] = "budget"
    to_sim = to_sim[:cap]

    results: dict[int, dict] = {}

    def work(c):
        step = c["steps"][0]
        if c.get("tx"):
            return c, sim_route(c["tx"], c.get("approval"), r, sender, override, B)
        try:
            st = router.step_transaction(step, prio=job.prio, timeout=job.rt())
        except router.Budget:
            try:
                st = router.quote(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], sender, tool=c["tool"], prio=job.prio,
                                  timeout=job.rt())
            except Exception as e:
                return c, {"ok": False, "error": "transaction not available (rate limited)", "budget": True}
        except Exception:
            # not simulated (provider error / deadline): the row stays "quoted", never "reverted" — that word is for real reverts
            return c, {"ok": False, "error": "transaction not available", "budget": True}
        tx = st.get("transactionRequest")
        if not tx:
            return c, {"ok": False, "error": "no transaction in the answer"}
        c["tx"], c["approval"] = tx, (st.get("estimate") or {}).get("approvalAddress")
        c["stepFresh"] = st
        return c, sim_route(tx, c["approval"], r, sender, override, B)

    # wave 1 = the best-quoted `cap` routes (known-flaky tools last); without a key, if fewer than 2 simulate fine, a second wave of up to 2
    flaky = {"bitget": 1, "nordstern": 1}
    to_sim.sort(key=lambda c: (flaky.get(c["tool"], 0), -c["quoted"]))
    rest = [c for c in cands if c.get("skip") == "budget"]
    rest.sort(key=lambda c: (flaky.get(c["tool"], 0), -c["quoted"]))
    to_sim = to_sim[:cap] if C.ROUTE_KEYED else sorted([c for c in cands if same and not c["intent"] and len(c["steps"]) == 1],
                                                       key=lambda c: (flaky.get(c["tool"], 0), -c["quoted"]))[:cap]
    to_sim += [c for c in cands if c.get("gapFill") and c not in to_sim]      # their transaction is already paid for
    for c in cands:
        if same and not c["intent"] and len(c["steps"]) == 1:
            c["skip"] = None if c in to_sim else "budget"
    rest = [c for c in cands if c.get("skip") == "budget"]
    futs = [POOL.submit(work, c) for c in to_sim]
    done_ok = 0
    i = 0
    while i < len(futs):
        c, sr = futs[i].result(timeout=90)
        i += 1
        if sr.get("ok"):
            done_ok += 1
        want_ok, max_total = min(2, cap), cap + (1 if cap == 1 else 2)
        if i == len(futs) and done_ok < want_ok and rest and len(futs) < max_total:
            for extra in rest[:min(2, max_total - len(futs))]:
                extra["skip"] = None
                futs.append(POOL.submit(work, extra))
            rest = [x for x in rest if x.get("skip") == "budget"]
        c["sim"] = sr
        p = _pub_route(c, r, fdec, tdec, in_usd)
        if p["status"] in ("ok", "reverted"):
            v = p["venue"]
            job.source(v["displayKey"], v["name"], v["logo"], "aggregator", "simulated" if p["status"] == "ok" else "reverted",
                       p["out"]["simulated"] if p["status"] == "ok" else None, p["id"], venueKind=v["kind"],
                       block=(p.get("sim") or {}).get("block"))
        job.emit("route", {"route": p, "group": _group(p, same)})
    base = f_base.result(timeout=60) if f_base else None
    if f_base:
        _emit_base(f_base)
    # the rest: intents and unsimulated
    for c in cands:
        if "sim" not in c:
            p = _pub_route(c, r, fdec, tdec, in_usd)
            job.emit("route", {"route": p, "group": _group(p, same)})
    allpub = [_pub_route(c, r, fdec, tdec, in_usd) for c in cands]
    direct = None
    if same and f_dir:
        try:
            direct = f_dir.result(timeout=60)
            _emit_direct(f_dir)
        except Exception:
            direct = None
    pub, quoted_only, best, vsd = finalize(allpub, base, r, same, tdec, in_usd, direct)
    for c in cands:     # every route of the answer gets a record — cross-chain and quoted-only too (audit v4 works)
        rid = _rid(c, r)
        with _lock:
            _routes_mem[rid] = {"at": time.time(), "tool": c["tool"], "step": (c.get("steps") or [None])[0], "r": {k: v for k, v in r.items()}}
    # the next executable route after each simulated one (audit v5 minor 10): a build refused simulation_reverted names it, the page
    # offers it (never signed without the person seeing it)
    ok_ids = [p["id"] for p in pub if p.get("status") == "ok"]
    with _lock:
        for i, rid in enumerate(ok_ids):
            if rid in _routes_mem:
                _routes_mem[rid]["next"] = ok_ids[i + 1] if i + 1 < len(ok_ids) else None
    if not same:
        notes.append("cross-chain: quoted, not simulated (the destination leg runs inside the bridge)")
    if same and not best:
        notes.append("no simulated route succeeded: nothing is ranked as best")
    if any(c.get("skip") == "budget" for c in cands):
        notes.append("some routes quoted only: simulation capped without a router key")
    res = {"request": _pub_req(r), "block": blk, "at": int(time.time()), "tookMs": int((time.time() - t0) * 1000), "stale": False,
           "fromToken": {**(r["fromMeta"] or {}), "chainId": r["fromChain"], "address": r["fromToken"]},
           "toToken": {**(r["toMeta"] or {}), "chainId": r["toChain"], "address": r["toToken"]},
           "amountIn": _amt(r["amount"], fdec, in_usd) if fdec is not None else {"raw": str(r["amount"]), "decimals": None, "usd": None},
           "probeSender": probe, "insufficientBalance": insufficient, "routesUnavailable": routes_unavailable,
           # audit v5 minor 6: the route provider did not answer (all routes, or some of the venues asked) — the card can say so
           "routerUnavailable": ({"reason": (routes_unavailable or {}).get("reason") or gap_unavailable[0], "all": bool(routes_unavailable)}
                                 if (routes_unavailable or gap_unavailable) else None),
           # D11: what the UI preselects. Same-chain = best (MOST). Cross-chain without a quoted bridge route = the solver row with the
           # highest guaranteed minimum (it is a real, executable route — never "No route")
           "defaultRoute": (best["id"] if best else (quoted_only[0]["id"] if (quoted_only and not same) else None)),
           "defaultBasis": ("most" if best else ("guaranteed_min" if (quoted_only and not same) else None)),
           "routes": pub, "quotedOnly": quoted_only, "best": best["id"] if best else None,
           "bestBasis": ("simulated" if same else "quoted") if best else None, "baseline": base,
           **vsd,
           "unavailable": unavailable[:20], "ourFeePct": 0, "notes": notes, "limitsEnabled": C.LIMITS_ENABLED}
    return V.sanitize(res)


DIRECT_DEXES = ("uniswap-v3", "uniswap-v4")
DIRECT_GAS_V3 = 230_000      # typical approve + SwapRouter02.exactInputSingle (fallback only; live builds use the simulated gas)
DIRECT_GAS_V4 = 330_000      # approve + Permit2.approve + V4 swap (swap alone measured 179,290 on SPY/USDG 16.11); fallback only


_GP = {"gp": 0, "at": 0.0}


def _gas_usd(gas: int | None) -> float | None:
    if not gas:
        return None
    try:
        now = time.time()
        if now - _GP["at"] > 30:                 # premium-2: eth_gasPrice took ~2.4 s on every build; the price moves slowly -> 30 s cache
            _GP.update(gp=u(rpc_any_("eth_gasPrice", [])), at=now)
        gp = _GP["gp"]
        px = M.price(C.NATIVE)
        return round(gas * gp * px / 1e18, 4) if px else None
    except Exception:
        return None


def rpc_any_(m, p):
    from chain import rpc_any
    return rpc_any(m, p)


def _direct(*a):
    import singlepool
    try:
        return _scrub(singlepool.best_uniswap(*a))
    except Exception:
        return {"kind": "direct", "available": False, "reason": "chain_unavailable"}


def direct_entry(base: dict | None, r: dict, tdec: int, in_usd: float | None) -> dict | None:
    """: on 4663 -> 4663 the base pool is an executable option `direct` when it is a Uniswap V3/V4 pool —
    a plain call from the user's wallet (V3: SwapRouter02.exactInputSingle; V4: UniversalRouter V4_SWAP after Permit2). No route fee, no
    our fee. ERC20 in and out only."""
    # `base` here is the direct option (singlepool.best_uniswap), not the vs-one-pool base
    if not (isinstance(base, dict) and base.get("available") and base.get("dex") in DIRECT_DEXES):
        return None
    if r["fromChain"] != C.CHAIN or r["fromToken"] == C.NATIVE or r["toToken"] == C.NATIVE:
        return None
    out = int(base["out"]["raw"])
    mn = out * (10_000 - int(C.SLIPPAGE * 10_000)) // 10_000
    px = M.price(r["toToken"])
    usd = (lambda x: round(x * px / 10 ** tdec, 2) if px else None)
    # D14: gas always has a figure — the simulated gas, or (no gasUsed, e.g. an old recording) a typical measured limit, flagged estimated
    gas_used = base.get("gasUsed") or (DIRECT_GAS_V4 if base.get("dex") == "uniswap-v4" else DIRECT_GAS_V3)
    gas_est = not base.get("gasUsed")
    gas_usd = _gas_usd(gas_used)
    rid = "direct-" + hashlib.sha1((base["pool"] + req_key(r)).encode()).hexdigest()[:14]
    with _lock:
        _routes_mem[rid] = {"at": time.time(), "tool": "direct", "direct": {k: base.get(k) for k in ("dex", "pool", "pair", "feeTier", "feePct", "liqUsd", "v4Key")},
                            "r": {k: v for k, v in r.items()}}
    impact = None
    in_px = TK.ext_price(r["fromChain"], r["fromToken"])
    if px and in_px and r["fromDecimals"] is not None:
        exp = r["amount"] * in_px / 10 ** r["fromDecimals"] / px * 10 ** tdec
        impact = round((1 - out / exp) * 100, 4) if exp else None
    name = V.dex(base["dex"])
    return {"id": rid, "rank": None, "kind": "direct", "venue": {"displayKey": base["dex"], "name": name["name"], "logo": name["logo"], "kind": "dex"},
            "status": "ok", "simulated": True, "delivery": "onchain", "rankOut": str(out),
            "pool": {"id": base["pool"], "pair": base.get("pair"), "feeTier": base.get("feeTier"), "feePct": base.get("feePct"),
                     "liqUsd": base.get("liqUsd")},
            "out": {"simulated": _amt(out, tdec, usd(out)), "quoted": _amt(out, tdec, usd(out)), "min": _amt(mn, tdec, usd(mn))},
            "simVsQuotedPct": 0.0, "valueLossPct": round((1 - usd(out) / in_usd) * 100, 4) if (usd(out) and in_usd) else None,
            "priceImpactPct": impact,
            "fees": {"ours": {"pct": 0, "usd": 0.0}, "route": {"pct": 0, "usd": 0.0, "items": []}, "bridge": [], "venue": [],
                     "gas": {"usd": gas_usd, "limit": int(gas_used * 13 // 10), "estimated": gas_est}, "totalUsd": 0.0},
            "gas": {"usd": gas_usd, "limit": int(gas_used * 13 // 10), "used": base.get("gasUsed"), "estimated": gas_est},
            "durationS": 0,
            "steps": [{"type": "swap", "displayKey": base["dex"], "name": name["name"], "logo": name["logo"], "kind": "dex", "fromChain": C.CHAIN,
                       "toChain": C.CHAIN, "fromToken": (r.get("fromMeta") or {}).get("symbol"), "toToken": (r.get("toMeta") or {}).get("symbol"),
                       "durationS": 0}],
            "split": [{**name, "dex": base["dex"], "pool": base["pool"], "pair": base.get("pair") or "", "amount": str(out), "shareBps": 10_000,
                       "sharePct": 100.0}],
            "splitByDex": [{**name, "dex": base["dex"], "amount": str(out), "shareBps": 10_000, "sharePct": 100.0}], "via": [],
            "path": [{"order": 1, **name, "dex": base["dex"], "pool": base["pool"], "pair": base.get("pair") or "", "role": "final",
                      "shareBps": 10_000, "sharePct": 100.0}],
            "sim": {"block": base.get("block"), "error": None, "gasUsed": base.get("gasUsed")}}


def finalize(allpub: list[dict], base, r: dict, same: bool, tdec: int, in_usd, direct=None):
    """Groups, ranks and picks MOST. Same-chain: the `direct` pool option competes with every simulated route; if it gives the most,
    vsSinglePool is null with reason best_is_pool. Intents / unsimulated -> quotedOnly (never MOST)."""
    allpub = [p for p in allpub if p.get("kind") != "direct"]
    if same:
        pub = [p for p in allpub if p["status"] in ("ok", "reverted")]
        quoted_only = [p for p in allpub if p["status"] in ("intent", "quoted")]
        d = direct_entry(direct, r, tdec, in_usd)
        if d:
            pub.append(d)
    else:
        pub = [p for p in allpub if p["status"] != "intent"]
        quoted_only = [p for p in allpub if p["status"] == "intent"]
    order = {"ok": 0, "quoted": 1, "reverted": 3}
    pub.sort(key=lambda x: (order.get(x["status"], 9), -int(x["rankOut"])))
    quoted_only.sort(key=lambda x: -int(x["out"]["min"]["raw"]))
    for i, p in enumerate(pub):
        p["rank"] = i + 1
        p["most"] = False
        p.setdefault("kind", "route")
    best = next((p for p in pub if p["status"] == "ok"), None) if same else next((p for p in pub if p["status"] == "quoted"), None)
    if best:
        best["most"] = True
    if best and best.get("kind") == "direct":
        routes_ok = [p for p in pub if p["status"] == "ok" and p.get("kind") != "direct"]
        dbg = None
        if routes_ok:
            dd = int(routes_ok[0]["out"]["simulated"]["raw"]) - int(best["out"]["simulated"]["raw"])
            px = M.price(r["toToken"])
            dbg = {"raw": str(dd), "decimals": tdec, "usd": round(dd * px / 10 ** tdec, 2) if px else None, "note": "best route - direct pool"}
        # no route simulated at all (budget): direct is MOST only by default — say so instead of claiming the pool beat the routes
        vsd = {"vsSinglePool": None, "vsSinglePoolUsd": None, "vsSinglePoolReason": "best_is_pool" if routes_ok else "no_simulated_route",
               "vsSinglePoolDebug": dbg, "directUncontested": not routes_ok}
    else:
        vsd = vs_single(int(best["out"]["simulated"]["raw"]) if best and best.get("out", {}).get("simulated") else None, base, r["toToken"], tdec,
                        same, bool(best and best["status"] == "ok"))
        vsd["directUncontested"] = False
    return pub, quoted_only, best, vsd


def _nokey_cap(n: int) -> int:
    """D1: simulate every route while the budget allows it. Without a key: all routes when the transaction buckets (stepTransaction +
    quote) hold >= 30 calls together, TX_CAP_NOKEY (3) above 6, else 1."""
    bs = router.budget_state()
    left = [b.get("remaining") for k, b in bs.items() if k in ("step", "quote") and b.get("remaining") is not None]
    if len(left) < 2:
        known = sum(left) + (50 if "step" not in bs else 0) + (75 if "quote" not in bs else 0)
    else:
        known = sum(left)
    if known >= 30:
        return n
    return TX_CAP_NOKEY if known > 6 else 1


def _group(p: dict, same: bool) -> str:
    if p["status"] == "intent" or (same and p["status"] == "quoted"):
        return "quotedOnly"
    return "routes"


def _native_balance(addr, B):
    from chain import rpc_any
    return u(rpc_any("eth_getBalance", [addr, B]))


def _rid(c, r) -> str:
    return hashlib.sha1((c["rt"].get("id") or "" + c["tool"]).encode() + req_key(r).encode()).hexdigest()[:20]


def _pub_req(r):
    return {"fromChain": r["fromChain"], "fromToken": r["fromToken"], "toChain": r["toChain"], "toToken": r["toToken"],
            "amount": str(r["amount"]), "fromDecimals": r["fromDecimals"], "toDecimals": r["toDecimals"], "fromAddress": r["fromAddress"]}


def _pub_route(c: dict, r: dict, fdec, tdec, in_usd) -> dict:
    rt = c["rt"]
    px = M.price(r["toToken"])
    usd = (lambda x: round(x * px / 10 ** tdec, 2) if px else None)
    sim = c.get("sim")
    if c["intent"]:
        status = "intent"
    elif sim is None:
        status = "quoted"
    elif sim.get("ok"):
        status = "ok"
    elif sim.get("rpc") or sim.get("budget"):
        status = "quoted"
    else:
        status = "reverted"
    simulated = sim["out"] if sim and sim.get("ok") else None
    rank_out = simulated if simulated is not None else (c["min"] if status == "intent" else (c["quoted"] if status == "quoted" else 0))
    out_usd = usd(simulated) if simulated is not None else None
    impact = None
    in_px = TK.ext_price(r["fromChain"], r["fromToken"])
    if simulated is not None and px and in_px and fdec is not None:
        expected = r["amount"] * in_px / 10 ** fdec / px * 10 ** tdec
        if expected > 0:
            impact = round((1 - simulated / expected) * 100 - _fees(c["steps"], None)["route"]["pct"], 4)
    fees = _fees(c["steps"], in_usd)
    sp = SP.decode(sim["logs"], r["toToken"], sim["out"]) if sim and sim.get("ok") and sim.get("logs") else None
    dur = sum(int((s.get("estimate") or {}).get("executionDuration") or 0) for s in c["steps"])
    main = V.tool(c["tool"])
    return {
        "id": _rid(c, r), "rank": None, "venue": main, "status": status, "simulated": simulated is not None,
        "delivery": "intent" if c["intent"] else "onchain",
        "rankOut": str(rank_out),
        "out": {"simulated": _amt(simulated, tdec, out_usd) if simulated is not None else None,
                "quoted": _amt(c["quoted"], tdec, usd(c["quoted"])), "min": _amt(c["min"], tdec, usd(c["min"]))},
        "simVsQuotedPct": round((simulated - c["quoted"]) * 100 / c["quoted"], 4) if (simulated is not None and c["quoted"]) else None,
        "valueLossPct": round((1 - out_usd / in_usd) * 100, 4) if (out_usd and in_usd) else None,
        "priceImpactPct": impact,
        "fees": fees, "gas": {**fees["gas"], "used": sim.get("gasUsed") if sim else None},
        "durationS": dur, "steps": _steps(c["steps"]),
        "split": sp["split"] if sp else [], "splitByDex": sp["byDex"] if sp else [], "via": sp["via"] if sp else [],
        "path": sp["path"] if sp else [],
        "sim": {"block": sim.get("block"), "error": ("chain_unavailable" if sim.get("rpc") else sim.get("error")), "gasUsed": sim.get("gasUsed")} if sim else
               {"block": None, "error": None, "skipped": c.get("skip") or ("intent: delivered by a solver after the transaction" if c["intent"]
                                                                          else "cross-chain" if r["fromChain"] != C.CHAIN else None)},
    }


def derive_sources(final: dict, timeline: list | None = None) -> list[tuple[int, dict]]:
    """`source` rows rebuilt from a finished answer and its recorded timeline — for recordings made before source events existed and for
    cached answers. Nothing is invented: every row is a venue / pool that is in the answer, with the state it ended in; the ms come from the
    recorded events (quoted = when the routes answer arrived — `routes_found`, else the first route event; simulated / reverted = that
    route's own event; pools = their baseline / direct events). Without a timeline every ms is 0."""
    tl = timeline or []
    same = int((final.get("request") or {}).get("fromChain") or C.CHAIN) == C.CHAIN
    t_meta = next((ms for ms, ev, _ in tl if ev == "meta"), 0)
    rms: dict[str, int] = {}
    base_ms = None
    for ms, ev, d in tl:
        if ev == "route" and isinstance(d, dict) and isinstance(d.get("route"), dict):
            rms.setdefault(d["route"].get("id"), ms)
        elif ev == "baseline" and base_ms is None:
            base_ms = ms
    routes = [p for p in (final.get("routes") or []) if p.get("kind") != "direct"]
    others = routes + list(final.get("quotedOnly") or [])
    t_found = next((ms for ms, ev, _ in tl if ev == "routes_found"), None)
    if t_found is None:
        t_found = min((rms[p["id"]] for p in others if p.get("id") in rms), default=t_meta)
    out: list[tuple[int, dict]] = []

    def add(ms, key, name, logo, kind, state, o=None, rid=None, **x):
        d = {"key": key, "name": name, "logo": logo, "kind": kind, "state": state, "ms": int(ms)}
        if o is not None:
            d["out"] = o
        if rid:
            d["routeId"] = rid
        d.update({k: v for k, v in x.items() if v is not None})
        d["derived"] = True
        out.append((int(ms), d))
    k = "aggregator" if same else "bridge"
    if same:
        add(t_meta, "baseline", "One pool", V.dex("other")["logo"], "pool", "asked")
        add(t_meta, "direct", "Uniswap", V.dex("uniswap-v3")["logo"], "dex", "asked")
        b = final.get("baseline")
        bm = base_ms if base_ms is not None else t_meta
        if isinstance(b, dict) and b.get("available") is not False and b.get("out"):
            dd = V.dex(b.get("dex") or "other")
            add(bm, "baseline", dd["name"], dd["logo"], "pool", "simulated", b["out"], None, dex=b.get("dex"), pair=b.get("pair"),
                pool=b.get("pool"), block=b.get("block"))
        else:
            add(bm, "baseline", "One pool", V.dex("other")["logo"], "pool", "no_route", reason=(b or {}).get("reason") or "no_pool")
        d = next((p for p in final.get("routes") or [] if p.get("kind") == "direct"), None)
        if d:
            v = d["venue"]
            add(rms.get(d["id"], bm), "direct", v["name"], v["logo"], "dex", "simulated", d["out"]["simulated"], d["id"],
                dex=v.get("displayKey"), pair=(d.get("pool") or {}).get("pair"), pool=(d.get("pool") or {}).get("id"),
                block=(d.get("sim") or {}).get("block"))
        else:
            add(bm, "direct", "Uniswap", V.dex("uniswap-v3")["logo"], "dex", "no_route", reason="no_uniswap_pool")
    for p in others:
        v = p.get("venue") or {}
        q = (p.get("out") or {}).get("quoted")
        add(min(t_found, rms.get(p.get("id"), t_found)), v.get("displayKey"), v.get("name"), v.get("logo"), k, "quoted", q, p.get("id"),
            venueKind=v.get("kind"), min=(p.get("out") or {}).get("min"))
        if p.get("status") in ("ok", "reverted"):
            add(rms.get(p.get("id"), t_found), v.get("displayKey"), v.get("name"), v.get("logo"), k,
                "simulated" if p["status"] == "ok" else "reverted", (p.get("out") or {}).get("simulated") if p["status"] == "ok" else None,
                p.get("id"), venueKind=v.get("kind"), block=(p.get("sim") or {}).get("block"))
    for x in final.get("unavailable") or []:
        st, why = tool_code_state(x.get("code"))
        add(t_found, x.get("displayKey"), x.get("name"), x.get("logo"), k, st, code=x.get("code"), reason=why, venueKind=x.get("kind"))
    out.sort(key=lambda e: e[0])
    return out


def get(q: dict, prio: str = "user", stream: bool = False):
    """-> (job, cached_result). Coalesces identical requests in flight, caches QUOTE_TTL_S, serves the last good answer (stale) when the
    router budget is out."""
    r = normalize(q)
    if r["fromDecimals"] is None:
        raise BadRequest("unknown fromToken: pass fromDecimals")
    k = req_key(r)
    now = time.time()
    with _lock:
        hit = _last.get(k)
        if hit and now - hit[0] < C.QUOTE_TTL_S:
            STATS["cached"] += 1
            return None, hit[1]
        job = _inflight.get(k)
        if job:
            STATS["coalesced"] += 1
            return job, None
        if len(_inflight) >= MAX_ACTIVE and prio != "board":
            raise Busy()
        job = Job(r, prio)
        _inflight[k] = job
    STATS["quotes"] += 1

    def runner():
        try:
            res = _run(job)
            try:
                import replay
                replay.record(job, res)
            except Exception as ex:
                log.warning("replay record: %s", ex)
            with _lock:
                _last[k] = (time.time(), res)
                if len(_last) > 500:
                    for kk in sorted(_last, key=lambda x: _last[x][0])[:100]:
                        _last.pop(kk, None)
            job.finish(res)
        except router.Budget as e:
            with _lock:
                old = _last.get(k)
            if old:
                STATS["stale_served"] += 1
                res = {**old[1], "stale": True, "staleAgeS": int(time.time() - old[0]), "retryInS": e.reset_in}
                job.emit("stale", {"retryInS": e.reset_in})
                job.finish(res)
            else:
                STATS["busy"] += 1
                job.finish(None, e)
        except Exception as e:
            log.warning("quote failed: %s", e, exc_info=not isinstance(e, (BadRequest, router.Upstream, ChainUnavailable)))
            job.finish(None, e)
        finally:
            with _lock:
                _inflight.pop(k, None)
            with _lock:
                for rid in [x for x, v in _routes_mem.items() if time.time() - v["at"] > 600]:
                    _routes_mem.pop(rid, None)

    threading.Thread(target=runner, daemon=True, name="quote-job").start()
    return job, None


def route_record(rid: str) -> dict | None:
    with _lock:
        return _routes_mem.get(rid)
