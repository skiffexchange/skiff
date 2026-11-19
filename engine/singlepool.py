"""Baseline "one pool" (coordinator 16.11): the DEEPEST pool of the direct pair (by DexScreener liquidity, any dex, V3-style or Uniswap V4),
simulated for the same input on the same block as the routes. No direct pool that can be simulated -> the best single-venue path
(Uniswap V3 QuoterV2 quoteExactInput through WETH or USDG), kind "single_venue".

How a pool is simulated, without any router:
  V3-style (Uniswap V3, Aero/Slipstream, Ramses, Up, Giga, Alandale/Nami-Algebra — all share swap(address,bool,int256,uint160,bytes)):
    eth_simulateV1 from a scratch address whose code is overridden with a 70-byte callback (any call: pay the positive of the first two
    int256 args in the token named in `data` to msg.sender), balance of the input token overridden. Output = Transfer of the out token to
    that address. Same number QuoterV2 returns for Uniswap V3 pools (checked by tools/verify.py).
  Uniswap V4: the PoolKey is read from the PositionManager that last modified liquidity in that pool (ModifyLiquidity log, last 100k
    blocks -> poolKeys(bytes25)), then UniversalRouter V4_SWAP exact-in-single (launchpad swap encoding) after Permit2 approvals.
A pool that cannot take the whole input (runs out of in-range liquidity and stops at the price limit) is skipped for the next deepest.
Prop-AMMs (Metric) and V2 pools are not simulated; a deeper pool that could not be simulated is listed in `skipped` with the reason."""
from __future__ import annotations

import threading
import time

import config as C
import market as M
import venues as V
from chain import RpcError, balance_override, multicall, pad, rpc, rpc_any, u, word_addr, words
from keccak import keccak256

SCRATCH = "0x2222222222222222222222222222222222222222"
PERMIT2 = C.PERMIT2
UR = C.UNIVERSAL_ROUTER
MIN_SQRT = 4295128740
MAX_SQRT = 1461446703485210103287273052203988822378723970341
TOPIC_MODIFY = "0x" + keccak256(b"ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)").hex()


def _callback_code() -> str:
    ops = ("6004356024356000821382829003020160443560240135"        # a0 a1 -> amt = a0 > 0 ? a0 : a1 ; token = data word
           "63a9059cbb60e01b600052" "3360045281602452"             # transfer(caller, amt) calldata in memory
           "6020600060446000600085" "5af1")                        # call(gas, token, 0, 0, 0x44, 0, 0x20)
    dest = len(ops) // 2 + 5                                       # ISZERO PUSH1 dest JUMPI STOP -> JUMPDEST
    return "0x" + ops + "15" + "60" + format(dest, "02x") + "57" + "00" + "5b" + "60008" + "0fd"


CALLBACK = _callback_code()
_keys: dict[str, dict | None] = {}
_klock = threading.Lock()


def _out_of(call: dict, token: str, to: str) -> int:
    t = C.ETH_PSEUDO if token == C.NATIVE else token
    got = 0
    for l in call.get("logs") or []:
        tp = l.get("topics") or []
        if l["address"].lower() == t and len(tp) > 2 and tp[0] == C.TOPIC_TRANSFER and word_addr(tp[2]) == to:
            got += u(l["data"][:66])
    return got


def _spent(call: dict, token: str, frm: str) -> int:
    t = C.ETH_PSEUDO if token == C.NATIVE else token
    return sum(u(l["data"][:66]) for l in call.get("logs") or [] if l["address"].lower() == t and len(l.get("topics") or []) > 2
               and l["topics"][0] == C.TOPIC_TRANSFER and word_addr(l["topics"][1]) == frm)


def _filled(call: dict, token: str, amount: int) -> tuple[bool, float]:
    """A pool that runs out of in-range liquidity stops at the price limit and takes only part of the input: not a valid base."""
    sp = _spent(call, token, SCRATCH)
    return sp * 1000 >= amount * 999, round(sp * 100 / amount, 2) if amount else 0.0


