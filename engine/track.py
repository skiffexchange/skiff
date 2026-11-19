"""GET /api/track — the live route map after Swap (NEW-PITCH 16.11). A node lights up only on a real confirmation: a receipt, a block time,
the route provider's status for the bridge leg. Nothing is estimated or animated ahead of the chain.

4663 -> 4663 (no bridge):   source (the swap transaction: receipt, block, block time, explorer)
                            -> split (shares per dex from the Swap / Transfer logs of THIS transaction — split.decode, the same decoder the
                               quote uses on its simulation) -> wallet (what the receiver got, from the Transfer logs).
                            Works for any swap transaction on 4663 (e.g. a big trade from /api/trades): the receiver is the sender when it
                            received a token, else the called contract, else the non-pool address that received the most of a stock token.
X -> 4663 (cross-chain):    source (the transaction on X: provider status `sending`, else a receipt read on X's public RPC)
                            -> bridge (provider status: tool shown by its display name only — own tools neutral)
                            -> chain (the receiving transaction on 4663: our own receipt read, block, block time)
                            -> split (shares from the receiving transaction's logs) -> wallet (receiving amount).
Finished answers (DONE / FAILED) are kept 1 h in memory: polling a settled swap costs nothing. Explorer links: Robinhood Chain ->
robinhoodchain.blockscout.com; other networks -> the explorer of that Pay-from network (tokens.pay_chains)."""
from __future__ import annotations

import re
import threading
import time

import config as C
import market as M
import router
import split as SP
import tokens as TK
import venues as V
from chain import RpcError, rpc, rpc_any, u, word_addr

HASH = re.compile(r"0x[0-9a-f]{64}")
RH_EXPLORER = "https://robinhoodchain.blockscout.com"
_done: dict[str, tuple[float, dict]] = {}
_bts: dict[tuple[int, int], int] = {}
_lock = threading.Lock()
DONE_TTL_S = 3600
_pending: dict[str, tuple[float, dict]] = {}
PENDING_TTL_S = 6


class Bad(Exception):
    pass


def _chain_pub(cid: int) -> dict:
    c = TK.chain_info(cid) or {}
    if cid == C.CHAIN:
        return {"id": C.CHAIN, "name": "Robinhood Chain", "logo": None}
    return {"id": cid, "name": c.get("name") or f"Chain {cid}", "logo": c.get("logo")}


def _explorer(cid: int, h: str | None) -> str | None:
    if not h:
        return None
    if cid == C.CHAIN:
        return f"{RH_EXPLORER}/tx/{h}"
    c = TK.chain_info(cid)
    return f"{c['explorer'].rstrip('/')}/tx/{h}" if c and c.get("explorer") else None


def _block_ts(cid: int, n: int, url: str | None = None) -> int | None:
    k = (cid, n)
    with _lock:
        if k in _bts:
            return _bts[k]
    try:
        b = rpc_any("eth_getBlockByNumber", [hex(n), False]) if cid == C.CHAIN else rpc("eth_getBlockByNumber", [hex(n), False], url=url, retries=2)
        ts = u(b["timestamp"]) if b else None
    except Exception:
        return None
    if ts:
        with _lock:
            _bts[k] = ts
            if len(_bts) > 5000:
                _bts.clear()
    return ts


def _meta(token: str) -> tuple[str | None, int | None]:
    m = TK.find(C.CHAIN, token) or {}
    if m.get("decimals") is not None:
        return m.get("symbol"), int(m["decimals"])
    try:
        import tokenmeta
        d = tokenmeta.lookup(token)
        return d.get("symbol"), int(d.get("decimals"))
    except Exception:
        return None, None


def _amount(raw: int | None, token: str | None, symbol=None, decimals=None) -> dict | None:
    if raw is None or not token:
        return None
    if decimals is None or symbol is None:
        ms, md = _meta(token)
        symbol = symbol if symbol is not None else ms
        decimals = decimals if decimals is not None else md
    usd = M.usd(raw, decimals, token) if decimals is not None and M.price(token) else None
    return {"raw": str(raw), "decimals": decimals, "usd": round(usd, 2) if usd is not None else None, "symbol": symbol, "token": token}


