"""GET /api/reach — the landing's counters, only measured ones (NEW-PITCH 16.11). Cached 24 h in the meta table, recomputed in the background,
served from the last saved measurement after a restart.

What is measured (route provider, its "other" bucket ~100/min, apart from the quote buckets; one request per network, with pauses):
  /chains?chainTypes=EVM          the provider's networks
  /tools?chains=4663              exchanges and bridges it can use on Robinhood Chain; its own tools (solver fill / solver bridge / deposit
                                  swap) are not counted
  /connections?fromChain=X&toChain=4663   per mainnet network: is there any token on X that can reach 4663 (and which)
  /tokens?chains=X                per connected network: native + main stables (USDC / USDT), only those inside the connection
Pay-from networks = EVM mainnets with a non-empty connection, a payable token, a public https RPC, an explorer and native decimals 18
(what a wallet can add). Excluded with a reason: Hyperliquid core (no public EVM RPC), Solana / Bitcoin (not EVM; Solana signing is not
supported by the sign layer), native decimals != 18. The same list is /api/tokens `chains[]` (tokens.pay_chains).
RFQ / prop makers: the provider shows only "other" for them; they are read from KyberSwap's own routes (the engine the kyberswap tool runs)
for USDG -> each board stock at $50k, once per measurement; every maker seen is kept in meta with first/last seen (history).
DEXes: Robinhood Chain pool dexes with swap volume in the last 24 h (trades.stats byDex, "other" excluded) — live, cached 5 min.
sources = dexes + makers + aggregators. ourFeePct is always 0.

Chain logos never point outside: a PNG source (trustwallet chain mark, slugs checked 16.11; else the native coin's picture when it is not
ETH) goes through tokenmeta.img_url -> /api/img/<id>, downloaded and checked once at measurement time; a source that fails is dropped
(logo null). The provider's own chain icons are SVG on its own domain and are never used."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import config as C
import market as M
import router
import store as S
import venues as V
from chain import http_json

log = logging.getLogger("rhdex.reach")
KEY = "reach:measure"
MAKERS_KEY = "reach:makers"
PERIOD_S = 86_400
RETRY_PARTIAL_S = 6 * 3600
PAUSE_S = float(os.environ.get("REACH_PAUSE_S") or 1.5)     # between provider calls: ~40/min, far under its 100/min
MAKER_WINDOW_S = 7 * 86_400                                  # a maker counts while it was seen in routes in the last 7 days
MAKER_SIZE_USD = 50_000
STATE = {"running": False, "lastRun": 0, "lastErr": "", "calls": 0, "phase": ""}
_mem = {"at": 0.0, "m": None}
_doc = {"at": 0.0, "doc": None}
_dex = {"at": 0.0, "rows": None}
_lock = threading.Lock()

OWN_TOOLS = {"smartDeposits"}       # the route API's own tools (plus its solver keys, V.is_intent): never counted as a bridge / aggregator
EXCLUDE = {1337: "no_public_evm_rpc"}                                 # Hyperliquid core: actions are signed off-EVM, RPC only via the provider
TW = "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/{}/info/logo.png"
# trustwallet folder per chain id — every slug answered 200 image/png on 16.11 (research probe); others have no chain mark there
TW_SLUG = {1: "ethereum", 42161: "arbitrum", 8453: "base", 999: "hyperevm", 143: "monad", 56: "smartchain", 10: "optimism", 137: "polygon",
           43114: "avalanchec", 100: "xdai", 1088: "metis", 1329: "sei", 146: "sonic", 1776: "nativeinjective", 196: "okxchain", 2020: "ronin",
           25: "cronos", 30: "rootstock", 324: "zksync", 42220: "celo", 4326: "megaeth", 5000: "mantle", 534352: "scroll", 59144: "linea",
           9745: "plasma", 8217: "klaytn"}
KYBER = "https://aggregator-api.kyberswap.com/robinhood/api/v1/routes"
_HOST = re.compile(r"^[a-z0-9.-]+$")


class _Stop(Exception):
    pass


# ------------------------------------------------------------------ helpers (pure; unit-tested in tools/test_engine_v2.py)
def clean_url(u, path_ok: bool = True) -> str | None:
    """A public https URL from the provider's metadata, or None: https only, a plain host, no credentials, never the provider's own domain."""
    if not isinstance(u, str) or len(u) > 200 or not u.startswith("https://") or V.FORBIDDEN.search(u) or "@" in u:
        return None
    rest = u[8:]
    host = rest.split("/", 1)[0].split(":")[0].lower()
    if not host or not _HOST.match(host) or "." not in host:
        return None
    if not path_ok and "/" in rest.rstrip("/"):
        return None
    if re.search(r"[\s\"'<>`\\]", u):
        return None
    return u.rstrip("/")


def chain_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "chain"


def _usdish(t: dict, kind: str) -> int:
    """preference score for a stable candidate (higher = better); 0 = not that stable"""
    sym = t.get("symbol") or ""
    ck = (t.get("coinKey") or "").upper()
    if kind == "USDC":
        if sym == "USDC":
            return 3
        return 1 if ck == "USDC" and sym.upper().startswith("USDC") and "." not in sym else 0
    if sym == "USDT":
        return 3
    if sym in ("USDT0", "USD₮0") or ck == "USDT0":
        return 2
    return 1 if ck == "USDT" and "." not in sym else 0


def pick_tokens(toks: list[dict], conn: set[str], native_addr: str) -> list[dict]:
    """Native + USDC + USDT of one network, only tokens inside the provider's connection to 4663; verified, decimals sane, stables priced
    0.9–1.1 (a fake "USDC" never makes it). -> [{a, s, n, d, k, logoUri}]"""
    native, best = None, {}
    for t in toks:
        a = (t.get("address") or "").lower()
        if a not in conn:
            continue
        if a != native_addr and t.get("verificationStatus") not in (None, "verified"):
            continue                                   # the native coin is the network's own (a screener flag on it means nothing)
        try:
            d = int(t.get("decimals"))
        except (TypeError, ValueError):
            continue
        if not 0 <= d <= 36:
            continue
        if a == native_addr:
            native = t
            continue
        for kind in ("USDC", "USDT"):
            sc = _usdish(t, kind)
            if not sc:
                continue
            try:
                px = float(t.get("priceUSD") or 0)
            except ValueError:
                px = 0
            if not 0.9 <= px <= 1.1:
                continue
            if kind not in best or sc > best[kind][0]:
                best[kind] = (sc, t)
    out = []
    for kind, t in [("native", native)] + [(k, best[k][1]) for k in ("USDC", "USDT") if k in best]:
        if not t:
            continue
        import tokenmeta
        sym = (t.get("symbol") or "").replace("₮", "T")                # «USD₮0» is USDT0 (never «USD0» — that is another coin)
        if tokenmeta.clean_symbol(sym) != sym:
            sym = tokenmeta.clean_symbol((t.get("coinKey") or sym).replace("₮", "T"))
        out.append({"a": (t.get("address") or "").lower(), "s": sym, "n": tokenmeta.clean_name(t.get("name") or sym) or sym,
                    "d": int(t["decimals"]), "k": "crypto" if kind == "native" else "stable", "logoUri": t.get("logoURI")})
    return out


def chain_row(c: dict, conn: set[str]) -> tuple[dict | None, str | None]:
    """(row, None) or (None, reason) for one provider network. Tokens are added by the caller."""
    cid = int(c.get("id") or 0)
    if cid in EXCLUDE:
        return None, EXCLUDE[cid]
    if (c.get("chainType") or "EVM") != "EVM":
        return None, "not_evm"
    mm = c.get("metamask") or {}
    nat = mm.get("nativeCurrency") or {}
    nt = c.get("nativeToken") or {}
    try:
        decs = {int(x) for x in (nat.get("decimals"), nt.get("decimals")) if x is not None}
    except (TypeError, ValueError):
        return None, "no_native"
    if decs != {18}:                 # Arc / Tempo / Stable: the provider's native has 6 decimals, the wallet's 18 — amounts would not agree
        return None, "native_decimals_not_18"
    rpc = [u for u in (clean_url(x) for x in (mm.get("rpcUrls") or [])) if u][:2]
    if not rpc:
        return None, "no_public_rpc"
    exp = next((u for u in (clean_url(x) for x in (mm.get("blockExplorerUrls") or [])) if u), None)
    if not exp:
        return None, "no_explorer"
    import tokenmeta
    name = tokenmeta.clean_name(V.sanitize(c.get("name") or "")) or f"Chain {cid}"
    sym = tokenmeta.clean_symbol(nat.get("symbol") or nt.get("symbol") or "")
    diamond = (c.get("diamondAddress") or "").lower()
    return {"id": cid, "key": chain_key(name), "name": name,
            "native": {"symbol": sym, "decimals": 18, "name": tokenmeta.clean_name(nat.get("name") or sym) or sym},
            "nativeAddress": (nt.get("address") or C.NATIVE).lower(), "nativeLogoUri": nt.get("logoURI"),
            "rpc": rpc, "explorer": exp, "fromTokens": len(conn),
            "routeContract": diamond if re.fullmatch(r"0x[0-9a-f]{40}", diamond) else None}, None


def conn_tokens(j: dict | None) -> set[str]:
    out = set()
    for cn in (j or {}).get("connections") or []:
        for t in cn.get("fromTokens") or []:
            a = (t.get("address") or "")
            out.add(a.lower() if a.startswith("0x") else a)
    return out


MAKER_HINT = re.compile(r"pmm|rfq|bebop|native|hashflow|prop|clipper|swaap|dexalot|limit-order|orderbook", re.I)


MAKER_NEUTRAL = "Market maker"     # audit v4 design 11: KyberSwap's numbered RFQ makers (pmm-19, pmm-20) have no public name


def maker_name(key: str, stored: str | None) -> str:
    """the display name of a maker: numbered ones neutral (also for names stored before 16.11 as «PMM 19»)"""
    if re.fullmatch(r"pmm-\d+", key or "") or re.fullmatch(r"PMM \d+", stored or ""):
        return MAKER_NEUTRAL
    return stored or key


def maker_of(exchange: str, pool_type: str | None) -> dict | None:
    """A KyberSwap route leg -> maker {key, name, kind} when it is an RFQ / prop maker (not an AMM pool)."""
    ex = (exchange or "").lower()
    if not ex or not (MAKER_HINT.search(ex) or MAKER_HINT.search(pool_type or "")):
        return None
    if V.FORBIDDEN.search(ex):
        return None
    m = re.fullmatch(r"pmm-(\d+)", ex)
    names = {"bebop": "Bebop", "fermi-prop": "Fermi", "manta-prop": "Manta", "native-v2": "Native", "native": "Native", "hashflow": "Hashflow",
             "metric-propamm": "Metric", "clipper": "Clipper", "swaap": "Swaap", "dexalot": "Dexalot"}
    name = MAKER_NEUTRAL if m else names.get(ex) or re.sub(r"-(prop|propamm|rfq|v\d+)$", "", ex).replace("-", " ").title()
    kind = "prop-amm" if "prop" in ex else "rfq"
    return {"key": "metric" if ex == "metric-propamm" else re.sub(r"[^a-z0-9-]", "", ex), "name": name, "kind": kind,
            **({"labelTbd": True} if m else {})}


def makers_in(route_json) -> list[dict]:
    out, seen = [], set()

    def walk(x):
        if isinstance(x, dict):
            if "exchange" in x:
                mk = maker_of(str(x.get("exchange")), str(x.get("poolType") or ""))
                if mk and mk["key"] not in seen:
                    seen.add(mk["key"])
                    out.append(mk)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(route_json)
    return out


# ------------------------------------------------------------------ storage
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reach-snapshot.json")   # shipped with the image


def snapshot() -> dict:
    """The last measurement shipped with the code (tools/reach_measure.py --snapshot writes it): a cold start — a fresh /data and the
    route provider not answering yet — serves the 48 measured networks and counts instead of the seven built-in ones (audit v4 M13)."""
    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_snapshot() -> dict:
    m = load()
    if not m or m.get("snapshot"):
        raise RuntimeError("no own measurement in meta to snapshot")
    m = json.loads(json.dumps(m))
    for k in ("exchanges", "bridges"):        # the provider's own tools are never counted (_tool_rows) — and not shipped either
        m["tools"][k] = [t for t in m["tools"].get(k) or [] if t.get("key") not in OWN_TOOLS and not V.is_intent(t.get("key")) and not V.FORBIDDEN.search(json.dumps(t))]
    m.pop("toolsRaw", None)
    d = {"reach": m, "makers": makers_hist(fallback=False), "dexes": _dexes(), "writtenAt": int(time.time())}
    tmp = SNAPSHOT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    os.replace(tmp, SNAPSHOT)
    return d


def load() -> dict | None:
    now = time.time()
    with _lock:
        if _mem["m"] is not None and now - _mem["at"] < 60:
            return _mem["m"]
    try:
        v = S.meta_get(KEY)
        m = json.loads(v) if v else None
    except Exception:
        m = None
    if m is None:
        sm = snapshot().get("reach")
        m = {**sm, "snapshot": True} if isinstance(sm, dict) and sm.get("chains") else None
    with _lock:
        _mem.update(at=now, m=m)
    return m


def _save(m: dict):
    S.meta_set(KEY, json.dumps(m, separators=(",", ":")))
    with _lock:
        _mem.update(at=time.time(), m=m)
        _doc.update(at=0.0, doc=None)


def makers_hist(fallback: bool = True) -> dict:
    try:
        v = S.meta_get(MAKERS_KEY)
        if v:
            return json.loads(v)
    except Exception:
        pass
    mk = snapshot().get("makers") if fallback else None      # cold start: the makers seen in the shipped measurement (read paths only)
    return mk if isinstance(mk, dict) else {}


def note_makers(found: list[dict], pair: str, now: int | None = None) -> dict:
    now = int(now or time.time())
    h = makers_hist(fallback=False)
    for mk in found:
        e = h.setdefault(mk["key"], {"name": mk["name"], "kind": mk["kind"], "firstSeen": now, "pairs": []})
        e.update(name=mk["name"], kind=mk["kind"], lastSeen=now)
        if mk.get("labelTbd"):
            e["labelTbd"] = True
        if pair not in e["pairs"]:
            e["pairs"] = (e["pairs"] + [pair])[-12:]
    S.meta_set(MAKERS_KEY, json.dumps(h, separators=(",", ":")))
    with _lock:
        _doc.update(at=0.0, doc=None)
    return h


# ------------------------------------------------------------------ measurement
def _logo_checked(src: str | None) -> str | None:
    """the source only if our picture proxy can really serve it (downloaded + type-checked now, cached on disk)"""
    if not src or V.FORBIDDEN.search(src) or src.lower().split("?")[0].endswith(".svg"):
        return None                                 # never the provider's own host; SVG is refused by the proxy anyway
    import tokenmeta
    p = tokenmeta.img_url(src)
    if not p:
        return None
    try:
        got = tokenmeta.fetch_image(p.rsplit("/", 1)[-1])
    except Exception:
        got = None
    return src if got else None


def _chain_logo_src(row: dict) -> str | None:
    import tokens as TK
    if row["id"] in TK.LOCAL_CHAIN_LOGO:
        return "local:" + TK.LOCAL_CHAIN_LOGO[row["id"]]
    if row["id"] in TW_SLUG:
        s = _logo_checked(TW.format(TW_SLUG[row["id"]]))
        if s:
            return s
    if row["native"]["symbol"] not in ("ETH", "WETH"):          # the native coin's mark stands for the network only when it is not ETH
        return _logo_checked(row.get("nativeLogoUri"))
    return None


def _token_logo_src(t: dict) -> str | None:
    import tokens as TK
    loc = TK.local_token_logo(t["s"])
    if loc:
        return "local:" + loc
    return _logo_checked(t.get("logoUri"))


def measure(stop: threading.Event, prev: dict | None = None) -> dict | None:
    """One full measurement (~2 provider calls per connected network). Keeps a network's previous row when its calls fail (partial)."""
    STATE["calls"] = 0
    prev_rows = {r["id"]: r for r in (prev or {}).get("chains") or []}

    def rcall(fn, *a, **k):
        for _ in range(3):
            if stop.is_set():
                raise _Stop()
            try:
                STATE["calls"] += 1
                return fn(*a, **k)
            except router.Budget as e:
                stop.wait(min(max(int(e.reset_in), 5), 120))
            except router.Upstream as e:
                if e.code in (400, 404, 422):
                    return None
                stop.wait(5)
        return None

    STATE["phase"] = "chains"
    cj = rcall(router.chains)
    if not cj or not cj.get("chains"):
        return None
    stop.wait(PAUSE_S)
    tj = rcall(router.tools, C.CHAIN) or (prev or {}).get("toolsRaw")
    tools = {"exchanges": [{"key": t.get("key"), "name": t.get("name")} for t in (tj or {}).get("exchanges") or []],
             "bridges": [{"key": t.get("key"), "name": t.get("name")} for t in (tj or {}).get("bridges") or []]}
    rows, excluded, partial = [], [], not tj
    for c in cj["chains"]:
        if stop.is_set():
            raise _Stop()
        cid = int(c.get("id") or 0)
        if not c.get("mainnet") or cid == C.CHAIN or cid not in C.PAY_CHAIN_IDS:      # owner's list of 23 (config.PAY_CHAIN_IDS)
            continue
        STATE["phase"] = f"chain {cid}"
        stop.wait(PAUSE_S)
        conn = rcall(router.connections, cid, C.CHAIN)
        if conn is None:
            if cid in prev_rows:
                rows.append(prev_rows[cid])
                partial = True
            continue
        cs = conn_tokens(conn)
        if not cs:
            continue
        row, why = chain_row(c, cs)
        if not row:
            excluded.append({"id": cid, "name": V.sanitize(c.get("name") or ""), "reason": why})
            continue
        stop.wait(PAUSE_S)
        tl = rcall(router.tokens, [cid], prio="reach", ttl=0)
        if tl is None:
            if cid in prev_rows:
                rows.append(prev_rows[cid])
                partial = True
            continue
        toks = pick_tokens((tl.get("tokens") or {}).get(str(cid)) or [], cs, row["nativeAddress"])
        if not toks:
            excluded.append({"id": cid, "name": row["name"], "reason": "no_payable_token"})
            continue
        for t in toks:
            t["logoSrc"] = _token_logo_src(t)
            t.pop("logoUri", None)
        row["tokens"] = toks
        row["logoSrc"] = _chain_logo_src(row)
        row.pop("nativeLogoUri", None)
        rows.append(row)
    return {"at": int(time.time()), "partial": partial, "chains": rows, "excluded": excluded, "tools": tools,
            "calls": STATE["calls"], "providerChains": len(cj["chains"])}