def _sim(calls, ov, block, trace=False):
    req = [{"blockStateCalls": [{"stateOverrides": ov, "calls": calls}], "validation": False, "traceTransfers": trace}, block]
    try:
        res = rpc("eth_simulateV1", req, retries=2, timeout=15)
    except RpcError as e:
        if "unavailable" not in str(e):
            raise
        req[1] = "latest"
        res = rpc("eth_simulateV1", req, url=C.RPC_FALLBACK, retries=1, timeout=15)
    return res[0]


def sim_v3(pool: dict, tin: str, tout: str, amount: int, block: str) -> dict:
    zf = pool["token0"] == tin
    data = ("0x128acb08" + pad(SCRATCH) + pad(1 if zf else 0) + pad(amount) + pad(MIN_SQRT if zf else MAX_SQRT) + pad(0xa0) + pad(32)
            + pad(tin))
    ov = {SCRATCH: {"code": CALLBACK, "balance": hex(10 ** 18)}}
    o = balance_override(tin, SCRATCH, amount)
    if o is None:
        return {"ok": False, "error": "input balance override unavailable"}
    for k, v in o.items():
        ov.setdefault(k, {}).update(v)
    b = _sim([{"from": SCRATCH, "to": pool["id"], "data": data, "gas": hex(8_000_000)}], ov, block)
    c = b["calls"][0]
    if c.get("status") != "0x1":
        return {"ok": False, "error": ((c.get("error") or {}).get("message") or "reverted")[:120]}
    out = _out_of(c, tout, SCRATCH)
    full, pct = _filled(c, tin, amount)
    if not full:
        return {"ok": False, "error": f"pool fills only {pct}% of the size (runs out of liquidity)", "filledPct": pct}
    fee = None
    try:
        r = rpc_any("eth_call", [{"to": pool["id"], "data": "0xddca3f43"}, block])
        fee = u(r[:66]) if r and len(r) >= 66 else None
    except Exception:
        pass
    return {"ok": out > 0, "out": out, "block": u(b.get("number")), "fee": fee, "method": "eth_simulateV1 pool.swap (direct)",
            "gasUsed": u(c.get("gasUsed"))}


def v4_key(pid: str) -> dict | None:
    with _klock:
        if pid in _keys:
            return _keys[pid]
    key = None
    try:
        key = _key_from_pm(C.V4_POSITION_MANAGER, pid)        # official V4 PositionManager first: one eth_call, no log scan
        if key:
            with _klock:
                _keys[pid] = key
            return key
        head = u(rpc_any("eth_blockNumber", []))
        for back in range(0, 600_000, 100_000):
            logs = rpc("eth_getLogs", [{"address": C.POOL_MANAGER, "topics": [TOPIC_MODIFY, pid], "fromBlock": hex(max(0, head - back - 99_999)),
                                        "toBlock": hex(head - back)}], retries=3)
            if logs:
                pm = word_addr(logs[-1]["topics"][2])
                r = rpc_any("eth_call", [{"to": pm, "data": "0x" + keccak256(b"poolKeys(bytes25)").hex()[:8] + pid[2:52].ljust(64, "0")}, "latest"])
                w = words(r)
                if len(w) >= 5 and (w[0] or w[1]):
                    k = {"currency0": word_addr(format(w[0], "064x")), "currency1": word_addr(format(w[1], "064x")), "fee": w[2],
                         "tickSpacing": w[3] - (1 << 256) if w[3] >= 1 << 255 else w[3], "hooks": word_addr(format(w[4], "064x"))}
                    enc = "".join(pad(x) for x in (k["currency0"], k["currency1"], k["fee"], k["tickSpacing"], k["hooks"]))
                    if "0x" + keccak256(bytes.fromhex(enc)).hex() == pid:          # the key must hash to the pool id
                        key = k
                break
    except Exception:
        return None                       # not cached: retry next time
    with _klock:
        _keys[pid] = key
    return key