def flows(logs: list[dict]) -> dict[str, dict[str, int]]:
    """address -> token -> net raw amount (+ in, - out) over the ERC-20 Transfer logs of one transaction"""
    f: dict[str, dict[str, int]] = {}
    for l in logs or []:
        tp = l.get("topics") or []
        if len(tp) != 3 or tp[0] != C.TOPIC_TRANSFER:
            continue
        tok = l["address"].lower()
        amt = u((l.get("data") or "0x")[:66])
        frm, to = word_addr(tp[1]), word_addr(tp[2])
        f.setdefault(frm, {})[tok] = f.get(frm, {}).get(tok, 0) - amt
        f.setdefault(to, {})[tok] = f.get(to, {}).get(tok, 0) + amt
    return f


def _is_pool(a: str, swappers: set | None = None) -> bool:
    return bool(M.pool(a)) or a in (C.POOL_MANAGER, C.NATIVE) or bool(swappers and a in swappers)


def who_and_what(logs: list[dict], tx_from: str, tx_to: str | None, to_token: str | None = None, to_address: str | None = None):
    """(receiver, bought token, received raw, sent token, sent raw) of a swap transaction, from its Transfer logs only. Pools = our pool
    index + every address that emitted a Swap event in this transaction (pools we do not index) + the V4 PoolManager."""
    f = flows(logs)
    swappers = {l["address"].lower() for l in logs if (l.get("topics") or [None])[0] in (C.TOPIC_V3_SWAP, C.TOPIC_V2_SWAP)}

    def best_in(addr, want=None):
        ins = {t: v for t, v in (f.get(addr) or {}).items() if v > 0 and (want is None or t == want)}
        if not ins:
            return None, 0
        stock = {t: v for t, v in ins.items() if t in M.STOCK_BY_ADDR}
        pool = stock or ins
        t = max(pool, key=lambda k: pool[k])
        return t, pool[t]

    # what the pools paid out, per token: a receiver must get at least half of it. A dust refund to the sender (148 wei of GLD left over by
    # a liquidity zap, 0xcea5…597f) or a fee skimmed to a third address is not "the wallet got the stock". No pool recognised -> no bar.
    pool_out: dict[str, int] = {}
    for a, toks in f.items():
        if _is_pool(a, swappers):
            for t, v in toks.items():
                if v < 0:
                    pool_out[t] = pool_out.get(t, 0) - v
    enough = lambda t, v: v * 2 >= pool_out.get(t, 0)

    cands = [a for a in (to_address, tx_from, tx_to) if a]
    who, tok, got = None, None, 0
    for a in cands:
        t, v = best_in(a.lower(), to_token)
        if t and enough(t, v):
            who, tok, got = a.lower(), t, v
            break
    if not who:                     # paid out to another address: the non-pool address that received the most (by USD; stocks first)
        best = None
        for a, toks in f.items():
            if _is_pool(a, swappers) or a == (tx_to or "").lower():
                continue
            for t, v in toks.items():
                if v <= 0 or (to_token and t != to_token) or not enough(t, v):
                    continue
                usd = M.usd(v, 18 if t in M.STOCK_BY_ADDR else (_meta(t)[1] or 18), t) if M.price(t) else 0
                key = (1 if t in M.STOCK_BY_ADDR else 0, usd or 0, v)
                if best is None or key > best[3]:
                    best = (a, t, v, key)
        if best:
            who, tok, got = best[:3]
    sent_tok, sent = None, 0
    src = (tx_from or "").lower()
    outs = {t: -v for t, v in (f.get(src) or {}).items() if v < 0}
    if not outs and who:
        outs = {t: -v for t, v in (f.get(who) or {}).items() if v < 0}
    if outs:
        sent_tok = max(outs, key=lambda k: outs[k])
        sent = outs[sent_tok]
    return who, tok, got, sent_tok, sent


# liquidity events: V3 Mint, V4 ModifyLiquidity, V2 Mint. A transaction that swaps and then deposits into a pool (a zap / LP rebalance)
# moves stock through pools but delivers it to no wallet — the map shows it, the landing must not present it as a user's swap.
LIQUIDITY_TOPICS = {"0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde",
                    "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec",
                    "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"}


def liquidity_tx(logs: list[dict]) -> bool:
    return any((l.get("topics") or [None])[0] in LIQUIDITY_TOPICS for l in logs or [])


