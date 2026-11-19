"""Execute without signing: /api/build returns the transaction(s) for the chosen route for the user's address; /api/status tracks them.

Build = re-quote the chosen venue for this sender (router /quote with allowExchanges/allowBridges = that tool; when that bucket is out:
/advanced/routes restricted to the tool + stepTransaction), re-simulate on the sender's REAL state (balance override only when the wallet
does not hold the amount — then `insufficientBalance` is true and the UI must not offer to sign), and refuse when the simulated output is
below the user's minOut. Two shapes are returned:
  batch7702  one transaction to = from = the EOA, data = Simple7702Account.executeBatch([approve?, swap]) (selector 0x34fcd5be); needs an
             EIP-7702 authorization for 0xe6Cae83B…555B when the account is not delegated yet (authorization.required); unavailable when the
             account is delegated elsewhere or is a contract.
  sequence   [approve (only if allowance < amount), swap] as plain transactions.
The server sends nothing and holds no key. Approve is exactly the amount, never unlimited."""
from __future__ import annotations

import time

import config as C
import market as M
import quote as Q
import router
import split as SP
import tokens as TK
import venues as V
from chain import (RpcError, allowance_data, approve_data, balance_of_data, enc_execute_batch, http_json, multicall, pad, rpc, rpc_any, u,
                   word_addr)


