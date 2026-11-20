"""Robinhood stock tokens, found on the chain instead of a hand list (ANT and the new stock tokens, 20.11).

Genuine = all three, read from the chain (checked 20.11 on all 213 factory tokens):
  1. Robinhood's token factory FACTORY emitted Deployed(bytes32 indexed id, address token, string name, string symbol) for it;
  2. its runtime code hashes to PROXY_CODEHASH (the factory's BeaconProxy) and its ERC-1967 beacon slot holds Robinhood's BEACON;
  3. its name (read now: Robinhood renames tokens, e.g. PEACH_DEFI_1 -> «Anthropic, PBC Class A Common Stock • Robinhood Token») ends
     with SUFFIX.
A same-ticker token from anyone else fails 1 and 2: it is a fake (clash), whatever DexScreener says about its liquidity.

Listed (in market.STOCKS, so in the picker, ⌘K, /stocks, /stock/<T>, prices, history, stocks-overview, portfolio): genuine AND its deepest
pool against USDG / ETH / WETH holds at least LIST_MIN_LIQ_USD (= the engine's pool floor) on DexScreener. A genuine token below that is
tracked, not shown («no pool yet»); it is listed by itself once a pool reaches the floor. A listed token stays listed.

Runs in the engine: every SCAN_S the factory's new Deployed logs (one address + one topic: 10M blocks a call), every LIQ_S the liquidity of
the genuine tokens not listed yet (DexScreener tokens/v1, 30 addresses a call; a candidate is then read pair by pair). State in
DATA/stockscan.json; first start from the shipped stockscan-snapshot.json (213 tokens up to block 118,969,020, 20.11), so a new process
reads only the blocks after it."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import chain
import config as C
from keccak import keccak256

log = logging.getLogger("rhdex.stockscan")

FACTORY = "0x4783c67b63de2b358ac5951a7d41f47a38f3c046"
BEACON = "0xe10b6f6b275de231345c20d14ab812db62151b00"
PROXY_CODEHASH = "0x6c1fdd40002dcb440c7fff6a84171404d279ccb057803b65826f7546acd65630"
BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"      # ERC-1967
DEPLOYED = "0xd9b0c6a1c0de228715ad0fa09f3259686ee84f8cc675e03ef7e47a9cdafa76d6"         # keccak("Deployed(bytes32,address,string,string)")
SUFFIX = " • Robinhood Token"
WINDOW = 10_000_000
SCAN_S = 600
LIQ_S = 3600
LIST_MIN_LIQ_USD = C.POOL_MIN_LIQ_USD
QUOTES = {C.USDG, C.WETH, C.NATIVE}
FILE = os.path.join(C.DATA, "stockscan.json")
SNAPSHOT = os.path.join(C.HERE, "stockscan-snapshot.json")
STATE = {"scannedTo": 0, "genuine": 0, "listed": 0, "added": [], "err": "", "scanAt": 0, "liqAt": 0}
_lock = threading.Lock()
_doc: dict = {"to": 0, "tokens": {}, "listed": {}}
# display names the chain's own name does not give well (the chain says «Anthropic, PBC Class A Common Stock»)
NAMES = {"ANT": "Anthropic"}


def _load() -> None:
    for p in (FILE, SNAPSHOT):
        try:
            d = json.load(open(p))
            with _lock:
                _doc.update(to=int(d.get("to") or 0), tokens=d.get("tokens") or {}, listed=d.get("listed") or {})
            return
        except (OSError, ValueError):
            continue


def _save() -> None:
    try:
        tmp = FILE + ".tmp"
        with _lock:
            json.dump(_doc, open(tmp, "w"), indent=0)
        os.replace(tmp, FILE)
    except OSError as e:
        log.warning("stockscan save: %s", e)


def genuine(addr: str) -> bool:
    """a Robinhood stock token by all three checks (listed or not)"""
    t = _doc["tokens"].get((addr or "").lower())
    return bool(t and t.get("genuine"))


def listed_extra() -> list[dict]:
    """stock rows added by this module (market loads them next to stocks.json at start)"""
    if not _doc["tokens"] and not _doc["listed"]:
        _load()
    return list(_doc["listed"].values())


def by_symbol(sym: str) -> dict | None:
    """the genuine token with this ticker (listed or not), for the fake warning"""
    s = (sym or "").upper()
    for a, t in _doc["tokens"].items():
        if t.get("genuine") and (t.get("symbol") or "").upper() == s:
            return {"address": a, **t}
    return None


# ------------------------------------------------------------------ chain reads
MAX_STR = 200          # a name / ticker longer than this is cut (a malformed answer never stalls the scan)


def _text(ret: str | None) -> str | None:
    try:
        d = ret[2:]
        off = int(d[:64], 16)
        n = min(int(d[off * 2:off * 2 + 64], 16), MAX_STR)
        return bytes.fromhex(d[off * 2 + 64:off * 2 + 64 + n * 2]).decode("utf-8", "replace")
    except Exception:
        return None


def _str_at(d: str, off: int) -> str:
    try:
        n = min(int(d[off * 2:off * 2 + 64], 16), MAX_STR)
        return bytes.fromhex(d[off * 2 + 64:off * 2 + 64 + n * 2]).decode("utf-8", "replace")
    except Exception:
        return ""


def verify(addr: str) -> dict:
    """{genuine, why, symbol, name}: codehash + beacon slot + the name suffix, read now"""
    code = chain.rpc_any("eth_getCode", [addr, "latest"]) or "0x"
    if "0x" + keccak256(bytes.fromhex(code[2:])).hex() != PROXY_CODEHASH:
        return {"genuine": False, "why": "codehash"}
    slot = chain.rpc_any("eth_getStorageAt", [addr, BEACON_SLOT, "latest"]) or "0x"
    if "0x" + slot[-40:].lower() != BEACON:
        return {"genuine": False, "why": "beacon"}
    name = _text(chain.rpc_any("eth_call", [{"to": addr, "data": "0x" + chain.sel("name()")}, "latest"]))
    sym = _text(chain.rpc_any("eth_call", [{"to": addr, "data": "0x" + chain.sel("symbol()")}, "latest"]))
    if not name or not name.endswith(SUFFIX) or not sym:
        return {"genuine": False, "why": "name", "name": name, "symbol": sym}
    return {"genuine": True, "why": None, "symbol": sym.strip(), "name": name}


def scan() -> int:
    """the factory's Deployed logs after the last scanned block; every new token verified. -> new tokens"""
    for a in [a for a, t in _doc["tokens"].items() if t.get("genuine") is None]:   # the ones the node did not answer for last time
        try:
            v = verify(a)
        except (chain.RpcError, OSError):
            break
        with _lock:
            _doc["tokens"][a].update(genuine=v["genuine"], why=v["why"], **({"symbol": v["symbol"], "name": v["name"]} if v["genuine"] else {}))
    head = chain.block_number()
    frm = _doc["to"] + 1
    new = 0
    while frm <= head:
        to = min(frm + WINDOW - 1, head)
        for lg in chain.rpc("eth_getLogs", [{"address": FACTORY, "fromBlock": hex(frm), "toBlock": hex(to), "topics": [DEPLOYED]}],
                            timeout=40) or []:
            d = (lg.get("data") or "0x")[2:]
            a = "0x" + d[24:64].lower()
            if a in _doc["tokens"]:
                continue
            try:
                v = verify(a)
            except (chain.RpcError, OSError) as e:           # the node failed: kept as unverified, verified again next round (never stalls)
                log.warning("stockscan verify %s: %s", a, e)
                v = {"genuine": None, "why": "unverified"}
            try:
                no, so = int(d[64:128], 16), int(d[128:192], 16)
            except ValueError:
                no = so = 0
            with _lock:
                _doc["tokens"][a] = {"symbol": v.get("symbol") or _str_at(d, so), "name": v.get("name") or _str_at(d, no),
                                     "block": chain.u(lg["blockNumber"]), "genuine": v["genuine"], "why": v["why"]}
            new += 1
            log.info("stockscan: factory token %s %s genuine=%s", _doc["tokens"][a]["symbol"], a, v["genuine"])
        with _lock:
            _doc["to"] = to
        frm = to + 1
    STATE.update(scannedTo=_doc["to"], scanAt=int(time.time()), genuine=sum(1 for t in _doc["tokens"].values() if t.get("genuine")))
    _save()
    return new