def _key_from_pm(pm: str, pid: str) -> dict | None:
    r = rpc_any("eth_call", [{"to": pm, "data": "0x" + keccak256(b"poolKeys(bytes25)").hex()[:8] + pid[2:52].ljust(64, "0")}, "latest"])
    w = words(r or "0x")
    if len(w) < 5 or not (w[0] or w[1]):
        return None
    k = {"currency0": word_addr(format(w[0], "064x")), "currency1": word_addr(format(w[1], "064x")), "fee": w[2],
         "tickSpacing": w[3] - (1 << 256) if w[3] >= 1 << 255 else w[3], "hooks": word_addr(format(w[4], "064x"))}
    enc = "".join(pad(x) for x in (k["currency0"], k["currency1"], k["fee"], k["tickSpacing"], k["hooks"]))
    return k if "0x" + keccak256(bytes.fromhex(enc)).hex() == pid else None


def _bytes(h: str) -> str:
    b = h[2:] if h.startswith("0x") else h
    n = len(b) // 2
    return pad(n) + b + "00" * ((32 - n % 32) % 32)


def _bytes_arr(arr: list[str]) -> str:
    enc = [_bytes(x) for x in arr]
    off, head = len(arr) * 32, ""
    for e in enc:
        head += pad(off)
        off += len(e) // 2
    return pad(len(arr)) + head + "".join(enc)