def discover_makers(stop: threading.Event) -> int:
    """KyberSwap's own routes for USDG -> each board stock at $50k: the RFQ / prop makers in them (6 calls per measurement)."""
    n = 0
    for sym in C.BOARD_STOCKS:
        s = M.STOCK_BY_SYM.get(sym)
        if not s or stop.is_set():
            continue
        url = f"{KYBER}?tokenIn={C.USDG}&tokenOut={s['address'].lower()}&amountIn={MAKER_SIZE_USD * 10 ** 6}"
        try:
            code, j, _ = http_json(url, timeout=20)
        except Exception:
            code, j = 0, None
        if code == 200 and isinstance(j, dict) and j.get("data"):
            found = makers_in(j["data"].get("routeSummary") or j["data"])
            note_makers(found, f"USDG/{sym}")
            n += 1
        stop.wait(2)
    return n


def run_once(stop: threading.Event) -> bool:
    if STATE["running"]:
        return False
    STATE.update(running=True, lastErr="")
    try:
        prev = load()
        m = measure(stop, prev)
        if m:
            _save(m)
            log.info("reach measured: %d pay-from networks, %d excluded, %d calls%s", len(m["chains"]), len(m["excluded"]), m["calls"],
                     " (partial)" if m["partial"] else "")
        else:
            STATE["lastErr"] = "provider catalog unavailable"
        STATE["phase"] = "makers"
        discover_makers(stop)
        return bool(m)
    except _Stop:
        return False
    except Exception as e:
        STATE["lastErr"] = str(e)[:160]
        log.warning("reach measure failed: %s", e, exc_info=True)
        return False
    finally:
        STATE.update(running=False, lastRun=int(time.time()), phase="")