LEGAL = re.compile(r",?\s+(?:Class\s+[A-Z]\b.*|common stock.*|Common Stock.*|Inc\.?|Incorporated|Corporation|Corp\.?|Holdings?|plc|PLC|"
                   r"Limited|Ltd\.?|N\.V\.|S\.A\.|PBC|Group,? Inc\.?)\s*$")


def short_name(sym: str, name: str) -> str:
    if sym in NAMES:
        return NAMES[sym]
    n = name[: -len(SUFFIX)] if name.endswith(SUFFIX) else name
    for _ in range(4):
        m = LEGAL.sub("", n).strip().rstrip(",")
        if m == n or not m:
            break
        n = m
    return n or sym


def kind_of(name: str) -> str:
    return "etf" if re.search(r"\b(ETF|Fund|Trust|Exchange-Traded)\b", name) else "stock"


def _deepest(addr: str) -> float:
    """deepest pool of a token against USDG / ETH / WETH on DexScreener, $ (0 when none)"""
    code, j, _ = chain.http_json(f"https://api.dexscreener.com/token-pairs/v1/robinhood/{addr}", timeout=15)
    if code != 200 or not isinstance(j, list):
        raise OSError(f"dexscreener {code}")
    best = 0.0
    for p in j:
        b, q = p["baseToken"]["address"].lower(), p["quoteToken"]["address"].lower()
        other = q if b == addr else b
        if other in QUOTES:
            best = max(best, float((p.get("liquidity") or {}).get("usd") or 0))
    return best