def _ur_v4_single(key: dict, zf: bool, amount: int, deadline: int) -> str:
    kw = "".join(pad(x) for x in (key["currency0"], key["currency1"], key["fee"], key["tickSpacing"], key["hooks"]))
    swap = pad(32) + kw + pad(1 if zf else 0) + pad(amount) + pad(0) + pad(0) + pad(320) + pad(0)
    cin, cout = (key["currency0"], key["currency1"]) if zf else (key["currency1"], key["currency0"])
    settle = pad(cin) + pad(amount)
    take = pad(cout) + pad(0)
    actions = "060c0f"
    plan = pad(64) + pad(64 + len(_bytes(actions)) // 2) + _bytes(actions) + _bytes_arr([swap, settle, take])
    commands = "10"
    return "0x3593564c" + pad(96) + pad(96 + len(_bytes(commands)) // 2) + pad(deadline) + _bytes(commands) + _bytes_arr(["0x" + plan])


def sim_v4(pool: dict, tin: str, tout: str, amount: int, block: str) -> dict:
    key = v4_key(pool["id"])
    if not key:
        return {"ok": False, "error": "pool key not found"}
    cin = C.NATIVE if (tin == C.WETH and key["currency0"] == C.NATIVE) else tin
    zf = key["currency0"] == cin
    data = _ur_v4_single(key, zf, amount, int(time.time()) + 600)
    ov = {SCRATCH: {"balance": hex(amount + 10 ** 18 if cin == C.NATIVE else 10 ** 18)}}
    calls = []
    if cin != C.NATIVE:
        o = balance_override(cin, SCRATCH, amount)
        if o is None:
            return {"ok": False, "error": "input balance override unavailable"}
        for k, v in o.items():
            ov.setdefault(k, {}).update(v)
        calls.append({"from": SCRATCH, "to": cin, "data": "0x095ea7b3" + pad(PERMIT2) + pad(amount), "gas": hex(150_000)})
        calls.append({"from": SCRATCH, "to": PERMIT2, "data": "0x87517c45" + pad(cin) + pad(UR) + pad(amount) + pad(int(time.time()) + 3600),
                      "gas": hex(200_000)})
    calls.append({"from": SCRATCH, "to": UR, "data": data, "value": hex(amount) if cin == C.NATIVE else "0x0", "gas": hex(8_000_000)})
    b = _sim(calls, ov, block, trace=(tout == C.NATIVE))
    c = b["calls"][-1]
    if c.get("status") != "0x1":
        return {"ok": False, "error": ((c.get("error") or {}).get("message") or "reverted")[:120]}
    out = _out_of(c, tout, SCRATCH)
    # V4 settles through Permit2 -> PoolManager: input moved = Transfer of the input token from the scratch address (native: the value)
    if cin != C.NATIVE:
        full, pct = _filled(c, cin, amount)
        if not full:
            return {"ok": False, "error": f"pool fills only {pct}% of the size (runs out of liquidity)", "filledPct": pct}
    return {"ok": out > 0, "out": out, "block": u(b.get("number")), "fee": key["fee"], "hooks": key["hooks"], "key": key,
            "gasUsed": sum(u(x.get("gasUsed")) for x in b["calls"]),
            "tickSpacing": key["tickSpacing"], "method": "eth_simulateV1 UniversalRouter V4 exact-in-single"}


def single_venue(tin: str, tout: str, amount: int, block: str) -> dict | None:
    """Best Uniswap V3 two-hop path through WETH or USDG (QuoterV2.quoteExactInput, one multicall)."""
    fees = (100, 500, 3000, 10000)
    paths = []
    for hub in (C.WETH, C.USDG):
        if hub in (tin, tout):
            continue
        for f1 in fees:
            for f2 in fees:
                path = tin[2:] + format(f1, "06x") + hub[2:] + format(f2, "06x") + tout[2:]
                paths.append((hub, f1, f2, path))
    calls = [(C.QUOTER_V2, "0xcdca1753" + pad(0x40) + pad(amount) + _bytes(p[3])) for p in paths]
    res = multicall(calls, block=block)
    best = None
    for p, (ok, ret) in zip(paths, res):
        out = u(ret[:66]) if ok and len(ret) >= 66 else 0
        if out and (best is None or out > best[1]):
            best = (p, out)
    if not best:
        return None
    (hub, f1, f2, _), out = best
    return {"hub": hub, "fees": [f1, f2], "out": out}


def baseline(from_token: str, to_token: str, amount: int, block: str, to_dec: int | None, from_dec: int | None) -> dict:
    tin = C.WETH if from_token == C.NATIVE else from_token
    tout = C.WETH if to_token == C.NATIVE else to_token
    in_usd = M.usd(amount, from_dec, tin) if (from_dec is not None and M.price(tin)) else None
    tdec = to_dec or 18
    blk = u(rpc_any("eth_blockNumber", [])) if block == "latest" else u(block)
    pools = sorted([p for p in M.pools_for_pair(tin, tout) if p["kind"] in ("v3", "v4") and not (p["kind"] == "v4" and p["dex"] != "uniswap-v4")],
                   key=lambda p: -(p["liqUsd"] or 0))
    others = [p for p in M.pools_for_pair(tin, tout) if p not in pools]
    skipped = [{**V.dex(p["dex"]), "pool": p["id"], "liqUsd": p["liqUsd"], "reason": "not simulable (prop-AMM / V2 / other pool manager)"}
               for p in others if (p["liqUsd"] or 0) > ((pools[0]["liqUsd"] or 0) if pools else 0)]
    for p in pools[:3]:
        try:
            r = sim_v4(p, tin, tout, amount, hex(blk)) if p["kind"] == "v4" else sim_v3(p, tin, tout, amount, hex(blk))
        except Exception as e:
            r = {"ok": False, "error": str(e)[:120]}
        if not r.get("ok"):
            skipped.append({**V.dex(p["dex"]), "pool": p["id"], "liqUsd": p["liqUsd"], "reason": r.get("error") or "no output"})
            continue
        fee = r.get("fee")
        return {"kind": "single_pool", "available": True, "most": False, **V.dex(p["dex"]), "pool": p["id"], "pair": p["symbol"],
                "feeTier": fee, "feePct": round(fee / 10_000, 4) if isinstance(fee, int) and fee < 1_000_000 else None,
                "feeNote": "V4 dynamic-fee flag" if isinstance(fee, int) and fee >= 0x800000 else None,
                "liqUsd": p["liqUsd"], "thin": bool(in_usd and (p["liqUsd"] or 0) < in_usd), "block": r["block"],
                "out": {"raw": str(r["out"]), "decimals": tdec, "usd": M.usd(r["out"], tdec, tout)}, "method": r["method"],
                "gasUsed": r.get("gasUsed"), "v4Key": r.get("key"), "tokenIn": tin, "tokenOut": tout,
                "rule": "deepest direct pool of the pair (by liquidity), same input, same block", "skipped": skipped}
    try:
        sv = single_venue(tin, tout, amount, hex(blk))
    except Exception:
        sv = None
    if sv:
        return {"kind": "single_venue", "available": True, "most": False, **V.dex("uniswap-v3"), "pool": None,
                "pair": f"via {'WETH' if sv['hub'] == C.WETH else 'USDG'}", "feeTier": sv["fees"], "feePct": [f / 10_000 for f in sv["fees"]],
                "liqUsd": None, "thin": None, "block": blk, "out": {"raw": str(sv["out"]), "decimals": tdec, "usd": M.usd(sv["out"], tdec, tout)},
                "method": "QuoterV2.quoteExactInput (two hops, one venue)", "rule": "no simulable direct pool: best one-venue path",
                "skipped": skipped}
    return {"kind": "single_pool", "available": False, "reason": "no_pool", "skipped": skipped, "block": blk}


def best_uniswap(from_token: str, to_token: str, amount: int, block: str, to_dec: int | None, from_dec: int | None) -> dict:
    """The executable `direct` option (only Uniswap V3/V4): the best-output Uniswap pool of the direct pair among its 4 deepest
    Uniswap pools, simulated like the base. Aero / Ramses / Metric pools are never a direct option (they still can be the base)."""
    tin = C.WETH if from_token == C.NATIVE else from_token
    tout = C.WETH if to_token == C.NATIVE else to_token
    in_usd = M.usd(amount, from_dec, tin) if (from_dec is not None and M.price(tin)) else None
    tdec = to_dec or 18
    blk = u(rpc_any("eth_blockNumber", [])) if block == "latest" else u(block)
    pools = sorted([p for p in M.pools_for_pair(tin, tout) if p["dex"] in ("uniswap-v3", "uniswap-v4") and p["kind"] in ("v3", "v4")],
                   key=lambda p: -(p["liqUsd"] or 0))[:4]
    best, skipped = None, []
    for p in pools:
        try:
            r = sim_v4(p, tin, tout, amount, hex(blk)) if p["kind"] == "v4" else sim_v3(p, tin, tout, amount, hex(blk))
        except Exception as e:
            r = {"ok": False, "error": str(e)[:120]}
        if not r.get("ok"):
            skipped.append({**V.dex(p["dex"]), "pool": p["id"], "liqUsd": p["liqUsd"], "reason": r.get("error") or "no output"})
        elif best is None or r["out"] > best[1]["out"]:
            best = (p, r)
    if not best:
        return {"kind": "direct", "available": False, "reason": "no_uniswap_pool", "skipped": skipped}
    p, r = best
    fee = r.get("fee")
    return {"kind": "direct", "available": True, **V.dex(p["dex"]), "pool": p["id"], "pair": p["symbol"], "feeTier": fee,
            "feePct": round(fee / 10_000, 4) if isinstance(fee, int) and fee < 1_000_000 else None, "liqUsd": p["liqUsd"],
            "thin": bool(in_usd and (p["liqUsd"] or 0) < in_usd), "block": r["block"],
            "out": {"raw": str(r["out"]), "decimals": tdec, "usd": M.usd(r["out"], tdec, tout)}, "method": r["method"],
            "gasUsed": r.get("gasUsed"), "v4Key": r.get("key"), "tokenIn": tin, "tokenOut": tout, "skipped": skipped, "candidates": len(pools)}