def _split_nodes(rc: dict, at, blk, h, to_token=None, to_address=None, received=None, wallet_amount=None) -> tuple[dict, dict]:
    logs = rc.get("logs") or []
    who, tok, got, _st, _sv = who_and_what(logs, (rc.get("from") or "").lower(), (rc.get("to") or "").lower() or None, to_token, to_address)
    tok = tok or to_token
    got = got or (int(received) if received else 0)
    sp = SP.decode(logs, tok, got) if tok else {"split": [], "byDex": []}
    split = [{"dex": r["dex"], "name": r["name"], "logo": r["logo"], "sharePct": r["sharePct"], "shareBps": r["shareBps"]} for r in sp["byDex"]]
    pools = [{"dex": r["dex"], "name": r["name"], "logo": r["logo"], "pool": r["pool"], "pair": r["pair"], "sharePct": r["sharePct"],
              "shareBps": r["shareBps"]} for r in sp["split"]]
    n_split = {"kind": "split", "state": "done", "at": at, "block": blk, "txHash": h, "explorer": _explorer(C.CHAIN, h),
               "chain": _chain_pub(C.CHAIN), "bridge": None, "split": split, "pools": pools, "amount": None}
    n_wallet = {"kind": "wallet", "state": "done", "at": at, "block": blk, "txHash": h, "explorer": _explorer(C.CHAIN, h),
                "chain": _chain_pub(C.CHAIN), "bridge": None, "split": None, "address": who,
                "amount": wallet_amount or (_amount(got, tok) if got else None)}
    return n_split, n_wallet


def _node(kind, state="pending", **kw) -> dict:
    d = {"kind": kind, "state": state, "at": None, "block": None, "txHash": None, "explorer": None, "chain": None, "bridge": None,
         "split": None, "amount": None}
    d.update(kw)
    return d


def _feed_rec(h: str) -> tuple[str, str] | None:
    """(stock address, side) of the biggest row of this transaction in the /api/trades feed, or None"""
    try:
        import store as S
        r = S.q("SELECT stock, side FROM trades WHERE tx=? ORDER BY usd DESC LIMIT 1", (h,))
    except Exception:
        return None
    if not r:
        return None
    a = (M.STOCK_BY_SYM.get(r[0]["stock"]) or {}).get("address", "").lower()
    return (a, r[0]["side"]) if a else None


def _feed_hint(h: str) -> str | None:
    """a big trade from /api/trades: on a buy the bought token is that stock"""
    fr = _feed_rec(h)
    return fr[0] if fr and fr[1] == "buy" else None


def _same(h: str, to_token=None, to_address=None) -> dict:
    feed = _feed_rec(h)
    to_token = to_token or (feed[0] if feed and feed[1] == "buy" else None)
    try:
        rc = rpc_any("eth_getTransactionReceipt", [h])
    except RpcError:
        raise
    src = _node("source", txHash=h, explorer=_explorer(C.CHAIN, h), chain=_chain_pub(C.CHAIN))
    if not rc:
        try:
            seen = bool(rpc_any("eth_getTransactionByHash", [h]))
        except Exception:
            seen = False
        return {"status": "PENDING", "done": False, "seen": seen, "nodes": [src, _node("split", chain=_chain_pub(C.CHAIN)),
                                                                            _node("wallet", chain=_chain_pub(C.CHAIN))]}
    blk = u(rc.get("blockNumber"))
    at = _block_ts(C.CHAIN, blk)
    ok = rc.get("status") == "0x1"
    logs = rc.get("logs") or []
    who, tok, got, st, sv = who_and_what(logs, (rc.get("from") or "").lower(), (rc.get("to") or "").lower() or None, to_token, to_address)
    src.update(state="done" if ok else "failed", at=at, block=blk, amount=_amount(sv, st) if ok and st else None,
               address=(rc.get("from") or "").lower())
    if not ok:
        return {"status": "FAILED", "done": True, "nodes": [src, _node("split", "failed", chain=_chain_pub(C.CHAIN)),
                                                           _node("wallet", "failed", chain=_chain_pub(C.CHAIN))]}
    n_split, n_wallet = _split_nodes(rc, at, blk, h, to_token, to_address)
    # a plain swap: the sender paid one token and a wallet got the bought one. Arbitrage / multi-party transactions (a bot cycling through
    # pools) have no such pair — the map still shows the real logs, but the landing should rather pick a userSwap one.
    user_swap = bool(src.get("amount") and n_wallet.get("amount") and n_wallet.get("address"))
    if user_swap and feed:      # a feed trade: the pair found must be that trade (a buy got the stock, a sell paid it), not some other leg
        user_swap = (n_wallet["amount"].get("token") == feed[0]) if feed[1] == "buy" else (src["amount"].get("token") == feed[0])
    # premium-4: the receiver is a contract (a router / vault that keeps the stock), not a person's wallet -> no address under «wallet»
    # (a 7702-delegated EOA, code 0xef0100…, is still a wallet)
    if n_wallet.get("address") and _is_contract(n_wallet["address"]):
        n_wallet["address"], n_wallet["contract"] = None, True
        user_swap = False      # premium-5: a bot / vault contract keeps the stock -> not «a swap the way yours will be»: Live skips it
    doc = {"status": "DONE", "done": True, "userSwap": user_swap, "nodes": [src, n_split, n_wallet]}
    if not n_wallet.get("address") and not n_wallet.get("contract") and liquidity_tx(logs):
        doc["note"] = "liquidity"      # swapped into a pool position, no wallet received the bought token
    return doc