def check_liquidity() -> list[str]:
    """list the genuine tokens whose pool reached the floor. -> tickers added now"""
    import market as M
    cands = [a for a, t in _doc["tokens"].items() if t.get("genuine") and a not in M.STOCK_BY_ADDR]
    top: dict[str, float] = {}
    for i in range(0, len(cands), 30):                       # one call per 30 tokens: their main pair
        part = cands[i:i + 30]
        code, j, _ = chain.http_json("https://api.dexscreener.com/tokens/v1/robinhood/" + ",".join(part), timeout=20)
        if code != 200 or not isinstance(j, list):
            raise OSError(f"dexscreener {code}")
        for p in j:
            for side in ("baseToken", "quoteToken"):
                a = p[side]["address"].lower()
                if a in part:
                    top[a] = max(top.get(a, 0.0), float((p.get("liquidity") or {}).get("usd") or 0))
        time.sleep(0.5)
    added = []
    for a in [a for a, v in top.items() if v >= LIST_MIN_LIQ_USD]:
        try:
            liq = _deepest(a)
        except OSError:
            continue
        time.sleep(0.3)
        if liq < LIST_MIN_LIQ_USD:
            continue
        t = _doc["tokens"][a]
        try:                                                    # the name / ticker of today (a renamed token), and still genuine
            v = verify(a)
        except chain.RpcError:
            continue
        if not v["genuine"]:
            with _lock:
                t.update(genuine=False, why=v["why"])
            continue
        sym = v["symbol"]
        if sym in M.STOCK_BY_SYM:                               # a second token under a listed ticker: never two rows with one ticker
            log.warning("stockscan: %s %s has a listed ticker, not added", sym, a)
            continue
        row = {"symbol": sym, "name": short_name(sym, v["name"]), "kind": kind_of(v["name"]), "address": a, "found": int(time.time()),
               "liqUsd": round(liq)}
        with _lock:
            t.update(symbol=sym, name=v["name"])
            _doc["listed"][a] = row
        M.add_stock(row)
        added.append(sym)
        log.info("stockscan: listed %s %s (pool $%s)", sym, a, round(liq))
    STATE.update(liqAt=int(time.time()), listed=len(_doc["listed"]))
    if added:
        STATE["added"] = (STATE["added"] + added)[-50:]
    _save()
    return added


def loop(stop: threading.Event) -> None:
    _load()
    last_scan = last_liq = 0.0
    stop.wait(20)                                                # after the market index is up
    while not stop.is_set():
        now = time.time()
        try:
            if now - last_scan > SCAN_S:
                last_scan = now
                scan()
            if now - last_liq > LIQ_S:
                last_liq = now
                check_liquidity()
        except Exception as e:                                   # never let the loop die
            STATE["err"] = str(e)[:200]
            log.warning("stockscan loop: %s", e)
        stop.wait(30)