class Refuse(Exception):
    def __init__(self, code: str, status: int = 409, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


SLIP_FLOOR = 0.001          # the tightest tolerance a fresh cross-chain route is pulled with (0.1 %)
SLIP_CAP = 0.05             # the person's own setting is taken up to 5 %


def fresh_slippage(min_out: int, fresh_quoted: int, user_slip: float, fresh_min: int | None = None, pulled_slip: float | None = None) -> float | None:
    """the tolerance that makes the fresh route's guaranteed minimum = the minimum the person saw: 1 - minOut / fresh toAmount, clamped to
    [0.1 %, the person's slippage]. A bridge may take its own haircut on top of the tolerance (Across 17.11: min = toAmount x 0.99495 at
    0.5 %): measured from the first pull (k = its min / (toAmount x (1 - its tolerance))) and divided out. None: even 0.1 % does not
    cover minOut (the price moved too far) -> below_min with a fresh quote. Rounded DOWN (1e-6, one more step) so rounding never lands
    the provider's minimum under minOut."""
    if fresh_quoted <= 0 or min_out <= 0:
        return None if min_out > 0 else user_slip
    k = 1.0
    if fresh_min and pulled_slip is not None and 0 < pulled_slip < 1:
        k = min(1.0, fresh_min / (fresh_quoted * (1 - pulled_slip)))
    s = 1 - min_out / (fresh_quoted * k)
    s = int(s * 1e6) / 1e6 - 1e-6
    if s < SLIP_FLOOR:
        return None
    return max(SLIP_FLOOR, min(s, user_slip))


def _fresh_step(r: dict, tool: str, cross: bool, slippage: float | None = None) -> dict:
    sender = r["fromAddress"]
    try:
        return router.quote(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], sender,
                            tool=None if cross else tool, bridge=tool if cross else None, ttl=0, to_address=r["toAddress"], slippage=slippage)
    except router.Budget:
        if cross:
            raise
    rr = router.routes(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"], r["amount"], sender, allow_exchanges=[tool])
    rts = [x for x in rr.get("routes") or [] if Q._main_tool(x.get("steps") or []) == tool]
    if not rts or len(rts[0]["steps"]) != 1:
        raise Refuse("venue_unavailable", 409)
    return router.step_transaction(rts[0]["steps"][0])


def _sim(calls: list[dict], ov: dict, trace: bool):
    return Q._simulate(calls, ov, "latest", trace=trace)


def build(body: dict) -> dict:
    rid = body.get("routeId")
    rec = Q.route_record(rid) if rid else None
    if rid and not rec and not body.get("venue"):
        raise Refuse("route_expired", 410, message="this quote is no longer held: quote again")   # unknown or older than 10 min
    if (rec and rec.get("direct")) or str(body.get("venue") or "") in ("direct", "uniswap-v3", "uniswap-v4"):
        return build_direct(body, rec)
    q = dict(rec["r"]) if rec else {}
    for k in ("fromChain", "fromToken", "toChain", "toToken", "amount", "fromDecimals", "toDecimals", "toAddress"):
        if body.get(k) not in (None, ""):
            q[k] = body[k]
    q["fromAddress"] = body.get("fromAddress")
    if not q.get("fromAddress"):
        raise Refuse("fromAddress required", 400)
    r = Q.normalize({k: (str(v) if k == "amount" else v) for k, v in q.items() if k not in ("fromMeta", "toMeta")})
    if r["fromDecimals"] is None:
        raise Refuse("unknown fromToken: pass fromDecimals", 400)
    tool = rec["tool"] if rec else V.tool_key_of(str(body.get("venue") or ""))
    if not tool:
        raise Refuse("routeId or venue required", 400)
    try:
        min_out = int(str(body.get("minOut") or "0"))
    except ValueError:
        raise Refuse("minOut must be a raw integer", 400)
    mode = body.get("mode") or "auto"
    cross = r["fromChain"] != C.CHAIN
    try:
        user_slip = min(SLIP_CAP, max(SLIP_FLOOR, float(body.get("slippage") if body.get("slippage") not in (None, "") else C.SLIPPAGE)))
    except (TypeError, ValueError):
        raise Refuse("slippage must be a fraction, e.g. 0.005", 400)
    slip_log = None
    intent = V.is_intent(tool)
    try:
        st = _fresh_step(r, tool, cross)
        if (cross or intent) and min_out > 0:
            # same-chain Solver fill too (P0 17.11): its guaranteed minimum is fresh x (1 - 0.5 %), so a 1-wei drift under the quote put
            # it below minOut on every build; re-pull it with the tolerance matched to minOut, as for cross-chain.
            #: the transaction protects exactly the minimum the person saw. The fresh route's guaranteed minimum
            # below minOut -> pull it again with slippage = 1 - minOut / fresh toAmount (clamped to [0.1 %, the person's setting]).
            e0 = st.get("estimate") or {}
            q0, m0 = int(e0.get("toAmount") or 0), int(e0.get("toAmountMin") or 0)
            if m0 >= min_out:
                slip_log = {"slippage": C.SLIPPAGE, "reason": "default_covers_min", "repulled": False}
            else:
                s = fresh_slippage(min_out, q0, user_slip, fresh_min=m0, pulled_slip=C.SLIPPAGE)
                if s is None or s >= C.SLIPPAGE:   # tighter than the first pull is the only way up; the same tolerance gives the same min
                    raise _fresh_below_min(r, q0, m0, min_out, tool, st.get("id"))
                if s is None:
                    raise _fresh_below_min(r, q0, m0, min_out, tool, st.get("id"))
                st = _fresh_step(r, tool, cross, slippage=s)
                slip_log = {"slippage": s, "reason": "matched_to_min" if s < user_slip else "user_setting", "repulled": True,
                            "firstMin": str(m0), "firstQuoted": str(q0)}
    except router.Budget as e:
        raise Refuse("busy", 503, retryInS=e.reset_in)
    except router.Upstream as e:
        raise Refuse("venue_unavailable", 409, detail=V.sanitize(str((e.body or {}).get("message") if isinstance(e.body, dict) else e.code)))
    tx = st.get("transactionRequest") or {}
    if not tx.get("data"):
        raise Refuse("no_transaction", 502)
    est = st.get("estimate") or {}
    approval = (est.get("approvalAddress") or "").lower() or None
    tdec = r["toDecimals"] or 18
    quoted, qmin = int(est.get("toAmount") or 0), int(est.get("toAmountMin") or 0)
    pub_steps = Q._steps([st])
    fees = Q._fees([st], None)
    out = {"ok": True, "venue": V.tool(tool), "delivery": "intent" if intent else "onchain", "chainId": r["fromChain"], "from": r["fromAddress"],
           "fees": fees, "steps": pub_steps, "quoted": {"raw": str(quoted), "decimals": tdec}, "minOutRouter": {"raw": str(qmin), "decimals": tdec},
           "minOutUser": {"raw": str(min_out), "decimals": tdec}, "builtAt": int(time.time()), "expiresInS": 60, "warnings": []}
    if slip_log:
        out["freshSlippage"] = slip_log           # fields only: which tolerance the fresh route was pulled with, and why
    if cross:
        return V.sanitize({**out, **_cross(r, tx, approval, min_out, qmin, fees, quoted=quoted, tool=tool, step_id=st.get("id"))})
    user = r["fromAddress"]
    ft = r["fromToken"]
    # real state: balance, allowance, code
    calls = []
    if ft != C.NATIVE:
        calls = [(ft, balance_of_data(user))] + ([(ft, allowance_data(user, approval))] if approval else [])
    try:
        mc = multicall(calls) if calls else []
        code = rpc_any("eth_getCode", [user, "latest"]) or "0x"
        nat = u(rpc_any("eth_getBalance", [user, "latest"]))
    except RpcError:
        raise Refuse("chain_unavailable", 503)
    bal = nat if ft == C.NATIVE else (u(mc[0][1][:66]) if mc and mc[0][0] else 0)
    allow = u(mc[1][1][:66]) if len(mc) > 1 and mc[1][0] else 0
    need_approve = ft != C.NATIVE and approval and allow < r["amount"]
    ov = {}
    if bal < r["amount"]:
        out["warnings"].append("insufficient_balance")
        out["insufficientBalance"] = True
        from chain import balance_override
        o = balance_override(ft, user, r["amount"]) or {}
        for k, v in o.items():
            ov.setdefault(k, {}).update(v)
    else:
        out["insufficientBalance"] = False
    approve_tx = {"to": ft, "value": "0x0", "data": approve_data(approval, r["amount"])} if need_approve else None
    swap_tx = {"to": tx["to"].lower(), "value": tx.get("value") or "0x0", "data": tx["data"]}
    designator = "0xef0100" + C.SIMPLE7702[2:]
    delegated_ok = code.lower() == designator
    can_7702 = code in ("0x", "") or delegated_ok
    # simulate the plain sequence (works for every wallet)
    seq_calls = ([{"from": user, **approve_tx, "gas": hex(150_000)}] if approve_tx else []) + [{"from": user, **swap_tx, "gas": hex(30_000_000)}]
    trace = r["toToken"] == C.NATIVE
    try:
        res = _sim(seq_calls, ov, trace)
    except RpcError:
        raise Refuse("simulation_unavailable", 503)
    sc = res["calls"][-1]
    got = _received(sc, r, user)
    sim = {"block": res["block"], "status": "ok" if sc.get("status") == "0x1" else "reverted", "gasUsed": u(sc.get("gasUsed")),
           "error": (sc.get("error") or {}).get("message") if sc.get("status") != "0x1" else None,
           "out": {"raw": str(got), "decimals": tdec, "usd": M.usd(got, tdec, r["toToken"])}}
    if sim["status"] != "ok":
        raise Refuse("simulation_reverted", 409, simulation=V.sanitize(sim), nextRouteId=(rec or {}).get("next"))
    if intent:
        sim["note"] = "intent: the transaction escrows the input; a solver delivers at least minOutRouter afterwards"
        if qmin < min_out:
            raise _fresh_below_min(r, quoted, qmin, min_out, tool, st.get("id"))
    else:
        if got < min_out:
            raise Refuse("below_min", 409, simulatedOut=str(got), minOut=str(min_out))
        sp = SP.decode(sc.get("logs") or [], r["toToken"], got)
        out["split"], out["splitByDex"] = sp["split"], sp["byDex"]
    out["simulation"] = sim
    gas_seq = [int(u(c.get("gasUsed")) * 13 // 10) + 21_000 for c in res["calls"]]
    seq = []
    if approve_tx:
        seq.append({"kind": "approve", **approve_tx, "gasLimit": hex(gas_seq[0])})
    seq.append({"kind": "swap", **swap_tx, "gasLimit": hex(gas_seq[-1])})
    out["sequence"] = seq
    # 7702 batch
    if mode in ("auto", "7702") and can_7702:
        bcalls = ([(approve_tx["to"], 0, approve_tx["data"])] if approve_tx else []) + [(swap_tx["to"], int(swap_tx["value"], 16), swap_tx["data"])]
        data = enc_execute_batch(bcalls)
        ov2 = {k: dict(v) for k, v in ov.items()}
        ov2.setdefault(user, {})["code"] = designator
        try:
            r2 = _sim([{"from": user, "to": user, "data": data, "value": swap_tx["value"], "gas": hex(30_000_000)}], ov2, trace)
            c2 = r2["calls"][0]
            got2 = _received(c2, r, user)
            ok2 = c2.get("status") == "0x1" and (intent or got2 >= min_out)
        except RpcError:
            ok2, got2, c2 = False, 0, {}
        out["batch7702"] = {"available": ok2, "to": user, "value": swap_tx["value"], "data": data,
                            "gasLimit": hex(int(u(c2.get("gasUsed")) * 13 // 10) + 30_000) if ok2 else None,
                            "authorization": {"required": not delegated_ok, "contractAddress": C.SIMPLE7702, "chainId": C.CHAIN},
                            "simulatedOut": {"raw": str(got2), "decimals": tdec}, "calls": len(bcalls)}
    elif mode in ("auto", "7702"):
        out["batch7702"] = {"available": False, "reason": "account already has code (delegated elsewhere or a contract)"}
    if mode == "sequence":
        out.pop("batch7702", None)
    return V.sanitize(out)


def _received(c: dict, r: dict, user: str) -> int:
    recv = (r["toAddress"] or user).lower()
    want = Q.ETH_PSEUDO if r["toToken"] == C.NATIVE else r["toToken"]
    got = 0
    for l in c.get("logs") or []:
        tp = l.get("topics") or []
        if l["address"].lower() == want and len(tp) > 2 and tp[0] == C.TOPIC_TRANSFER and word_addr(tp[2]) == recv:
            got += u(l["data"][:66])
    return got


def _fresh_below_min(r: dict, quoted: int, qmin: int, min_out: int, tool: str | None, step_id: str | None) -> Refuse:
    """cross-chain / intent: the fresh GUARANTEED minimum is below what the person accepted. Carries the fresh numbers and a routeId for
    them, so the page offers «new quote: X» in one click (audit v5 works). basis guaranteed_min: no simulation is involved."""
    tdec = r["toDecimals"] or 18
    rid = None
    if tool:
        import hashlib
        rid = hashlib.sha1(f"{step_id or tool}{time.time()}".encode() + Q.req_key(r).encode()).hexdigest()[:20]
        with Q._lock:
            Q._routes_mem[rid] = {"at": time.time(), "tool": tool, "step": None, "r": {k: v for k, v in r.items()}}
    return Refuse("below_min", 409, basis="guaranteed_min", guaranteedMin=str(qmin), minOut=str(min_out),
                  fresh={"quoted": {"raw": str(quoted), "decimals": tdec}, "min": {"raw": str(qmin), "decimals": tdec}, "routeId": rid})


def _cross(r: dict, tx: dict, approval: str | None, min_out: int, qmin: int, fees: dict | None = None, quoted: int | None = None,
           tool: str | None = None, step_id: str | None = None) -> dict:
    # the person's minimum is compared with the fresh GUARANTEED minimum (the page sends minOut = the quote's out.min, not quoted - 0.5 %)
    if qmin < min_out:
        raise _fresh_below_min(r, quoted if quoted is not None else qmin, qmin, min_out, tool, step_id)
    out = {"simulation": {"status": "not_simulated", "note": "cross-chain: quoted, not simulated"}}
    if r["fromChain"] == C.SOLANA:
        out["solana"] = {"transaction": tx.get("data"), "encoding": "base64"}
        return out
    # the only contract a source-network transaction may call (and be approved for): that network's route contract from the measured
    # catalog (/api/tokens chains[].routeContract). Anything else is refused here; the sign layer checks the same against its own pinned list.
    rc = ((TK.chain_info(r["fromChain"]) or {}).get("routeContract") or "").lower()
    if not rc:
        raise Refuse("chain_unverified", 409, chainId=r["fromChain"])
    to = str(tx.get("to") or "").lower()
    if to != rc or (approval and approval != rc):
        raise Refuse("unverified_contract", 409, chainId=r["fromChain"])
    if tx.get("chainId") is not None and int(str(tx["chainId"]), 0) != int(r["fromChain"]):
        raise Refuse("unverified_contract", 409, chainId=r["fromChain"], why="chainId")
    nf = int(((fees or {}).get("nativeFee") or {}).get("raw") or 0)
    val = int(str(tx.get("value") or "0x0"), 16) if str(tx.get("value") or "0x0").startswith("0x") else int(str(tx.get("value") or 0))
    if val > (r["amount"] if r["fromToken"] == C.NATIVE else 0) + nf:
        raise Refuse("unverified_contract", 409, chainId=r["fromChain"], why="value")
    seq = []
    url = C.OTHER_RPC.get(r["fromChain"]) or ((TK.chain_info(r["fromChain"]) or {}).get("rpc") or [None])[0]   # any Pay-from network
    if r["fromToken"] != C.NATIVE and approval:
        allow = None
        urls = list(dict.fromkeys([x for x in [url] + list((TK.chain_info(r["fromChain"]) or {}).get("rpc") or []) if x]))[:3]
        for uu in urls:                    # every public RPC of the network once: an unread allowance is the exception (audit v5 minor 11)
            try:
                allow = u(rpc("eth_call", [{"to": r["fromToken"], "data": allowance_data(r["fromAddress"], approval)}, "latest"], url=uu)[:66])
                break
            except Exception:
                allow = None
        if allow is None or allow < r["amount"]:
            # USDT-style tokens revert approve(x) while the allowance is non-zero (audit v4 M20): approve(0) to the same spender first —
            # also when the allowance could not be read at all (approve(0) is always safe; never silently skipped)
            if (allow is None or allow > 0) and (r["fromChain"], r["fromToken"]) in C.APPROVE_RESET_FIRST:
                seq.append({"kind": "approve-reset", "to": r["fromToken"], "value": "0x0", "data": approve_data(approval, 0)})
            seq.append({"kind": "approve", "to": r["fromToken"], "value": "0x0", "data": approve_data(approval, r["amount"])})
    seq.append({"kind": "swap", "to": tx["to"].lower(), "value": tx.get("value") or "0x0", "data": tx["data"], "gasLimit": tx.get("gasLimit")})
    out["sequence"] = seq
    return out


def status(tx_hash: str, from_chain: int, to_chain: int, venue: str | None) -> dict:
    if from_chain == C.CHAIN and to_chain == C.CHAIN:
        rc = rpc_any("eth_getTransactionReceipt", [tx_hash])
        if not rc:
            return {"status": "PENDING", "txHash": tx_hash}
        return {"status": "DONE" if rc.get("status") == "0x1" else "FAILED", "txHash": tx_hash, "block": u(rc.get("blockNumber"))}
    bridge = V.tool_key_of(venue) if venue else None
    try:
        j = router.status(tx_hash, from_chain, to_chain, bridge)
    except router.Budget as e:
        return {"status": "UNKNOWN", "stale": True, "retryInS": e.reset_in}
    except router.Upstream as e:
        if e.code == 404:
            return {"status": "NOT_FOUND", "txHash": tx_hash}
        return {"status": "UNKNOWN", "stale": True}
    def side(x):
        x = x or {}
        return {"txHash": x.get("txHash"), "chainId": x.get("chainId"), "amount": x.get("amount"),
                "token": {"address": (x.get("token") or {}).get("address"), "symbol": (x.get("token") or {}).get("symbol"),
                          "decimals": (x.get("token") or {}).get("decimals")} if x.get("token") else None, "timestamp": x.get("timestamp")}
    return V.sanitize({"status": j.get("status") or "UNKNOWN", "substatus": j.get("substatus"), "substatusMessage": j.get("substatusMessage"),
                       "venue": V.tool(j.get("tool")) if j.get("tool") else None, "sending": side(j.get("sending")),
                       "receiving": side(j.get("receiving"))})


# ------------------------------------------------------------------ direct pool: Uniswap V3 / V4 called from the user's wallet
def _permit2_allowance(user: str, token: str, spender: str) -> tuple[int, int]:
    r = rpc_any("eth_call", [{"to": C.PERMIT2, "data": "0x927da105" + pad(user) + pad(token) + pad(spender)}, "latest"])
    w = [int(r[2 + 64 * i: 66 + 64 * i], 16) for i in range(3)]
    return w[0], w[1]


def build_direct(body: dict, rec: dict | None) -> dict:
    """[approve(s)…, swap] for the best direct Uniswap V3/V4 pool of the pair (never Aero / Ramses / Metric), same shapes as a route build (sequence + batch7702).
    V3: approve(SwapRouter02, amount) + SwapRouter02.exactInputSingle (0x04e45aaf). V4: approve(Permit2, amount) + Permit2.approve(token,
    UniversalRouter, amount, 30 min) + UniversalRouter.execute(V4_SWAP: SWAP_EXACT_IN_SINGLE · SETTLE_ALL · TAKE_ALL). minOut in the
    transaction = the user's minOut (their slippage); without minOut fresh simulated out x (1 - 0.5 %). Refused only when the fresh output or
    the simulation is under it."""
    import singlepool as SPL
    q = dict(rec["r"]) if rec else {}
    for k in ("fromChain", "fromToken", "toChain", "toToken", "amount", "fromDecimals", "toDecimals"):
        if body.get(k) not in (None, ""):
            q[k] = body[k]
    q["fromAddress"] = body.get("fromAddress")
    if not q.get("fromAddress"):
        raise Refuse("fromAddress required", 400)
    r = Q.normalize({k: (str(v) if k == "amount" else v) for k, v in q.items() if k not in ("fromMeta", "toMeta")})
    if r["fromChain"] != C.CHAIN:
        raise Refuse("direct_same_chain_only", 400)
    if r["fromToken"] == C.NATIVE or r["toToken"] == C.NATIVE:
        raise Refuse("direct_erc20_only", 400)
    try:
        min_out = int(str(body.get("minOut") or "0"))
    except ValueError:
        raise Refuse("minOut must be a raw integer", 400)
    mode = body.get("mode") or "auto"
    user, ft, tt, amt = r["fromAddress"], r["fromToken"], r["toToken"], r["amount"]
    tdec = r["toDecimals"] or 18
    rd = (rec or {}).get("direct") or {}
    out_note = None
    if min_out > 0 and rd.get("pool") and rd.get("dex") in Q.DIRECT_DEXES and (rd.get("feeTier") or rd.get("dex") == "uniswap-v4"):
        # premium-2: the quote already picked the pool; scanning every pool again cost ~6 s per build. The pool the person saw is used
        # as is and the simulation below guards their minimum (refused under it).
        base, expected = dict(rd), None
    else:
        try:
            base = SPL.best_uniswap(ft, tt, amt, "latest", tdec, r["fromDecimals"])      # direct = best Uniswap V3/V4 pool only 
        except Exception:
            raise Refuse("chain_unavailable", 503)
        if not (base.get("available") and base.get("dex") in Q.DIRECT_DEXES):
            raise Refuse("no_direct_pool", 409, base=V.sanitize({k: base.get(k) for k in ("kind", "dex", "pool", "reason")}))
        if rd and rd.get("pool") != base["pool"]:
            out_note = "deepest pool changed since the quote"
        expected = int(base["out"]["raw"])
    # P0 17.11: the swap's minimum IS the minimum the person accepted (minOut = the quote's out.min,
    # their slippage). Deriving it from the fresh pool (fresh x 0.995) and refusing when that lands under minOut failed on any 1-wei drift
    # down since the quote. Refuse only when the fresh output itself is under minOut. No minOut (API use) -> fresh x (1 - default slippage).
    tx_min = min_out if min_out > 0 else expected * (10_000 - int(C.SLIPPAGE * 10_000)) // 10_000
    if expected is not None and expected < tx_min:
        raise Refuse("below_min", 409, simulatedOut=str(expected), minOut=str(tx_min))
    pre, spender = [], None
    try:
        if base["dex"] == "uniswap-v3":
            spender = C.SWAP_ROUTER02
            allow = u(rpc_any("eth_call", [{"to": ft, "data": allowance_data(user, spender)}, "latest"])[:66])
            if allow < amt:
                pre.append({"kind": "approve", "to": ft, "value": "0x0", "data": approve_data(spender, amt)})
            swap = {"kind": "swap", "to": C.SWAP_ROUTER02, "value": "0x0",
                    "data": "0x04e45aaf" + pad(ft) + pad(tt) + pad(int(base["feeTier"])) + pad(user) + pad(amt) + pad(tx_min) + pad(0)}
        else:
            key = base.get("v4Key") or SPL.v4_key(base["pool"])
            if not key:
                raise Refuse("no_direct_pool", 409, reason="pool key not found")
            allow = u(rpc_any("eth_call", [{"to": ft, "data": allowance_data(user, C.PERMIT2)}, "latest"])[:66])
            if allow < amt:
                pre.append({"kind": "approve", "to": ft, "value": "0x0", "data": approve_data(C.PERMIT2, amt)})
            p2amt, p2exp = _permit2_allowance(user, ft, C.UNIVERSAL_ROUTER)
            if p2amt < amt or p2exp < time.time() + 120:
                pre.append({"kind": "approve-permit2", "to": C.PERMIT2, "value": "0x0",
                            "data": "0x87517c45" + pad(ft) + pad(C.UNIVERSAL_ROUTER) + pad(amt) + pad(int(time.time()) + 1800)})
            zf = key["currency0"] == ft
            data = SPL._ur_v4_single(key, zf, amt, int(time.time()) + 600)
            # the encoder puts minOut 0 into the swap and TAKE_ALL: write tx_min into both (amountOutMinimum and TAKE_ALL minimum)
            data = _v4_with_min(key, zf, amt, tx_min)
            swap = {"kind": "swap", "to": C.UNIVERSAL_ROUTER, "value": "0x0", "data": data}
    except RpcError:
        raise Refuse("chain_unavailable", 503)
    out = {"ok": True, "kind": "direct", "venue": {**V.dex(base["dex"]), "displayKey": base["dex"], "kind": "dex"}, "delivery": "onchain",
           "chainId": C.CHAIN, "from": user, "pool": {"id": base["pool"], "pair": base.get("pair"), "feeTier": base.get("feeTier"),
                                                     "feePct": base.get("feePct"), "liqUsd": base.get("liqUsd")},
           "fees": {"ours": {"pct": 0, "usd": 0.0}, "route": {"pct": 0, "usd": 0.0, "items": []}, "bridge": [], "venue": [], "totalUsd": 0.0},
           "quoted": {"raw": str(expected if expected is not None else tx_min), "decimals": tdec}, "minOutTx": {"raw": str(tx_min), "decimals": tdec},
           "minOutUser": {"raw": str(min_out), "decimals": tdec}, "builtAt": int(time.time()), "expiresInS": 60, "warnings": []}
    if out_note:
        out["warnings"].append(out_note)
    # balance
    bal = u(multicall([(ft, balance_of_data(user))])[0][1][:66])
    ov = {}
    if bal < amt:
        out["warnings"].append("insufficient_balance")
        out["insufficientBalance"] = True
        from chain import balance_override
        for k, v in (balance_override(ft, user, amt) or {}).items():
            ov.setdefault(k, {}).update(v)
    else:
        out["insufficientBalance"] = False
    txs = pre + [swap]
    res = _sim([{"from": user, "to": t["to"], "data": t["data"], "value": t["value"], "gas": hex(8_000_000)} for t in txs], ov, False)
    sc = res["calls"][-1]
    got = _received(sc, r, user)
    sim = {"block": res["block"], "status": "ok" if sc.get("status") == "0x1" and all(c.get("status") == "0x1" for c in res["calls"]) else "reverted",
           "gasUsed": u(sc.get("gasUsed")), "error": (sc.get("error") or {}).get("message") if sc.get("status") != "0x1" else None,
           "out": {"raw": str(got), "decimals": tdec, "usd": M.usd(got, tdec, tt)}}
    if sim["status"] != "ok":
        raise Refuse("simulation_reverted", 409, simulation=sim)
    if got < tx_min:
        raise Refuse("below_min", 409, simulatedOut=str(got), minOut=str(tx_min))
    if expected is None:
        out["quoted"] = {"raw": str(got), "decimals": tdec}      # the fresh output = the simulation of this very transaction
    out["simulation"] = sim
    out["split"] = [{**V.dex(base["dex"]), "pool": base["pool"], "pair": base.get("pair"), "amount": str(got), "shareBps": 10_000, "sharePct": 100.0}]
    out["gas"] = {"used": sum(u(c.get("gasUsed")) for c in res["calls"]), "usd": Q._gas_usd(sum(u(c.get("gasUsed")) for c in res["calls"]))}
    out["fees"]["gas"] = {"usd": out["gas"]["usd"], "limit": int(out["gas"]["used"] * 13 // 10)}
    out["sequence"] = [{**t, "gasLimit": hex(int(u(c.get("gasUsed")) * 13 // 10) + 21_000)} for t, c in zip(txs, res["calls"])]
    code = rpc_any("eth_getCode", [user, "latest"]) or "0x"
    designator = "0xef0100" + C.SIMPLE7702[2:]
    if mode in ("auto", "7702") and (code in ("0x", "") or code.lower() == designator):
        data = enc_execute_batch([(t["to"], 0, t["data"]) for t in txs])
        ov2 = {k: dict(v) for k, v in ov.items()}
        ov2.setdefault(user, {})["code"] = designator
        r2 = _sim([{"from": user, "to": user, "data": data, "value": "0x0", "gas": hex(10_000_000)}], ov2, False)
        c2 = r2["calls"][0]
        got2 = _received(c2, r, user)
        ok2 = c2.get("status") == "0x1" and got2 >= tx_min
        out["batch7702"] = {"available": ok2, "to": user, "value": "0x0", "data": data,
                            "gasLimit": hex(int(u(c2.get("gasUsed")) * 13 // 10) + 30_000) if ok2 else None,
                            "authorization": {"required": code.lower() != designator, "contractAddress": C.SIMPLE7702, "chainId": C.CHAIN},
                            "simulatedOut": {"raw": str(got2), "decimals": tdec}, "calls": len(txs), "block": r2["block"]}
    elif mode in ("auto", "7702"):
        out["batch7702"] = {"available": False, "reason": "account already has code (delegated elsewhere or a contract)"}
    return V.sanitize(out)


def _v4_with_min(key: dict, zf: bool, amount: int, min_out: int) -> str:
    """UniversalRouter V4_SWAP exact-in-single with amountOutMinimum = min_out and TAKE_ALL minimum = min_out (same encoding as the launchpad swap code)."""
    import singlepool as SPL
    kw = "".join(pad(x) for x in (key["currency0"], key["currency1"], key["fee"], key["tickSpacing"], key["hooks"]))
    swap = pad(32) + kw + pad(1 if zf else 0) + pad(amount) + pad(min_out) + pad(0) + pad(320) + pad(0)
    cin, cout = (key["currency0"], key["currency1"]) if zf else (key["currency1"], key["currency0"])
    plan = pad(64) + pad(64 + len(SPL._bytes("060c0f")) // 2) + SPL._bytes("060c0f") + SPL._bytes_arr([swap, pad(cin) + pad(amount), pad(cout) + pad(min_out)])
    return ("0x3593564c" + pad(96) + pad(96 + len(SPL._bytes("10")) // 2) + pad(int(time.time()) + 600) + SPL._bytes("10")
            + SPL._bytes_arr(["0x" + plan]))
