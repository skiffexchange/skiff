"""rhdex web server: public JSON (no sign-in), signed-in JSON, SSE quotes, and the static front end (site/web/) with clean URLs.

Public:  /healthz  /api/config  /api/quote  /api/quote/stream (SSE)  /api/build (POST)  /api/status  /api/track  /api/reach  /api/tokens
         /api/venues  /api/board  /api/trades  /api/stats  /api/token/{addr}  /api/img/{id}
Vault:   /token  /api/vault  /api/vault/check?address=  (only with TOKEN_PAGE=1; otherwise all three are a plain 404)
Signed:  /api/me  /api/me/watchlist  /api/me/alerts[/{id}]  /api/me/alerts/feed  /api/me/history  /api/me/telegram[/link]
Dev:     /api/dev/tg/start (DEV=1 + 127.0.0.1 only)
Every JSON body passes venues.sanitize (hidden names are scrubbed) and a final string check."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

logging.basicConfig(level=os.environ.get("RHDEX_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
import guard  # noqa: E402,F401

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

import auth  # noqa: E402
import board  # noqa: E402
import build as BLD  # noqa: E402
import chain  # noqa: E402
import config as C  # noqa: E402
import market as M  # noqa: E402
import quote as Q  # noqa: E402
import reach  # noqa: E402
import replay as RP  # noqa: E402
import router  # noqa: E402
import store as S  # noqa: E402
import tg  # noqa: E402
import tokens as TK  # noqa: E402
import network_tokens as NT  # noqa: E402
import track as TRK  # noqa: E402
import trades as TR  # noqa: E402
import pricehist as PH
import users as U  # noqa: E402
import venues as V  # noqa: E402
import vault as VLT  # noqa: E402

log = logging.getLogger("rhdex")
STOP = threading.Event()
STARTED = time.time()
THREADS: dict[str, threading.Thread] = {}


def _start(name, fn):
    t = threading.Thread(target=fn, args=(STOP,), name=name, daemon=True)
    t.start()
    THREADS[name] = t


@asynccontextmanager
async def lifespan(_app):
    S.db()
    _start("market", M.loop)
    _start("prices", TK.loop)
    if C.flag("TRADES"):
        if C.ALERTS_ENABLED:                           # alerts are off since 16.11 (owner)
            TR.listeners.append(U.on_trade)
        _start("trades", TR.loop)
        _start("pricehist", PH.loop)               # /stock chart: ~25 h of the price pools' Swap logs when the history is short
        # tokhist (the /tokens sparklines) is off since 17.11: the /tokens page is gone (owner), nothing reads it — no RPC spent
    if not C.routing_enabled():
        log.warning("ROUTE_API_URL is empty: routing is unavailable (quotes and builds answer routing_unavailable; board and reach are off)")
    if C.flag("BOARD") and not RP.enabled() and C.routing_enabled():        # replay mode spends no router budget in the background
        _start("board", board.loop)
    if C.flag("NTOKENS") and not RP.enabled():      # «Tokens» list (10 min): DexScreener + the provider's token list; replay reads the saved one
        _start("ntokens", NT.loop)
    if C.flag("REACH") and not RP.enabled() and C.routing_enabled():        # /api/reach measurement (24 h); a replay engine reads the saved one from the shared meta
        _start("reach", reach.loop)
    if C.TOKEN_PAGE and C.TOKEN_CA:                  # Vault: find (or read back) the launch time once, off the request path
        threading.Thread(target=VLT.launch, name="vault-launch", daemon=True).start()
    if tg.enabled() and tg.token():                   # Telegram is off until TG_ENABLED=1 
        _start("tg", tg.poll_loop)
    yield
    STOP.set()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

_RPC_STATIC = ("https://rpc.mainnet.chain.robinhood.com https://mainnet.base.org https://arb1.arbitrum.io https://ethereum-rpc.publicnode.com "
               "https://mainnet.optimism.io https://bsc-dataseed.bnbchain.org https://polygon-bor-rpc.publicnode.com")
_csp = {"at": 0.0, "v": None}


def csp() -> str:
    """The CSP with connect-src = Privy / WalletConnect + the public RPC of every Pay-from network (the page reads balances there;
    tokens.rpc_hosts, measured list). Rebuilt at most once a minute."""
    now = time.time()
    if _csp["v"] and now - _csp["at"] < 60:
        return _csp["v"]
    try:
        hosts = [h for h in TK.rpc_hosts() if re.fullmatch(r"https://[a-z0-9.-]+", h)]
    except Exception:
        hosts = []
    extra = " ".join(h for h in hosts if h not in _RPC_STATIC.split())
    v = SEC_HEADERS["content-security-policy"].replace(_RPC_STATIC, _RPC_STATIC + (" " + extra if extra else ""))
    _csp.update(at=now, v=v)
    return v


SEC_HEADERS = {
    # audit v1 P1-6: no framing (clickjacking on a page that builds transactions to sign), no MIME sniffing, no full referrer outward.
    # audit v2 S3: strict CSP. Hosts: Privy + WalletConnect (from web/assets/vendor/privy.js, prod.md) and the chain RPCs the signer uses
    # (web/assets/js/sign/chains.js). No inline scripts on any page (each page = one module <script src>).
    "content-security-policy": "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self' https://fonts.reown.com",
        "connect-src 'self' https://auth.privy.io https://*.walletconnect.org wss://relay.walletconnect.org https://*.walletconnect.com "
        "wss://relay.walletconnect.com https://pulse.walletconnect.org wss://www.walletlink.org https://www.walletlink.org "   # audit v4 C5: Coinbase Wallet
        "https://rpc.mainnet.chain.robinhood.com https://mainnet.base.org "
        "https://arb1.arbitrum.io https://ethereum-rpc.publicnode.com https://mainnet.optimism.io https://bsc-dataseed.bnbchain.org "
        "https://polygon-bor-rpc.publicnode.com",
        "frame-src https://auth.privy.io https://verify.walletconnect.org https://verify.walletconnect.com https://secure.walletconnect.org",
        "frame-ancestors 'none'", "base-uri 'self'", "object-src 'none'", "form-action 'self'"]),
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


MAX_BODY = 64 * 1024


@app.middleware("http")
async def body_limit(request: Request, call_next):
    """audit v2 minor 8: POST/PUT bodies over 64 KB are refused before they are read"""
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY:
                return err("too_large", 413, maxBytes=MAX_BODY)
        except ValueError:
            return err("bad_request", 400)
        if "chunked" in (request.headers.get("transfer-encoding") or "").lower():
            return err("length_required", 411)
    return await call_next(request)


@app.middleware("http")
async def www_to_apex(request: Request, call_next):
    """prod: www.<domain> → 301 to the apex SITE_URL, path and query kept (owner domain skiff.exchange)"""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if guard.is_prod() and host.startswith("www."):
        site = C.SITE_URL.rstrip("/")
        if site.startswith("https://") and host[4:] == site[8:].split("/")[0].lower():
            q = ("?" + request.url.query) if request.url.query else ""
            return Response(status_code=301, headers={"location": site + request.url.path + q, "cache-control": "no-store"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SEC_HEADERS.items():
        resp.headers.setdefault(k, csp() if k == "content-security-policy" else v)
    if guard.is_prod():
        resp.headers.setdefault("strict-transport-security", "max-age=31536000; includeSubDomains")
    return resp


# ------------------------------------------------------------------ helpers
def J(obj, status: int = 200, headers: dict | None = None) -> Response:
    body = json.dumps(V.sanitize(obj), separators=(",", ":"), default=str)
    if V.FORBIDDEN.search(body):                         # never: sanitize covers keys and strings; belt and braces
        log.error("forbidden string in a response, scrubbed")
        body = V.FORBIDDEN.sub("route", body)
    h = {"cache-control": "no-store"}
    h.update(headers or {})
    return Response(body, status_code=status, media_type="application/json", headers=h)


def err(code: str, status: int, **extra) -> Response:
    return J({"error": code, **extra}, status)


_rl: dict[tuple[str, str], deque] = {}
_rl_lock = threading.Lock()
LIMITS = {"quote": (40, 60), "build": (12, 60), "me": (120, 60), "pub": (240, 60), "status": (60, 60), "lookup": (20, 60), "track": (90, 60),
          "trackx": (20, 60)}   # audit v4 M21: a cross-chain track asks the route provider (cached 6 s per tx) — 20/min per IP


def client_ip(req: Request) -> str:
    if guard.on_railway():
        # Railway's edge sets X-Real-IP to the real client; X-Forwarded-For / Fly-Client-IP can be supplied by the client there
        h = (req.headers.get("x-real-ip") or "").strip()
        if h:
            return h
    elif (os.environ.get("RHDEX_TRUST_PROXY") or "") == "1":
        h = req.headers.get("fly-client-ip") or (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if h:
            return h
    return req.client.host if req.client else "?"


def limited(req: Request, cls: str) -> Response | None:
    n, win = LIMITS[cls]
    ip = client_ip(req)
    now = time.time()
    with _rl_lock:
        dq = _rl.setdefault((ip, cls), deque())
        while dq and now - dq[0] > win:
            dq.popleft()
        if len(dq) >= n:
            return err("rate_limited", 429, retryInS=int(win - (now - dq[0])) + 1)
        dq.append(now)
        if len(_rl) > 50_000:                        # audit v2 minor 10: evict the oldest keys, never reset everyone's limits
            for k in sorted(_rl, key=lambda k: _rl[k][-1] if _rl[k] else 0)[:10_000]:
                _rl.pop(k, None)
    return None


def wallet(req: Request):
    try:
        return auth.address_of(req.headers, req.client.host if req.client else None), None
    except auth.Unavailable:
        return None, err("auth_unavailable", 503)


def need_wallet(req: Request):
    w, e = wallet(req)
    if e:
        return None, e
    if not w:
        return None, err(auth.missing_reason(req.headers) or "sign_in_required", 401)
    return w, None


# ------------------------------------------------------------------ health + config
def _degraded() -> dict:
    now = time.time()
    rpc_bad = now - (chain.STATS.get("last_ok") or 0) > 60
    bs = router.budget_state()
    router_bad = router.unhealthy() or any(b.get("paused") or b.get("remaining") == 0 for k, b in bs.items() if k in ("routes", "step", "quote"))
    router_bad = router_bad or not C.routing_enabled()
    return {"rpc": rpc_bad, "router": router_bad, "trades": TR.stale() and TR.STATE["backfilled"]}


@app.get("/healthz")
async def healthz(req: Request):
    now = time.time()
    ok = all(t.is_alive() for t in THREADS.values())
    full = ((os.environ.get("DEV") or "") == "1" and auth.local_direct(req.headers, req.client.host if req.client else None)) or (
        (os.environ.get("HEALTHZ_TOKEN") or "") and req.headers.get("x-health-token") == os.environ.get("HEALTHZ_TOKEN"))
    if not full:   # audit v1 P2: the public health check says only what a monitor needs
        return J({"ok": ok, "uptimeS": int(now - STARTED), "degraded": _degraded()}, 200 if ok else 503)
    doc = {"ok": ok, "uptimeS": int(now - STARTED), "threads": {k: t.is_alive() for k, t in THREADS.items()},
           "market": {"pools": len(M.POOLS), "prices": len(M.PRICES), "stale": M.stale()},
           "trades": {"head": TR.STATE["head"], "scannedTo": TR.STATE["scanned_to"], "backfilled": TR.STATE["backfilled"], "stale": TR.stale(),
                      "err": TR.STATE["err"] or None},
           "board": {"lastRun": int(board.STATE["last_run"]), "rowsOk": board.STATE["rows_ok"], "rowsFail": board.STATE["rows_fail"],
                     "lastError": board.STATE["last_err"] or None},
           "reach": reach.health(),
           "routerBudget": router.budget_state(), "routerKey": C.ROUTE_KEYED, "routerConfigured": C.routing_enabled(), "routerStats": router.STATS, "quoteStats": Q.STATS,
           "rpc": {k: v for k, v in chain.STATS.items()}, "dev": (os.environ.get("DEV") or "") == "1", "quoteReplay": RP.enabled(),
           "degraded": _degraded()}
    return J(doc, 200 if ok else 503)


@app.get("/api/config")
def config_(req: Request):
    if (r := limited(req, "pub")):
        return r
    w, _ = wallet(req)
    import copytext
    return J({"brand": copytext.brand(), "siteUrl": C.SITE_URL, "chainId": C.CHAIN, "privyAppId": auth.app_id() or None, "limitsEnabled": C.LIMITS_ENABLED, "ourFeePct": 0,
              "slippage": C.SLIPPAGE, "delegate7702": C.SIMPLE7702, "features": {f: U.access(f, w) for f in U.FEATURES},
              "telegramBot": tg.bot_name() if tg.enabled() else None, "boardSizesUsd": C.BOARD_SIZES_USD, "routingAvailable": C.routing_enabled(), "quoteReplay": RP.enabled()})


# ------------------------------------------------------------------ quotes
def _quote_params(req: Request) -> dict:
    return dict(req.query_params)


def _quote_error(e: Exception) -> Response:
    if isinstance(e, Q.BadRequest):
        return err(e.code, 400, message=str(e))
    if isinstance(e, Q.Busy):
        return err("busy", 429, message="many quotes are computing right now, try again", retryInS=3)
    if isinstance(e, router.Budget):
        return err("busy", 503, message="route provider rate limit; no cached answer for this request", retryInS=e.reset_in)
    if isinstance(e, router.NotConfigured):
        return err("routing_unavailable", 503, message="routing is not configured on this server")
    if isinstance(e, router.Upstream):
        return err("upstream", 502, message=f"route provider error {e.code}")
    if isinstance(e, (Q.ChainUnavailable, asyncio.TimeoutError, TimeoutError)):
        return err("chain_unavailable", 503, message="the network node does not answer, try again", retryInS=10)
    log.warning("quote error: %s", e)
    return err("unavailable", 503, message="quote failed, try again")


@app.get("/api/quote")
async def api_quote(req: Request):
    """audit v2 S1: async — waiting for a quote holds no worker thread; the computation runs in its own job thread."""
    if (r := limited(req, "quote")):
        return r
    if RP.enabled():
        try:
            rec = RP.lookup(Q.normalize(_quote_params(req)))
        except Q.BadRequest as e:
            return err(e.code, 400, message=str(e))
        return J(rec["final"]) if rec else err("no_replay", 404, message="QUOTE_REPLAY: no recording for this pair", available=RP.available())
    try:
        job, res = Q.get(_quote_params(req))
        if res is None:
            res = await asyncio.wait_for(asyncio.wrap_future(job.fut), timeout=Q.QUOTE_DEADLINE_S)
        return J(res)
    except Exception as e:
        return _quote_error(e)


def _sse(ev, data):
    return f"event: {ev}\ndata: {json.dumps(V.sanitize(data), separators=(',', ':'), default=str)}\n\n"


SSE_HEADERS = {"cache-control": "no-store", "x-accel-buffering": "no"}


@app.get("/api/quote/stream")
async def api_quote_stream(req: Request):
    """Async SSE (audit v2 S1): events come through an asyncio queue fed from the job thread; the stream ALWAYS ends with `done` or
    `error` (minor 7), at the latest after QUOTE_DEADLINE_S."""
    if (r := limited(req, "quote")):
        return r
    if RP.enabled():
        try:
            rec = RP.lookup(Q.normalize(_quote_params(req)))
        except Q.BadRequest as e:
            return err(e.code, 400, message=str(e))
        if not rec:
            return err("no_replay", 404, message="QUOTE_REPLAY: no recording for this pair", available=RP.available())

        async def replay_gen():
            last = 0
            for ms, ev, data in rec["timeline"]:
                await asyncio.sleep(max(0, ms - last) / 1000)
                last = ms
                yield _sse(ev, {**data, **rec["mark"]} if ev == "meta" else data)
            await asyncio.sleep(max(0, rec.get("doneMs", last) - last) / 1000)
            yield _sse("done", rec["final"])

        return StreamingResponse(replay_gen(), media_type="text/event-stream", headers=SSE_HEADERS)
    try:
        job, res = Q.get(_quote_params(req))
    except Exception as e:
        return _quote_error(e)

    async def gen():
        if res is not None:
            yield _sse("meta", {"request": res["request"], "block": res["block"], "cached": True})
            for _ms, src in Q.derive_sources(res, None):           # a cached answer: its sources at once (ms 0, derived: true)
                yield _sse("source", src)
            if res.get("baseline") is not None:
                yield _sse("baseline", {"baseline": res["baseline"]})
            for p in res["routes"]:
                yield _sse("route", {"route": p, "group": "routes"})
            for p in res.get("quotedOnly") or []:
                yield _sse("route", {"route": p, "group": "quotedOnly"})
            yield _sse("done", res)
            return
        aq = job.subscribe_async(asyncio.get_running_loop())
        end = time.time() + Q.QUOTE_DEADLINE_S
        while True:
            left = end - time.time()
            if left <= 0:
                yield _sse("error", {"error": "timeout", "retryInS": 10})
                return
            try:
                ev, data = await asyncio.wait_for(aq.get(), timeout=min(10, left))
            except (asyncio.TimeoutError, TimeoutError):
                yield ": keep-alive\n\n"
                continue
            if ev == "_end":
                break
            yield _sse(ev, data)
        try:
            yield _sse("done", await asyncio.wait_for(asyncio.wrap_future(job.fut), timeout=5))
        except router.Budget as e:
            yield _sse("error", {"error": "busy", "retryInS": e.reset_in})
        except Q.BadRequest as e:
            yield _sse("error", {"error": e.code, "message": str(e)})
        except (Q.ChainUnavailable, asyncio.TimeoutError, TimeoutError):
            yield _sse("error", {"error": "chain_unavailable", "retryInS": 10})
        except Exception:
            yield _sse("error", {"error": "unavailable"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/build")
async def api_build(req: Request):
    if (r := limited(req, "build")):
        return r
    try:
        body = await req.json()
        assert isinstance(body, dict)
    except Exception:
        return err("bad_request", 400, message="JSON body required")
    import anyio
    try:
        return J(await anyio.to_thread.run_sync(BLD.build, body))
    except BLD.Refuse as e:
        return err(e.code, e.status, **e.extra)
    except Q.BadRequest as e:
        return err(e.code, 400, message=str(e))
    except Exception as e:
        log.warning("build error: %s", e, exc_info=True)
        return err("unavailable", 503)


@app.get("/api/status")
def api_status(req: Request, txHash: str = "", fromChain: int = C.CHAIN, toChain: int = C.CHAIN, venue: str | None = None):
    if (r := limited(req, "status")):
        return r
    if not txHash or len(txHash) > 100:
        return err("bad_request", 400, message="txHash required")
    try:
        return J(BLD.status(txHash, fromChain, toChain, venue))
    except Exception:
        return J({"status": "UNKNOWN", "stale": True})


@app.get("/api/track")
async def api_track(req: Request):
    """Live route map after Swap: nodes light up on real confirmations (receipts, block times, the provider's bridge status)."""
    cross = str(req.query_params.get("fromChain") or str(C.CHAIN)).strip() not in ("", str(C.CHAIN))
    if (r := limited(req, "trackx" if cross else "track")):
        return r
    import anyio
    try:
        return J(await anyio.to_thread.run_sync(TRK.track, dict(req.query_params)))
    except TRK.Bad as e:
        return err("bad_request", 400, message=str(e))
    except Exception as e:
        log.warning("track error: %s", e)
        return err("chain_unavailable", 503, retryInS=5)


@app.get("/api/network-tokens")
def api_network_tokens(req: Request):
    """«Tokens» next to Stocks: ETH / WETH / USDG / cbBTC (listed) + the live network tokens with real liquidity (rule in the answer)."""
    if (r := limited(req, "pub")):
        return r
    return J(NT.doc(), headers={"cache-control": "public, max-age=300"})


@app.get("/api/reach")
def api_reach(req: Request):
    """Measured counters for the landing: Pay-from networks, bridges, dexes, makers, aggregators, sources; ourFeePct 0."""
    if (r := limited(req, "pub")):
        return r
    return J(reach.doc(), headers={"cache-control": "public, max-age=300"})


# ------------------------------------------------------------------ catalog + live data
@app.get("/api/tokens")
def api_tokens(req: Request):
    if (r := limited(req, "pub")):
        return r
    return J(TK.index(), headers={"cache-control": "public, max-age=60"})


@app.get("/api/token/{addr}")
def api_token(req: Request, addr: str):
    """A pasted address on 4663 (any CA): our record if listed, else symbol / name / decimals / picture from the chain."""
    if (r := limited(req, "lookup")):
        return r
    import lookalike as LK
    import tokenmeta
    try:
        doc = tokenmeta.lookup(addr)
        if not doc.get("listed"):
            n = NT.find(doc["address"])      # a token of the «Tokens» list: the same record there and here (audit v5 works minor 8)
            if n:
                doc = {**doc, **{k: n[k] for k in ("symbol", "name", "decimals", "logo", "kind")}}
            elif doc.get("logo") and not NT._img_ok(doc["logo"]):
                doc = {**doc, "logo": None}  # never an /api/img id that does not load
            lk = LK.check(doc.get("symbol") or "", doc.get("name") or "")
            doc = {**doc, "lookalike": lk["lookalike"], "clash": bool(doc.get("clash")) or lk["why"] == "our_stock_ticker"}
        return J(doc, headers={"cache-control": "public, max-age=300"})
    except ValueError:
        return err("bad_address", 400)
    except tokenmeta.NotToken:
        if req.query_params.get("soft") == "1":   # audit v4 works M4: a pasted wallet address is an answer, not a red 404 in the console
            return J({"ok": False, "code": "not_a_token", "address": addr.strip().lower()}, headers={"cache-control": "public, max-age=300"})
        return err("not_a_token", 404)
    except tokenmeta.ChainDown:
        return err("chain_unavailable", 503, retryInS=10)


@app.get("/api/img/{iid}")
def api_img(req: Request, iid: str):
    """A token picture through our own address (never an outside URL on the page): type, magic bytes and size checked, cached on disk."""
    if (r := limited(req, "pub")):
        return r
    import tokenmeta
    got = tokenmeta.fetch_image(iid)
    if not got:
        return Response(status_code=404, headers={"cache-control": "no-store"})
    return Response(content=got[0], media_type=got[1], headers={"cache-control": "public, max-age=86400",
                    "content-security-policy": "default-src 'none'; sandbox", "x-content-type-options": "nosniff"})


@app.get("/api/venues")
def api_venues(req: Request):
    if (r := limited(req, "pub")):
        return r
    dexes = [{**V.dex(k), "kind": "dex"} for k in V.DEXES]
    return J({"tools": V.table(), "dexes": dexes}, headers={"cache-control": "public, max-age=300"})


@app.get("/api/board")
def api_board(req: Request):
    if (r := limited(req, "pub")):
        return r
    if RP.enabled():
        return J(RP.board_rows(C.BOARD_STOCKS, C.BOARD_SIZES_USD))
    return J(board.get())


@app.get("/api/trades")
def api_trades(req: Request, limit: int = 50, before: int | None = None, stock: str | None = None, minUsd: float | None = None):
    if (r := limited(req, "pub")):
        return r
    limit = max(1, min(200, limit))
    return J({"trades": TR.feed(limit, before, stock.upper() if stock else None, minUsd), "minUsd": C.TRADE_MIN_USD,
              "stale": TR.stale(), "backfilled": TR.STATE["backfilled"], "scannedTo": TR.STATE["scanned_to"], "at": int(time.time())})


@app.get("/api/price-history")
def api_price_history(req: Request, stock: str = ""):
    if (r := limited(req, "pub")):
        return r
    d = PH.doc(stock.upper().strip())
    if d is None:
        return JSONResponse({"error": "unknown_stock"}, status_code=404)
    return JSONResponse(d, headers={"cache-control": "public, max-age=60"})


@app.get("/api/stocks-overview")
def api_stocks_overview(req: Request):
    """the /stocks table: per stock price, 24 h change + sparkline, liquidity, pools / venues, volume and big buys 24 h (pricehist.overview)"""
    if (r := limited(req, "pub")):
        return r
    return J(PH.overview(), headers={"cache-control": "public, max-age=60"})


@app.get("/api/stats")
def api_stats(req: Request):
    if (r := limited(req, "pub")):
        return r
    st = TR.stats()
    tracked = sorted({p["dex"] for p in M.stock_pools()})
    st.update({"venues": {"dexes": len(tracked), "dexList": [V.dex(d) for d in tracked],
                          "routeVenues": len([t for t in V.TOOLS.values() if t[3] in ("aggregator", "own-router-tool")])},
               "trackedPools": len(M.stock_pools()), "stocks": len(M.STOCKS), "stale": TR.stale() or not TR.STATE["backfilled"],
               "backfilled": TR.STATE["backfilled"], "at": int(time.time())})
    return J(st)


# ------------------------------------------------------------------ signed-in
@app.get("/api/me")
def me(req: Request):
    if (r := limited(req, "me")):
        return r
    w, e = wallet(req)
    if e:
        return e
    if not w:
        return J({"signedIn": False})
    me = {"signedIn": True, "wallet": w, "features": {f: U.access(f, w) for f in U.FEATURES}}
    if tg.enabled():
        me["telegram"] = tg.status(w)
    return J(me)


@app.get("/api/me/watchlist")
def me_watch(req: Request):
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J({"tickers": U.watchlist(w)})


@app.put("/api/me/watchlist")
async def me_watch_put(req: Request):
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    if e:
        return e
    try:
        body = await req.json()
        return J({"tickers": U.watch_set(w, list(body.get("tickers") or []))})
    except (ValueError, AttributeError, TypeError) as x:
        return err("bad_request", 400, message=str(x))


@app.get("/api/me/alerts")
def me_alerts(req: Request):
    if not C.ALERTS_ENABLED:
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J({"alerts": U.alerts(w), "minUsdFloor": C.TRADE_MIN_USD})


@app.post("/api/me/alerts")
async def me_alerts_post(req: Request):
    if not C.ALERTS_ENABLED:
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    if e:
        return e
    try:
        b = await req.json()
        return J(U.alert_put(w, b.get("ticker"), float(b.get("minUsd")), bool(b.get("telegram", True)) and tg.enabled()))
    except (ValueError, AttributeError, TypeError) as x:
        return err("bad_request", 400, message=str(x))


@app.delete("/api/me/alerts/{aid}")
def me_alerts_del(req: Request, aid: int):
    if not C.ALERTS_ENABLED:
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J({"deleted": U.alert_del(w, aid)})


@app.get("/api/me/alerts/feed")
def me_alert_feed(req: Request):
    if not C.ALERTS_ENABLED:
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J({"hits": U.hits(w)})


@app.get("/api/me/history")
def me_hist(req: Request):
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J({"swaps": U.history(w)})


@app.post("/api/me/history")
async def me_hist_post(req: Request):
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    if e:
        return e
    try:
        b = await req.json()
        import anyio
        doc = await anyio.to_thread.run_sync(U.history_add, w, str(b.get("tx") or ""), int(b.get("chainId") or C.CHAIN))
        return J(doc)
    except U.ChainDown:
        return err("chain_unavailable", 503, retryInS=10)
    except (ValueError, AttributeError, TypeError) as x:
        return err("bad_request", 400, message=str(x))


@app.get("/api/me/telegram")
def me_tg(req: Request):
    if not tg.enabled():
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J(tg.status(w))


@app.post("/api/me/telegram/link")
def me_tg_link(req: Request):
    if not tg.enabled():
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    return e or J(tg.link(w))


@app.delete("/api/me/telegram")
def me_tg_del(req: Request):
    if not tg.enabled():
        return err("not_found", 404)
    if (r := limited(req, "me")):
        return r
    w, e = need_wallet(req)
    if e:
        return e
    tg.unlink(w)
    return J({"linked": False})


@app.post("/api/dev/tg/start")
async def dev_tg_start(req: Request):
    """DEV only: emulate Telegram's /start <code> from a chat (fake transport)."""
    if not tg.enabled() or (os.environ.get("DEV") or "") != "1" or (req.client and req.client.host not in ("127.0.0.1", "::1")):
        return err("not_found", 404)
    b = await req.json()
    if b.get("text"):                                    # any bot message: /start, /stop, /help, free text
        tg.command(int(b.get("chatId") or 0), str(b["text"]))
        return J({"ok": True, "outbox": S.q("SELECT text FROM tg_outbox WHERE chat_id=? ORDER BY id DESC LIMIT 1", (int(b.get("chatId") or 0),))})
    return J({"linked": tg.handle_start(int(b.get("chatId") or 0), str(b.get("code") or ""))})


# ------------------------------------------------------------------ the token page (Vault): TOKEN_PAGE=1 only (owner decides when)
@app.get("/api/vault")
def api_vault(req: Request):
    """{ticker, ca, vault, launched, holdings, totalUsd, log, nextDrop}: holdings are the vault's real balances of our listed tokens at
    the engine price; log is [] until the distribution engine exists (later step)."""
    if not C.TOKEN_PAGE:
        return err("not_found", 404)
    if (r := limited(req, "pub")):
        return r
    return J(VLT.doc(), headers={"cache-control": "public, max-age=30"})


@app.get("/api/vault/check")
def api_vault_check(req: Request, address: str = ""):
    if not C.TOKEN_PAGE:
        return err("not_found", 404)
    if (r := limited(req, "pub")):
        return r
    try:
        return J(VLT.check(address), headers={"cache-control": "public, max-age=60"})
    except ValueError:
        return err("bad_address", 400)


@app.get("/api/limits")
def limits_stub(req: Request):
    return J({"enabled": C.LIMITS_ENABLED, "message": "limit orders are not available yet"}, 200)


# ------------------------------------------------------------------ static front end
PLACEHOLDER = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{brand} engine</title><style>body{font:15px/1.5 system-ui;background:#0b0d12;color:#e8ecf3;margin:0;padding:40px 16px}main{max-width:640px;margin:auto}
a{color:#9fb4ff}code{background:#1c1f26;padding:2px 6px;border-radius:4px}</style></head><body><main><h1>Engine is running</h1>
<p>The front end is not built yet (site/web/). API: <a href="/api/board">/api/board</a> · <a href="/api/trades">/api/trades</a> ·
<a href="/api/stats">/api/stats</a> · <a href="/api/tokens">/api/tokens</a> · <a href="/api/venues">/api/venues</a> · <a href="/healthz">/healthz</a></p>
</main></body></html>"""
PAGES = {"/": "index", "/app": "app", "/faq": "faq"}


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*$")


def _page(name: str) -> str | None:
    """audit v2 S2: a page file strictly inside web/ — safe characters only, then realpath + prefix check."""
    if name and (not _SAFE_NAME.match(name) or ".." in name):
        return None
    root = os.path.realpath(C.WEB) + os.sep
    for p in (os.path.join(C.WEB, name + ".html"), os.path.join(C.WEB, name, "index.html")):
        rp = os.path.realpath(p)
        if rp.startswith(root) and os.path.isfile(rp):
            return rp
    return None


def _html(path: str, status: int = 200) -> Response:
    if C.TOKEN_PAGE:
        return FileResponse(path, status_code=status, media_type="text/html", headers={"cache-control": "no-store"})
    try:   # Vault off: the same file without its Vault links (cached per file by mtime)
        mt = os.path.getmtime(path)
        c = _STRIPPED.get(path)
        if not c or c[0] != mt:
            c = (mt, _no_vault(open(path, encoding="utf-8").read()))
            _STRIPPED[path] = c
    except OSError:
        return _not_found()
    return HTMLResponse(c[1], status_code=status, headers={"cache-control": "no-store"})


_HASHED = re.compile(r"[.-][0-9a-f]{8,}\.[a-z0-9]+$")


# unversioned files that change with a rebuild while keeping their name: the brand marks (rename Fleet -> Skiff showed the old mark for
# up to an hour) and the Privy bundle. Until the page links them with ?v=, every use revalidates (ETag / Last-Modified -> 304, cheap).
_REVALIDATE = ("/assets/brand/", "/assets/vendor/")


def asset_cache(path: str, query: str) -> str:
    """/assets/*: immutable for a year ONLY when the URL is versioned (?v=… or a content hash in the file name); brand marks and vendor
    bundles without a version: no-cache (always revalidated, audit v4 M9); other unversioned assets 1 h, revalidated. HTML is no-store."""
    if "/assets/" in path and ("v=" in query or _HASHED.search(path)):
        return "public, max-age=31536000, immutable"
    if any(path.startswith(p) for p in _REVALIDATE):
        return "public, no-cache"
    if "/assets/" in path:
        return "public, max-age=3600, must-revalidate"
    return "public, max-age=300"


ALIASES = {"/tokens": "/app", "/swap": "/app",   #  — old links land on the swap
           "/stock": "/stocks", "/stock/index": "/stocks", "/index": "/"}


def canonical(path: str) -> str:
    """audit v2 S6: one address per page — no trailing slash, no .html, aliases folded, tickers upper-case."""
    c = path
    if c != "/" and c.endswith("/"):
        c = c.rstrip("/") or "/"
    if c.endswith(".html"):
        c = c[:-5]
    if c.endswith("/index"):
        c = c[:-6] or "/"
    c = ALIASES.get(c, c)
    m = re.match(r"^/stock/([A-Za-z0-9.]{1,12})$", c)
    if m:
        c = "/stock/" + m.group(1).upper()
    return c or "/"


def _site() -> str:
    return C.SITE_URL.rstrip("/")


@app.get("/robots.txt")
async def robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {_site()}/sitemap.xml\n"
    return Response(body, media_type="text/plain", headers={"cache-control": "public, max-age=3600"})


@app.get("/sitemap.xml")
async def sitemap():
    urls = ["/", "/app", "/stocks", "/faq", "/legal"] + (["/token"] if C.TOKEN_PAGE and VLT.launched() else [])   # Vault: only once indexable
    sd = os.path.join(C.WEB, "stock")
    if os.path.isdir(sd):
        urls += [f"/stock/{f[:-5]}" for f in sorted(os.listdir(sd)) if f.endswith(".html") and f != "index.html"]
    urls = [u for u in urls if u == "/" or _page(u.strip("/")) or u.startswith("/stock/")]
    items = "".join(f"<url><loc>{_site()}{u if u != '/' else '/'}</loc></url>" for u in urls)
    body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(body, media_type="application/xml", headers={"cache-control": "public, max-age=3600"})


_CANON = re.compile(r'<link\b[^>]*\brel=["\']?canonical["\']?[^>]*>\s*', re.I)
_OGURL = re.compile(r'<meta\b[^>]*\bproperty=["\']og:url["\'][^>]*>\s*', re.I)
_404 = {"mtime": None, "body": None}
_TOKEN = {"mtime": None, "body": None}
# «Vault» links (header, mobile menu, footer, dock; built with data-vlink): when TOKEN_PAGE is off the engine strips them from every page
# it serves, so a build that ships with the flag off never links to a 404. ⌘K reads the same links from the page.
_VLINK_LI = re.compile(r'<li><a href="/token"[^>]*\bdata-vlink\b[^>]*>.*?</a></li>', re.S)
_VLINK = re.compile(r'<a href="/token"[^>]*\bdata-vlink\b[^>]*>.*?</a>', re.S)
_STRIPPED: dict[str, tuple[float, str]] = {}


def _no_vault(html: str) -> str:
    return _VLINK.sub("", _VLINK_LI.sub("", html))


def _token_page() -> Response:
    """/token (Vault): only with TOKEN_PAGE=1, else the plain 404. The server sets what the page shows before any script runs:
    data-ca / data-vault / data-drop = 0|1, the CA and the vault address, and every {ticker} slot: «$SKIFF» when TOKEN_TICKER is set,
    else the string token.ticker.fallback («the {brand} token»), so no placeholder ever reaches a reader. noindex (header + meta) until the
    token is launched (TOKEN_CA)."""
    if not C.TOKEN_PAGE:
        return _not_found()
    p = _page("token")
    if not p:
        return _not_found()
    import html as _h
    try:
        mt = os.path.getmtime(p)
        if _TOKEN["mtime"] != mt:
            _TOKEN.update(mtime=mt, body=open(p, encoding="utf-8").read())
    except OSError:
        return _not_found()
    live = VLT.launched()
    body = _TOKEN["body"]
    body = body.replace("{ticker}", _h.escape(VLT.ticker_text()))
    flags = f'data-ca="{int(live)}" data-vault="{int(bool(C.VAULT_ADDRESS))}" data-drop="{int(bool(VLT.next_drop()))}"'
    body = body.replace('data-ca="0" data-vault="0" data-drop="0"', flags, 1)
    body = body.replace("{ca}", C.TOKEN_CA).replace("{vaultaddr}", C.VAULT_ADDRESS)
    if not live:
        body = body.replace("</head>", '<meta name="robots" content="noindex">\n</head>', 1)
    r = HTMLResponse(body, headers={"cache-control": "no-store"})
    if not live:
        r.headers["x-robots-tag"] = "noindex"
    return r


def _not_found() -> Response:
    """404 page; X-Robots-Tag noindex. A 404 has no canonical (audit v3 minor 12, v4 M11): the built 404.html carries one like every
    page, so it is stripped here (and og:url with it) — the file under web/ is the designer's and stays as built."""
    p404 = _page("404")
    if p404:
        try:
            mt = os.path.getmtime(p404)
            if _404["mtime"] != mt:
                html = open(p404, encoding="utf-8").read()
                _404.update(mtime=mt, body=_OGURL.sub("", _CANON.sub("", html)))
            body404 = _404["body"] if C.TOKEN_PAGE else _no_vault(_404["body"])
            r = HTMLResponse(body404, status_code=404, headers={"cache-control": "no-store"})
        except OSError:
            r = HTMLResponse("<!doctype html><title>Not found</title><h1>404</h1>", status_code=404, headers={"cache-control": "no-store"})
    else:
        r = HTMLResponse("<!doctype html><title>Not found</title><h1>404</h1>", status_code=404, headers={"cache-control": "no-store"})
    r.headers["x-robots-tag"] = "noindex"
    return r


@app.get("/favicon.ico")
async def favicon():
    """audit v4 M10: browsers and crawlers ask /favicon.ico — the site's mark is the SVG favicon"""
    return Response(status_code=301, headers={"location": "/assets/brand/favicon.svg", "cache-control": "public, max-age=86400"})


@app.api_route("/{full:path}", methods=["GET", "HEAD"])
async def static(full: str, request: Request):
    path = "/" + full
    if path.startswith("/api/"):
        return err("not_found", 404)
    # a real asset file under web/ (realpath-checked); pages never go through here
    if "." in path.rsplit("/", 1)[-1] and not path.endswith(".html"):
        fp = os.path.realpath(os.path.join(C.WEB, full))
        if fp.startswith(os.path.realpath(C.WEB) + os.sep) and os.path.isfile(fp):
            return FileResponse(fp, headers={"cache-control": asset_cache(path, request.url.query or "")})
        return _not_found()
    # audit v3 N2: no open redirect. A decoded path starting with // (protocol-relative) or holding a backslash is never a page;
    # a redirect target is always a same-site path with exactly one leading slash.
    if path.startswith("//") or "\\" in path or "\x00" in path:
        return _not_found()
    c = canonical(path)
    if c == "/404" or not c.startswith("/") or c.startswith("//") or "\\" in c:
        return _not_found()
    if c != path:
        q = request.url.query
        return Response(status_code=301, headers={"location": c + ("?" + q if q else ""), "cache-control": "public, max-age=3600"})
    if c == "/token":
        return _token_page()
    if c in PAGES:
        p = _page(PAGES[c])
        if p:
            return _html(p)
        return HTMLResponse(PLACEHOLDER.replace("{brand}", C.BRAND_NAME), headers={"cache-control": "no-store"})   # until web/ exists
    p = _page(c.strip("/"))
    return _html(p) if p else _not_found()