_codes: dict[str, bool] = {}


def _is_contract(addr: str) -> bool:
    a = addr.lower()
    if a not in _codes:
        try:
            code = rpc_any("eth_getCode", [a, "latest"]) or "0x"
        except Exception:
            return False
        if len(_codes) > 5000:
            _codes.clear()
        _codes[a] = len(code) > 2 and not code.lower().startswith("0xef0100")
    return _codes[a]


def _src_receipt(fc: int, h: str):
    c = TK.chain_info(fc)
    url = ((c or {}).get("rpc") or [None])[0]
    if not url:
        return None, None
    try:
        rc = rpc("eth_getTransactionReceipt", [h], url=url, retries=2, timeout=10)
    except Exception:
        return None, url
    return rc, url


def _cross(h: str, fc: int, tool_key: str | None, to_token=None, to_address=None) -> dict:
    src = _node("source", txHash=h, explorer=_explorer(fc, h), chain=_chain_pub(fc))
    brg = _node("bridge")
    arr = _node("chain", chain=_chain_pub(C.CHAIN))
    n_split = _node("split", chain=_chain_pub(C.CHAIN))
    n_wallet = _node("wallet", chain=_chain_pub(C.CHAIN))
    nodes = [src, brg, arr, n_split, n_wallet]
    j, stale = None, False
    try:
        j = router.status(h, fc, C.CHAIN, tool_key)
    except router.Budget:
        stale = True
    except router.Upstream as e:
        stale = e.code != 404
    j = j or {}
    status = j.get("status") or "NOT_FOUND"
    snd, rcv = j.get("sending") or {}, j.get("receiving") or {}
    if j.get("tool"):
        t = V.tool(j.get("tool"))
        brg["bridge"] = {"key": t["displayKey"], "name": t["name"], "logo": t["logo"]}
    # source: the provider saw the sending transaction, or its receipt on the source network says so
    if snd.get("txHash") or snd.get("timestamp"):
        src.update(state="done", at=snd.get("timestamp"), block=None)
        sa = snd.get("amount")
        stok = (snd.get("token") or {})
        if sa and stok:
            src["amount"] = {"raw": str(sa), "decimals": stok.get("decimals"), "symbol": stok.get("symbol"), "token": (stok.get("address") or "").lower(),
                             "usd": round(float(snd.get("amountUSD")), 2) if snd.get("amountUSD") else None}
    else:
        rc, url = _src_receipt(fc, h)
        if rc:
            blk = u(rc.get("blockNumber"))
            src.update(state="done" if rc.get("status") == "0x1" else "failed", block=blk, at=_block_ts(fc, blk, url))
    if src["state"] == "failed":
        for n in nodes[1:]:
            n["state"] = "failed"
        return {"status": "FAILED", "done": True, "nodes": nodes, "stale": stale}
    if status == "FAILED":
        brg["state"] = "failed"
        for n in nodes[2:]:
            n["state"] = "failed"
        return {"status": "FAILED", "done": True, "nodes": nodes, "substatus": j.get("substatus"), "stale": stale}
    rh = (rcv.get("txHash") or "").lower()
    if rh and int(rcv.get("chainId") or C.CHAIN) == C.CHAIN and HASH.fullmatch(rh):
        brg.update(state="done", at=rcv.get("timestamp"))
        try:
            rc = rpc_any("eth_getTransactionReceipt", [rh])
        except Exception:
            rc = None
        arr.update(txHash=rh, explorer=_explorer(C.CHAIN, rh))
        if rc:
            blk = u(rc.get("blockNumber"))
            at = _block_ts(C.CHAIN, blk)
            ok = rc.get("status") == "0x1"
            arr.update(state="done" if ok else "failed", at=at, block=blk)
            if ok:
                rtok = (rcv.get("token") or {})
                tok = (rtok.get("address") or to_token or "").lower() or None
                wa = None
                if rcv.get("amount") and tok:
                    wa = _amount(int(rcv["amount"]), tok, rtok.get("symbol"), rtok.get("decimals"))
                ns, nw = _split_nodes(rc, at, blk, rh, tok, (j.get("toAddress") or to_address or "").lower() or None, rcv.get("amount"), wa)
                nodes[3], nodes[4] = ns, nw
                return {"status": "DONE" if status == "DONE" else "PENDING", "done": status == "DONE", "nodes": nodes,
                        "durationS": (at - src["at"]) if (at and src.get("at")) else None, "stale": stale}
            for n in nodes[3:]:
                n["state"] = "failed"
            return {"status": "FAILED", "done": True, "nodes": nodes, "stale": stale}
    return {"status": "PENDING", "done": False, "nodes": nodes, "substatus": j.get("substatus") if j else None, "stale": stale}