def loop(stop: threading.Event):
    """Live mode only (server.py does not start it under QUOTE_REPLAY): measure when the saved one is older than 24 h (6 h if partial)."""
    stop.wait(20)                                             # let pools, prices and the trade scanner start first
    fails = 0
    while not stop.is_set():
        m = load()
        due = (m["at"] + (RETRY_PARTIAL_S if m.get("partial") else PERIOD_S)) if m and not m.get("snapshot") else 0   # a shipped one: measure now
        if time.time() >= due:
            ok = run_once(stop)
            fails = 0 if ok else fails + 1
            wait = 60 if ok else min(3600, 300 * (2 ** min(fails, 4)))
        else:
            wait = min(3600, max(60, due - time.time()))
        stop.wait(wait)


# ------------------------------------------------------------------ public doc
def _dexes() -> list[dict]:
    now = time.time()
    with _lock:
        if _dex["rows"] is not None and now - _dex["at"] < 300:
            return _dex["rows"]
    import trades as TR
    rows = [{"dex": r["dex"], "name": r["name"], "logo": r["logo"], "volume24hUsd": r["usd"]}
            for r in TR.stats()["byDex"] if r["dex"] != "other" and (r["usd"] or 0) > 0]
    with _lock:
        _dex.update(at=now, rows=rows)
    return rows


