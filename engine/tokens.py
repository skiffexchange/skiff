"""Token index for the instant picker: every stock token on 4663 (engine/stocks.json, from the launchpad knowledge base), the main 4663 tokens, and the
Pay-from networks' tokens: native + USDC / USDT (+ cbBTC on Base / Ethereum). Pay-from networks = Robinhood Chain, the six networks the
sign layer was checked with (Base, Arbitrum, Ethereum, Optimism, BNB, Polygon) and every other EVM mainnet the route provider connects into
4663 (reach.py measurement, 24 h). Logos: local files under /assets/logos/ or our own /api/img/<id> (stocks: nvstly mirror -> FMP; SPCX
(nvstly SpaceX mark) and SKHY (FMP 000660.KS, SK hynix) added 17.11 in web-src/assets/logos/stocks — null + needsManualLogo only if a file is missing). Prices: market.PRICES (4663) or the provider's token list per network (background)."""
from __future__ import annotations

import os
import threading
import time

import config as C
import market as M

L = "/assets/logos/"
# Robinhood Chain + the six source networks the sign layer was built and checked with (sign/chains.js: same RPCs and explorers). Every other
# Pay-from network comes from the route provider's catalog, measured by reach.py (EVM mainnet, non-empty connection into 4663, payable token,
# public https RPC, explorer, native decimals 18). Solana is not a Pay-from network: the sign layer does not sign there (solana_unsupported).
STATIC_CHAINS = [
    {"id": 4663, "key": "robinhood", "name": "Robinhood Chain", "logo": None, "native": {"symbol": "ETH", "decimals": 18, "name": "Ether"},
     "rpc": ["https://rpc.mainnet.chain.robinhood.com"], "explorer": "https://robinhoodchain.blockscout.com"},
    {"id": 8453, "key": "base", "name": "Base", "logo": "chains/base.png", "native": {"symbol": "ETH", "decimals": 18, "name": "Ether"},
     "rpc": ["https://mainnet.base.org"], "explorer": "https://basescan.org"},
    {"id": 42161, "key": "arbitrum", "name": "Arbitrum", "logo": "chains/arbitrum.png", "native": {"symbol": "ETH", "decimals": 18, "name": "Ether"},
     "rpc": ["https://arb1.arbitrum.io/rpc"], "explorer": "https://arbiscan.io"},
    {"id": 1, "key": "ethereum", "name": "Ethereum", "logo": "chains/ethereum.png", "native": {"symbol": "ETH", "decimals": 18, "name": "Ether"},
     "rpc": ["https://ethereum-rpc.publicnode.com"], "explorer": "https://etherscan.io"},
    {"id": 10, "key": "optimism", "name": "Optimism", "logo": "chains/optimism.png", "native": {"symbol": "ETH", "decimals": 18, "name": "Ether"},
     "rpc": ["https://mainnet.optimism.io"], "explorer": "https://optimistic.etherscan.io"},
    {"id": 56, "key": "bnb", "name": "BNB Chain", "logo": "chains/bnb.png", "native": {"symbol": "BNB", "decimals": 18, "name": "BNB"},
     "rpc": ["https://bsc-dataseed.bnbchain.org"], "explorer": "https://bscscan.com"},
    {"id": 137, "key": "polygon", "name": "Polygon", "logo": "chains/polygon.png", "native": {"symbol": "POL", "decimals": 18, "name": "POL"},
     "rpc": ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org"], "explorer": "https://polygonscan.com"},   # polygon-rpc.com: 401 (16.11)
]
STATIC_BY_ID = {c["id"]: c for c in STATIC_CHAINS}
LOCAL_CHAIN_LOGO = {c["id"]: c["logo"][len("chains/"):] for c in STATIC_CHAINS if c["logo"]}
LOCAL_TOKEN_LOGO = {"ETH": "eth.png", "WETH": "eth.png", "USDC": "usdc.png", "USDT": "usdt.png", "USDT0": "usdt.png", "BNB": "bnb.png",
                    "POL": "pol.png", "SOL": "sol.png", "CBBTC": "cbbtc.png"}
N = C.NATIVE
MAIN_4663 = [
    (N, "ETH", "Ether", 18, "crypto", "eth.png"),
    (C.WETH, "WETH", "Wrapped Ether", 18, "crypto", "eth.png"),
    (C.USDG, "USDG", "Global Dollar", 6, "stable", "usdg.png"),
    (C.CBBTC, "cbBTC", "Coinbase Wrapped BTC", 8, "crypto", "cbbtc.png"),
]
SOURCE = {
    8453: [(N, "ETH", "Ether", 18, "crypto", "eth.png"), ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "USDC", "USD Coin", 6, "stable", "usdc.png"),
           ("0xfde4c96c8593536e31f229ea8f37b2ada2699bb2", "USDT", "Tether USD", 6, "stable", "usdt.png"),
           ("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "cbBTC", "Coinbase Wrapped BTC", 8, "crypto", "cbbtc.png")],
    42161: [(N, "ETH", "Ether", 18, "crypto", "eth.png"), ("0xaf88d065e77c8cc2239327c5edb3a432268e5831", "USDC", "USD Coin", 6, "stable", "usdc.png"),
            ("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", "USDT", "Tether USD", 6, "stable", "usdt.png")],
    1: [(N, "ETH", "Ether", 18, "crypto", "eth.png"), ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", "USD Coin", 6, "stable", "usdc.png"),
        ("0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", "Tether USD", 6, "stable", "usdt.png"),
        ("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "cbBTC", "Coinbase Wrapped BTC", 8, "crypto", "cbbtc.png")],
    10: [(N, "ETH", "Ether", 18, "crypto", "eth.png"), ("0x0b2c639c533813f4aa9d7837caf62653d097ff85", "USDC", "USD Coin", 6, "stable", "usdc.png"),
         ("0x94b008aa00579c1307b0ef2c499ad98a8ce58e58", "USDT", "Tether USD", 6, "stable", "usdt.png")],
    56: [(N, "BNB", "BNB", 18, "crypto", "bnb.png"), ("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "USDC", "USD Coin", 18, "stable", "usdc.png"),
         ("0x55d398326f99059ff775485246999027b3197955", "USDT", "Tether USD", 18, "stable", "usdt.png")],
    137: [(N, "POL", "Polygon", 18, "crypto", "pol.png"), ("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", "USDC", "USD Coin", 6, "stable", "usdc.png"),
          ("0xc2132d05d31c914a87c6611c10748aeb04b58e8f", "USDT", "Tether USD", 6, "stable", "usdt.png")],
    C.SOLANA: [("11111111111111111111111111111111", "SOL", "Solana", 9, "crypto", "sol.png"),
               ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "USDC", "USD Coin", 6, "stable", "usdc.png"),
               ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "USDT", "Tether USD", 6, "stable", "usdt.png")],
}
_ext_px: dict[int, tuple[float, dict]] = {}
_lock = threading.Lock()
_pc = {"at": 0.0, "rows": None}
PRICE_PERIOD_STATIC_S = 300      # the six networks people use most: every 5 min
PRICE_PERIOD_OTHER_S = 1800      # every other Pay-from network: every 30 min (one provider list call each, spread out)


def _logo_ok(rel: str) -> str | None:
    return L + rel if os.path.exists(os.path.join(C.WEB, "assets", "logos", rel)) else None


def local_token_logo(sym: str) -> str | None:
    f = LOCAL_TOKEN_LOGO.get((sym or "").upper())
    return ("tokens/" + f) if f and os.path.exists(os.path.join(C.WEB, "assets", "logos", "tokens", f)) else None


def _src_logo(src: str | None, sub: str) -> str | None:
    """measured logo source -> what the page gets: a local file, or our own /api/img/<id> (never an outside URL)"""
    if not src:
        return None
    if src.startswith("local:"):
        rel = src[6:]
        return _logo_ok(rel if "/" in rel else f"{sub}/{rel}")
    import venues as V
    if V.FORBIDDEN.search(src) or src.lower().split("?")[0].endswith(".svg"):
        return None
    import tokenmeta
    return tokenmeta.img_url(src)


def _measured() -> dict[int, dict]:
    import reach
    m = reach.load()
    return {r["id"]: r for r in (m or {}).get("chains") or []} if m else {}


# the route contract of the six built-in source networks (= the sign layer's pinned list): never null before the first measurement (v4 M12)
STATIC_ROUTE = {i: "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae" for i in (8453, 42161, 1, 10, 56, 137)}


def pay_chains() -> list[dict]:
    """Pay-from networks (= /api/tokens chains[] = /api/reach chains.list): Robinhood Chain first, the six checked networks, then every
    measured one by the size of its connection into 4663. Until the first measurement: the static seven. Cached 60 s."""
    now = time.time()
    with _lock:
        if _pc["rows"] is not None and now - _pc["at"] < 60:
            return _pc["rows"]
    import reach
    has = reach.load() is not None
    meas = _measured()
    out = []
    for c in STATIC_CHAINS:
        if c["id"] not in C.PAY_CHAIN_IDS or (c["id"] != C.CHAIN and has and c["id"] not in meas):
            continue                                     # no longer connected into 4663 -> not offered
        out.append({**c, "logo": _logo_ok(c["logo"]) if c["logo"] else None, "routeContract": C.ROUTER_DIAMOND if c["id"] == C.CHAIN else ((meas.get(c["id"]) or {}).get("routeContract") or STATIC_ROUTE.get(c["id"])),
                    "measured": c["id"] == C.CHAIN or c["id"] in meas})
    for r in sorted((r for r in meas.values() if r["id"] not in STATIC_BY_ID and r["id"] in C.PAY_CHAIN_IDS), key=lambda r: (-(r.get("fromTokens") or 0), r["id"])):
        import local_logos as LL
        out.append({"id": r["id"], "key": r["key"], "name": r["name"].removesuffix(" Mainnet") or r["name"], "logo": LL.path("chains", r["id"]) or _src_logo(r.get("logoSrc"), "chains"),
                    "native": r["native"],
                    "rpc": r["rpc"], "explorer": r["explorer"], "routeContract": r.get("routeContract"), "measured": True})
    with _lock:
        _pc.update(at=now, rows=out)
    return out


def chain_info(cid: int) -> dict | None:
    return next((c for c in pay_chains() if c["id"] == int(cid)), None)


def _chain_tokens(cid: int) -> list[tuple]:
    """(address, symbol, name, decimals, kind, logo) rows of one Pay-from network (4663 excluded)"""
    if cid in SOURCE and cid in STATIC_BY_ID:
        return [(a, sym, name, dec, kind, _logo_ok("tokens/" + logo)) for a, sym, name, dec, kind, logo in SOURCE[cid]]
    r = _measured().get(cid)
    if not r:
        return []
    return [(t["a"], t["s"], t["n"], t["d"], t["k"], src_token_logo(cid, t["a"], t.get("logoSrc"))) for t in r.get("tokens") or []]


def src_token_logo(cid: int, addr: str, src: str | None) -> str | None:
    """a token of another Pay-from network: a local file of ours, else the picture baked into the build (web/assets/logos/src/
    <chainId>-<address>.<ext>?v=, tools/bake_logos.py), else null — never an /api/img id (prod audit 1: outside hosts do not answer
    the engine there)"""
    lp = _src_logo(src, "tokens") if src and src.startswith("local:") else None
    if lp:
        return lp
    import local_logos as LL
    return LL.path("src", f"{int(cid)}-{addr.lower()}")


def _chain_prices(chain: int) -> dict:
    """Last known prices of a source chain — never fetched on a request (audit v1 P1-3); loop() refreshes them in the background."""
    with _lock:
        hit = _ext_px.get(chain)
    return hit[1] if hit else {}


def _fetch_chain_prices(chain: int) -> dict:
    with _lock:
        hit = _ext_px.get(chain)
    import router
    want = {(a.lower() if a.startswith("0x") else a) for a, *_ in _chain_tokens(chain)} or None
    try:
        j = router.tokens([chain], prio="board")
        px = {}
        for t in (j.get("tokens") or {}).get(str(chain), []):
            a = t["address"].lower() if t["address"].startswith("0x") else t["address"]
            if want is not None and a not in want:
                continue                                   # keep only what we list: 50 networks x thousands of tokens is not needed
            try:
                px[a] = float(t.get("priceUSD") or 0)
            except ValueError:
                pass
    except Exception:
        px = hit[1] if hit else {}
    with _lock:
        _ext_px[chain] = (time.time(), px)
    return px


def index(with_prices: bool = True) -> dict:
    out = []
    for s in M.STOCKS:
        logo = _logo_ok(f"stocks/{s['symbol']}.png")
        a = s["address"].lower()
        row = {"c": C.CHAIN, "a": a, "s": s["symbol"], "n": s["name"], "d": 18, "k": s["kind"] if s["kind"] == "etf" else "stock",
               "l": logo, "p": M.price(a)}
        if not logo:
            row["needsManualLogo"] = True
        out.append(row)
    for a, sym, name, dec, kind, logo in MAIN_4663:
        out.append({"c": C.CHAIN, "a": a, "s": sym, "n": name, "d": dec, "k": kind, "l": _logo_ok("tokens/" + logo), "p": M.price(a)})
    chains = pay_chains()
    for ch in chains:
        if ch["id"] == C.CHAIN:
            continue
        px = _chain_prices(ch["id"]) if with_prices else {}
        for a, sym, name, dec, kind, logo in _chain_tokens(ch["id"]):
            out.append({"c": ch["id"], "a": a, "s": sym, "n": name, "d": dec, "k": kind, "l": logo,
                        "p": px.get(a.lower() if a.startswith("0x") else a) or None})
    pub = [{k: v for k, v in c.items() if k != "measured"} for c in chains]
    return {"fields": {"c": "chainId", "a": "address (EVM lowercase)", "s": "symbol", "n": "name", "d": "decimals",
                       "k": "kind stock|etf|crypto|stable", "l": "logo path (local file or /api/img/<id>) or null", "p": "USD price or null"},
            "chains": pub, "tokens": out, "pricesAt": int(M.STATE["prices_at"]), "stale": M.stale()["prices"]}


def find_listed(chain: int, addr: str) -> dict | None:
    """Only our own list (stocks + main 4663 tokens): what a pasted address is checked against."""
    a = addr.lower()
    s = M.STOCK_BY_ADDR.get(a)
    if s:
        return {"symbol": s["symbol"], "name": s["name"], "decimals": 18, "kind": s["kind"] if s["kind"] == "etf" else "stock",
                "logo": _logo_ok(f"stocks/{s['symbol']}.png")}
    for x in MAIN_4663:
        if x[0] == a:
            return {"symbol": x[1], "name": x[2], "decimals": x[3], "kind": x[4], "logo": _logo_ok("tokens/" + x[5])}
    return None


def find(chain: int, addr: str) -> dict | None:
    a = addr.lower() if addr.startswith("0x") else addr
    if chain == C.CHAIN:
        s = M.STOCK_BY_ADDR.get(a)
        if s:
            return {"symbol": s["symbol"], "name": s["name"], "decimals": 18, "kind": s["kind"] if s["kind"] == "etf" else "stock",
                    "logo": _logo_ok(f"stocks/{s['symbol']}.png")}
        for x in MAIN_4663:
            if x[0] == a:
                return {"symbol": x[1], "name": x[2], "decimals": x[3], "kind": x[4], "logo": _logo_ok("tokens/" + x[5])}
        m = M.STATE.get("token_meta", {}).get(a)
        if m:
            return {"symbol": m["symbol"], "name": m["name"], "decimals": m["decimals"], "kind": "crypto", "logo": None}
        import network_tokens as NT                  # «Tokens» section: decimals known, quotable without a lookup first
        n = NT.find(a)
        if n:
            return {"symbol": n["symbol"], "name": n["name"], "decimals": n["decimals"], "kind": "token", "logo": n["logo"]}
        import tokenmeta                                   # a pasted address someone already looked up (any CA)
        c = tokenmeta.cached(a)
        if c:
            return {"symbol": c["symbol"], "name": c["name"], "decimals": c["decimals"], "kind": "custom", "logo": c.get("logo"), "listed": False}
        return None
    for x in SOURCE.get(chain, []):                        # the static seven (+ Solana: quotes from Solana stay possible through the API)
        if (x[0].lower() if x[0].startswith("0x") else x[0]) == a:
            return {"symbol": x[1], "name": x[2], "decimals": x[3], "kind": x[4], "logo": _logo_ok("tokens/" + x[5])}
    for t in (_measured().get(chain) or {}).get("tokens") or []:
        if t["a"] == a:
            return {"symbol": t["s"], "name": t["n"], "decimals": t["d"], "kind": t["k"], "logo": src_token_logo(chain, t["a"], t.get("logoSrc"))}
    return None


def loop(stop):
    """Pay-from prices in the background: the six checked networks every 5 min, every other one every 30 min; one provider call per tick
    (15 s), always the most overdue network."""
    last: dict[int, float] = {}
    while not stop.is_set():
        try:
            ids = [c["id"] for c in pay_chains() if c["id"] != C.CHAIN]
        except Exception:
            ids = [c["id"] for c in STATIC_CHAINS if c["id"] != C.CHAIN]
        now = time.time()
        due = sorted(((last.get(i, 0) + (PRICE_PERIOD_STATIC_S if i in STATIC_BY_ID else PRICE_PERIOD_OTHER_S), i) for i in ids))
        if due and due[0][0] <= now:
            cid = due[0][1]
            try:
                _fetch_chain_prices(cid)
            except Exception:
                pass
            last[cid] = time.time()
        stop.wait(15)


def prices_at() -> int:
    with _lock:
        return int(min((v[0] for v in _ext_px.values()), default=0))


def ext_price(chain: int, addr: str) -> float | None:
    if chain == C.CHAIN:
        return M.price(addr)
    a = addr.lower() if addr.startswith("0x") else addr
    return _chain_prices(chain).get(a) or None


def rpc_hosts() -> list[str]:
    """https origins of every Pay-from network's public RPC (the page reads balances there): the CSP connect-src list."""
    out = []
    for c in pay_chains():
        for u in c.get("rpc") or []:
            o = "https://" + u[8:].split("/", 1)[0] if u.startswith("https://") else None
            if o and o not in out:
                out.append(o)
    return out