def track(q: dict) -> dict:
    h = str(q.get("txHash") or "").strip().lower()
    if not HASH.fullmatch(h):
        raise Bad("txHash must be a 0x transaction hash (64 hex)")
    try:
        fc = int(q.get("fromChain") or C.CHAIN)
        tc = int(q.get("toChain") or C.CHAIN)
    except ValueError:
        raise Bad("fromChain / toChain must be integers")
    if tc != C.CHAIN:
        raise Bad("toChain must be 4663")
    if fc != C.CHAIN and not TK.chain_info(fc):
        raise Bad("fromChain is not a Pay-from network")
    to_token = str(q.get("toToken") or "").lower() or None
    to_address = str(q.get("toAddress") or "").lower() or None
    for x in (to_token, to_address):
        if x and not re.fullmatch(r"0x[0-9a-f]{40}", x):
            raise Bad("toToken / toAddress must be 0x addresses")
    rid = str(q.get("routeId") or "")
    tool = None
    if rid:
        if not re.fullmatch(r"[0-9a-z-]{1,48}", rid):
            raise Bad("bad routeId")
        import quote as Q
        rec = Q.route_record(rid)
        if rec:
            to_token = to_token or rec["r"].get("toToken")
            to_address = to_address or rec["r"].get("toAddress") or rec["r"].get("fromAddress")
            tool = rec.get("tool") if rec.get("tool") not in ("direct",) else None
    key = f"{h}:{fc}:{to_token or ''}:{to_address or ''}"
    now = time.time()
    with _lock:
        hit = _done.get(key)
    if hit and now - hit[0] < DONE_TTL_S:
        return {**hit[1], "cached": True}
    if fc != C.CHAIN:       # audit v4 M21: a pending cross-chain answer is reused for PENDING_TTL_S — polls never fan out to the provider
        with _lock:
            ph = _pending.get(key)
        if ph and now - ph[0] < PENDING_TTL_S:
            return {**ph[1], "cached": True}
    doc = _same(h, to_token, to_address) if fc == C.CHAIN else _cross(h, fc, tool, to_token, to_address)
    doc.update({"txHash": h, "fromChain": fc, "toChain": tc, "at": int(now), "pollS": 1.5 if fc == C.CHAIN else 5})
    if fc != C.CHAIN and not doc.get("done"):
        with _lock:
            _pending[key] = (now, doc)
            if len(_pending) > 2000:
                _pending.clear()
    if doc.get("done") and not doc.get("stale"):
        with _lock:
            _pending.pop(key, None)
            _done[key] = (now, doc)
            if len(_done) > 2000:
                for k in sorted(_done, key=lambda k: _done[k][0])[:500]:
                    _done.pop(k, None)
    return V.sanitize(doc)