def _tool_rows(items: list[dict], kind: str) -> list[dict]:
    out, seen = [], set()
    for t in items:
        k = t.get("key") or ""
        if k in OWN_TOOLS or V.is_intent(k) or V.FORBIDDEN.search(k) or V.FORBIDDEN.search(str(t.get("name") or "")):
            continue
        v = V.tool(k)
        if v["kind"] in ("solver", "swap"):
            continue
        if v["displayKey"] in seen:
            continue
        seen.add(v["displayKey"])
        import tokenmeta
        name = v["name"] if k in V.TOOLS else (tokenmeta.clean_name(str(t.get("name") or k)) or k)
        out.append({"key": v["displayKey"], "name": name, "logo": v["logo"], "kind": kind})
    return out


def doc() -> dict:
    now = time.time()
    with _lock:
        if _doc["doc"] is not None and now - _doc["at"] < 60:
            return _doc["doc"]
    import tokens as TK
    import trades as TR
    m = load()
    chains = [{"id": c["id"], "name": c["name"], "logo": c["logo"]} for c in TK.pay_chains()]
    try:
        dexes = _dexes()
    except Exception:
        dexes = []
    if not TR.STATE.get("backfilled"):      # right after a restart the 24 h trade scan is still filling: the shipped measurement's dexes (prod minor 1)
        sd = snapshot().get("dexes")
        if isinstance(sd, list) and len(sd) > len(dexes):
            dexes = sd
    dex_keys = {d["dex"] for d in dexes}
    hist = makers_hist()
    makers = []
    for k, e in sorted(hist.items(), key=lambda kv: (-(kv[1].get("lastSeen") or 0), kv[0])):
        if now - (e.get("lastSeen") or 0) > MAKER_WINDOW_S or k in dex_keys:      # Metric counts once: as a dex when it shows volume
            continue
        logo = V.dex(k)["logo"] if k in V.DEXES else V.OTHER_LOGO
        makers.append({"key": k, "name": maker_name(k, e.get("name")), "logo": logo, "kind": e.get("kind"), "lastSeen": e.get("lastSeen"),
                       **({"labelTbd": True} if e.get("labelTbd") else {})})
    same = [x for x in makers if x["name"] == MAKER_NEUTRAL]
    if len(same) > 1:                       # premium audit: two unnamed RFQ makers read as one duplicated name -> number them
        for i, x in enumerate(same, 1):
            x["name"] = f"{MAKER_NEUTRAL} {i}"
    if not m:
        d = {"pending": True, "chains": {"count": len(chains), "external": len([c for c in chains if c["id"] != C.CHAIN]), "list": chains},
             "bridges": {"count": None, "list": []}, "dexes": {"count": len(dexes), "list": dexes, "at": int(now)},
             "makers": {"count": len(makers), "list": makers, "windowS": MAKER_WINDOW_S}, "aggregators": {"count": None, "list": []},
             "sources": {"count": None}, "ourFeePct": 0, "measuredAt": None, "stale": True}
    else:
        bridges = _tool_rows(m["tools"]["bridges"], "bridge")
        aggs = _tool_rows(m["tools"]["exchanges"], "aggregator")
        stale = bool(now - m["at"] > PERIOD_S + 3600 or m.get("partial") or not TR.STATE.get("backfilled"))
        d = {"chains": {"count": len(chains), "external": len([c for c in chains if c["id"] != C.CHAIN]), "list": chains},
             "bridges": {"count": len(bridges), "list": bridges},
             "dexes": {"count": len(dexes), "list": dexes, "at": int(now)},
             "makers": {"count": len(makers), "list": makers, "windowS": MAKER_WINDOW_S},
             "aggregators": {"count": len(aggs), "list": aggs},
             "sources": {"count": len(dexes) + len(makers) + len(aggs)},
             "ourFeePct": 0, "measuredAt": m["at"], "stale": stale, **({"snapshot": True} if m.get("snapshot") else {})}
    with _lock:
        _doc.update(at=now, doc=d)
    return d


def health() -> dict:
    m = load()
    return {**STATE, "measuredAt": (m or {}).get("at"), "partial": (m or {}).get("partial"), "payChains": len([r for r in (m or {}).get("chains") or [] if r.get("id") in C.PAY_CHAIN_IDS]),
            "excluded": (m or {}).get("excluded")}
