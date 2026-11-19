"""«Tokens» next to Stocks: the live non-stock tokens of Robinhood Chain 4663 that
really have liquidity. GET /api/network-tokens.

Candidates: the route provider's token list for 4663 (one list call, its «other» bucket, already used for prices) + the other side of the
deepest USDG and WETH pairs on DexScreener (token-pairs, top 30 each). Depth and volume: DexScreener tokens/v1 in batches of 30 (its
deepest pair per token): liqUsd = that pool's liquidity, vol24hUsd = its 24 h volume, priceUsd when the token is the pair's base.
Rule: liqUsd >= MIN_LIQ_USD and vol24hUsd >= MIN_VOL_USD (16.11 data: 138 tokens pass at $15k / $1k; the count is flat between $5k and
$20k — 140.. 137 — so the bar does not decide much, the volume floor removes parked pools). Stocks of our list are never here (they are
the Stocks section); ETH, WETH, USDG, cbBTC come first as listed:true. A token whose symbol is one of our stocks (on another address) is
kept with clash:true, its own name and never our stock's logo. Names via tokenmeta.clean_name; logos only as /api/img/<id> (the provider
list's logoURI, else the contract lookup's picture). Decimals: the provider list, else a contract read (tokenmeta.lookup, cached).
Refresh every REFRESH_S in the background (live mode only), cache in meta, cold start = the shipped snapshot (network-tokens-snapshot.json)."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import config as C
import market as M
import store as S
import venues as V
from chain import http_json

log = logging.getLogger("rhdex.ntokens")
MIN_LIQ_USD = 15_000
MIN_VOL_USD = 1_000
POOL_COUNT_MIN_USD = 1_000          # a pair counts in «Pools» when it holds at least this (DexScreener liquidity)
REFRESH_S = 600
KEY = "ntokens:doc"
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network-tokens-snapshot.json")
DS = "https://api.dexscreener.com"
_lock = threading.Lock()
_mem: dict = {"at": 0.0, "doc": None}
STATE = {"lastRun": 0, "lastErr": "", "candidates": 0, "kept": 0, "hidden": []}


# token names that never appear in a public answer: the same hidden names as every public JSON (venues.FORBIDDEN, from ROUTE_HIDDEN_NAMES
# and the route API host) plus the optional TOKEN_HIDDEN_NAMES regex (e.g. a launch platform's own name used as a token name).
def _name_guard() -> re.Pattern:
    extra = (os.environ.get("TOKEN_HIDDEN_NAMES") or "").strip()
    pats = [V.FORBIDDEN.pattern]
    if extra:
        try:
            re.compile(extra)
            pats.append(extra)
        except re.error:
            pass
    return re.compile("|".join(f"(?:{p})" for p in pats), re.I)


NAME_GUARD = _name_guard()


def _clash_syms() -> set[str]:
    return {s["symbol"].upper() for s in M.STOCKS}


def _candidates() -> dict[str, dict]:
    """address -> {symbol, name, decimals, logoSrc, price} from the provider list + the USDG / WETH pair partners"""
    import router
    out: dict[str, dict] = {}
    try:
        j = router.tokens([C.CHAIN], prio="board", ttl=300)
        for t in (j.get("tokens") or {}).get(str(C.CHAIN), []):
            a = (t.get("address") or "").lower()
            if not a or a in M.STOCK_BY_ADDR:
                continue
            try:
                px = float(t.get("priceUSD") or 0) or None
            except ValueError:
                px = None
            out[a] = {"symbol": t.get("symbol"), "name": t.get("name"), "decimals": t.get("decimals"), "logoSrc": t.get("logoURI"), "price": px}
    except Exception as e:
        STATE["lastErr"] = f"token list: {type(e).__name__}"
    for base in (C.USDG, C.WETH):
        try:
            code, pairs, _ = http_json(f"{DS}/token-pairs/v1/robinhood/{base}", timeout=20)
        except Exception:
            continue
        if code != 200 or not isinstance(pairs, list):
            continue
        for p in pairs:
            for side in ("baseToken", "quoteToken"):
                a = (p.get(side) or {}).get("address", "").lower()
                if a and a not in (C.USDG, C.WETH) and a not in M.STOCK_BY_ADDR and a not in out:
                    out[a] = {"symbol": p[side].get("symbol"), "name": p[side].get("name"), "decimals": None, "logoSrc": None, "price": None}
        time.sleep(0.3)
    return out


def _depth(addrs: list[str]) -> dict[str, dict]:
    """address -> {liq, vol, price, pool, dex} of its deepest pair (DexScreener tokens/v1, 30 per call)"""
    got: dict[str, dict] = {}
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i + 30]
        try:
            code, pairs, _ = http_json(f"{DS}/tokens/v1/robinhood/" + ",".join(chunk), timeout=20)
        except Exception:
            continue
        if code != 200 or not isinstance(pairs, list):
            continue
        want = set(chunk)
        for p in pairs:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            vol = float((p.get("volume") or {}).get("h24") or 0)
            for side in ("baseToken", "quoteToken"):
                a = (p.get(side) or {}).get("address", "").lower()
                if a not in want:
                    continue
                other = (p.get("quoteToken" if side == "baseToken" else "baseToken") or {}).get("address", "").lower()
                e = got.get(a)
                if e is None or liq > e["liq"]:
                    px = None
                    if side == "baseToken" and p.get("priceUsd"):
                        try:
                            px = float(p["priceUsd"])
                        except ValueError:
                            px = None
                    got[a] = {"liq": liq, "vol": vol, "price": px, "pool": (p.get("pairAddress") or "").lower(), "quote": other}
                if side == "baseToken" and (p.get("info") or {}).get("imageUrl") and not got[a].get("image"):
                    got[a]["image"] = _ds_image(p["info"]["imageUrl"])   # the token's profile picture (logos everywhere)
        time.sleep(0.3)
    return got


def _img_ok(iid: str | None) -> bool:
    """the picture really loads through /api/img (downloaded and type-checked now, cached on disk) — never hand out an id that 404s"""
    import tokenmeta
    if not iid or not iid.startswith("/api/img/"):
        return False
    try:
        return tokenmeta.fetch_image(iid.rsplit("/", 1)[-1]) is not None
    except Exception:
        return False


def _src_usable(src: str | None) -> bool:
    return bool(src) and not str(src).lower().split("?")[0].endswith(".svg") and "unknown" not in str(src).lower().rsplit("/", 1)[-1]


def _ds_image(u: str) -> str | None:
    """a DexScreener profile picture, asked at 128 px (it is shown at 22–34 px)"""
    u = str(u or "")
    if not u.startswith("https://"):
        return None
    return re.sub(r"width=\d+&height=\d+", "width=128&height=128", u)


def _row(a: str, c: dict, d: dict, clash: set[str]) -> dict | None:
    import lookalike as LK
    import tokenmeta
    import local_logos as LL
    sym, name, dec, logo = c.get("symbol"), c.get("name"), c.get("decimals"), LL.path("net", a)   # baked into the build first (prod S1)
    no_logo = LL.denied("net", a)                 # another company's brand: a letter, never any source's picture
    if not logo and not no_logo and _src_usable(c.get("logoSrc")):
        cand = tokenmeta.img_url(c["logoSrc"])
        logo = cand if _img_ok(cand) else None
    if dec is None or not logo:
        try:
            m = tokenmeta.lookup(a)          # contract read (+ its picture), cached in meta for days
            sym, name = sym or m.get("symbol"), name or m.get("name")
            dec = dec if dec is not None else m.get("decimals")
            if not logo and not no_logo and _img_ok(m.get("logo")):
                logo = m.get("logo")
        except Exception:
            if dec is None:
                return None                  # decimals unknown: not quotable, not listed
    if not logo and not no_logo and d.get("image"):            # third source: the token's DexScreener profile picture
        cand = tokenmeta.img_url(d["image"])
        logo = cand if _img_ok(cand) else None
    sym = tokenmeta.clean_name(str(sym or ""))[:16]
    if not sym or NAME_GUARD.search(sym) or NAME_GUARD.search(str(name or "")):
        return None
    nm = tokenmeta.clean_name(str(name or "")) or sym
    lk = LK.check(sym, nm)
    if lk["lookalike"] or sym.upper() in clash:   # tokens named like a public company / our stock are hidden here
        STATE["hidden"].append({"address": a, "symbol": sym, "name": nm, "why": lk["why"] or "our_stock_ticker"})
        return None
    px = d.get("price") or c.get("price")
    return {"address": a, "symbol": sym, "name": nm, "decimals": int(dec), "logo": logo,
            "liqUsd": round(d["liq"]), "vol24hUsd": round(d["vol"]), "priceUsd": float(f"{px:.6g}") if px else None, "pool": d.get("pool"),
            "poolQuote": d.get("quote"), "pools": d.get("pools"), "dexes": d.get("dexes"), "kind": "token", "listed": False, "clash": False}


def _listed_totals(addrs: list[str]) -> dict[str, dict]:
    """listed tokens: liquidity / 24 h volume summed over every pool that holds them, from the union of their DexScreener pair lists
    (token-pairs, top 30 each, deduplicated by pool) — the deepest pair alone is the same WETH/USDG pool for both (audit v5 design 9)"""
    pools: dict[str, dict] = {}
    for a in addrs:
        try:
            code, pairs, _ = http_json(f"{DS}/token-pairs/v1/robinhood/{a}", timeout=20)
        except Exception:
            continue
        if code == 200 and isinstance(pairs, list):
            for p in pairs:
                pools.setdefault((p.get("pairAddress") or "").lower(), p)
        time.sleep(0.3)
    out: dict[str, dict] = {}
    for p in pools.values():
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        for side in ("baseToken", "quoteToken"):
            a = (p.get(side) or {}).get("address", "").lower()
            if a in addrs:
                e = out.setdefault(a, {"liq": 0.0, "vol": 0.0, "pools": 0})
                e["liq"] += liq
                e["vol"] += vol
                e["pools"] += 1
    return out


def _pair_counts(addrs: list[str]) -> dict[str, dict]:
    """/tokens «Pools»: per token the pairs holding at least POOL_COUNT_MIN_USD and their distinct venues, from
    DexScreener token-pairs (one call per token, top 30 pairs). Kept 1 h in meta: ~125 calls an hour, not every 10-min refresh."""
    k = "ntokens:counts"
    try:
        c = json.loads(S.meta_get(k) or "null")
    except Exception:
        c = None
    if c and time.time() - c.get("at", 0) < 3600 and set(addrs) <= set(c.get("rows", {})):
        return c["rows"]
    rows: dict[str, dict] = {}
    for a in addrs:
        try:
            code, pairs, _ = http_json(f"{DS}/token-pairs/v1/robinhood/{a}", timeout=20)
        except Exception:
            continue
        if code == 200 and isinstance(pairs, list):
            ok = [p for p in pairs if float((p.get("liquidity") or {}).get("usd") or 0) >= POOL_COUNT_MIN_USD]
            rows[a] = {"pools": len({(p.get("pairAddress") or "").lower() for p in ok}), "dexes": len({p.get("dexId") or "?" for p in ok})}
        time.sleep(0.25)
    if rows:
        S.meta_set(k, json.dumps({"at": time.time(), "rows": rows}))
    return rows


def build_doc() -> dict:
    cands = _candidates()
    STATE["candidates"] = len(cands)
    main = {x[0] for x in __import__("tokens").MAIN_4663}
    addrs = [a for a in cands if a not in main]
    depth = _depth(addrs + [a for a in main])
    clash = _clash_syms()
    STATE["hidden"] = []
    rows = []
    for a in addrs:
        d = depth.get(a)
        if not d or d["liq"] < MIN_LIQ_USD or d["vol"] < MIN_VOL_USD:
            continue
        r = _row(a, cands[a], d, clash)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: (-r["liqUsd"], r["symbol"]))
    pc = _pair_counts([r["address"] for r in rows])
    for r in rows:
        c = pc.get(r["address"])
        r["pools"], r["dexes"] = (c["pools"], c["dexes"]) if c else (None, None)
    import tokens as TK
    listed = []
    tot = _listed_totals([x[0] for x in TK.MAIN_4663 if x[0] != C.NATIVE])   # native ETH has no pools of its own (WETH carries them)
    for a, sym, name, dec, kind, logo in TK.MAIN_4663:
        d = tot.get(a)
        listed.append({"address": a, "symbol": sym, "name": name, "decimals": dec, "logo": TK._logo_ok("tokens/" + logo), "kind": kind,
                       "listed": True, "clash": False, "liqUsd": round(d["liq"]) if d and d.get("liq") else None,
                       "vol24hUsd": round(d["vol"]) if d and d.get("vol") else None, "pools": d["pools"] if d else None, "priceUsd": M.price(a)})
    STATE["kept"] = len(rows)
    return {"rule": {"minLiqUsd": MIN_LIQ_USD, "minVol24hUsd": MIN_VOL_USD, "depth": "deepest pool", "lookalikes": "hidden"},
            "listed": listed, "tokens": rows, "count": len(rows), "hiddenLookalikes": len(STATE["hidden"]), "at": int(time.time())}


def _save(doc: dict):
    S.meta_set(KEY, json.dumps(doc, separators=(",", ":")))
    with _lock:
        _mem.update(at=time.time(), doc=doc)


def load() -> dict | None:
    now = time.time()
    with _lock:
        if _mem["doc"] is not None and now - _mem["at"] < 60:
            return _mem["doc"]
    doc = None
    try:
        v = S.meta_get(KEY)
        doc = json.loads(v) if v else None
    except Exception:
        doc = None
    if doc is None:
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                doc = {**json.load(f), "snapshot": True}
        except (OSError, ValueError):
            doc = None
    with _lock:
        _mem.update(at=now, doc=doc)
    return doc


def _localize(rows: list[dict], strict: bool = False) -> list[dict]:
    """a baked logo wins over an /api/img id; strict (the shipped snapshot): an id without a baked file becomes null — no 404 in prod"""
    import local_logos as LL
    out = []
    for r in rows:
        lp = LL.path("net", r["address"])
        if LL.denied("net", r["address"]):
            r = {**r, "logo": None}
        elif lp:
            r = {**r, "logo": lp}
        elif strict and (r.get("logo") or "").startswith("/api/img/"):
            r = {**r, "logo": None}
        out.append(r)
    return out


def doc() -> dict:
    d = load()
    if d and d.get("tokens"):
        d = {**d, "tokens": _localize(d["tokens"], strict=bool(d.get("snapshot")))}
    if not d:
        return {"pending": True, "rule": {"minLiqUsd": MIN_LIQ_USD, "minVol24hUsd": MIN_VOL_USD, "depth": "deepest pool", "lookalikes": "hidden"}, "listed": [],
                "tokens": [], "count": 0, "at": None, "stale": True}
    return {**d, "stale": bool(time.time() - (d.get("at") or 0) > 3 * REFRESH_S)}


def find(addr: str) -> dict | None:
    """a network token of the list (quote / build know its decimals without a chain read)"""
    a = (addr or "").lower()
    d = load() or {}
    for r in d.get("tokens") or []:
        if r["address"] == a:
            return _localize([r], strict=bool(d.get("snapshot")))[0]
    return None


def write_snapshot() -> dict:
    d = load()
    if not d or d.get("snapshot"):
        raise RuntimeError("no own list in meta to snapshot")
    tmp = SNAPSHOT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({**{k: v for k, v in d.items() if k not in ("snapshot", "stale")}, "tokens": _localize(d.get("tokens") or [], strict=True)},
                  f, separators=(",", ":"))
    os.replace(tmp, SNAPSHOT)
    return d


def run_once() -> bool:
    try:
        d = build_doc()
        if d["count"] or not (load() or {}).get("count"):
            _save(d)
        STATE.update(lastRun=int(time.time()), lastErr=STATE["lastErr"] if d["count"] else "no token passed")
        return True
    except Exception as e:
        STATE["lastErr"] = str(e)[:160]
        log.warning("network tokens: %s", e, exc_info=True)
        return False


def loop(stop: threading.Event):
    stop.wait(30)                         # pools, prices first
    while not stop.is_set():
        d = load()
        due = 0 if (not d or d.get("snapshot")) else (d.get("at") or 0) + REFRESH_S
        if time.time() >= due:
            run_once()
        stop.wait(max(60, min(REFRESH_S, (due + REFRESH_S - time.time()) if due else REFRESH_S)))
