"""Sign-in: a Privy access token (wallets only) -> the wallet address that is the account.

The token (Authorization: Bearer …) is checked against the app's public keys (ES256, audience = PRIVY_APP_ID, issuer privy.io); the
wallet comes from Privy's server API with PRIVY_APP_SECRET (never from the page), cached 5 min. Copied from Sentro (terminal/engine/auth.py,
server.py _privy_lookup). Neither the secret nor a token is ever logged.

Keys (audit v6 P1-8): PRIVY_VERIFICATION_KEY (the PEM from Privy's dashboard) means no network at all. Otherwise the app's JWKS is fetched
once and re-fetched for an unknown `kid` at most once per JWKS_REFETCH_S globally, so junk tokens with random kids never make us call Privy.

Which wallet (audit v6 P1-10): the SAME one the page uses. The page takes Privy's `user.wallet` (privy/entry.jsx), and the Privy SDK
(react-auth, function m in privy-context-*.mjs) sets user.wallet = the linked account of type "wallet" with the EARLIEST first_verified_at.
The server applies that rule to linked_accounts; if that wallet is not an Ethereum 0x address there is no account (the page could not use
it either). Embedded wallets are off in this app (privy/entry.jsx: loginMethods wallet, no embedded), so the rule never meets one.

Privy's API failing (5xx, 429, timeout) is not "signed out": wallet_of raises Unavailable and the API answers 503; a failure is cached
NEG_S per user so a burst does not hammer Privy.

Identity token path (rhdex): when PRIVY_APP_SECRET is not set, a Privy identity token in the `privy-id-token` header (same JWKS, ES256,
aud = app id) carries `linked_accounts`; the wallet is picked from it with the same rule. No server secret needed.

Local testing without Privy: DEV=1 lets a request from 127.0.0.1 sign in as the address in the X-Dev-Wallet header — only a direct
one (client 127.0.0.1/::1, Host 127.0.0.1/localhost, no X-Forwarded-For / Forwarded), so a proxy in front of the app cannot open it, and
only while RHDEX_SITE is unset or local. Never set it on a server (guard.py refuses to start a production process with it).
Copied from Hylit (fundtap/site/engine/auth.py), env names changed."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

import jwt

ISSUER = "privy.io"
JWKS_REFETCH_S = 300
JWKS_RETRY_S = 15                              # after a FAILED fetch
WALLET_TTL_S = 300
NEG_S = 15
CACHE_MAX = 10_000
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_keys_lock = threading.Lock()
_keys: dict = {"aid": None, "by_kid": {}, "at": 0.0}
_wallets: "OrderedDict[str, tuple[float, str | None]]" = OrderedDict()
_neg: dict[str, float] = {}
_wlock = threading.Lock()
STATS = {"jwks_fetch": 0, "privy_err": 0}


class Unavailable(Exception):
    """Privy's API did not answer properly: the caller says 503, not 401."""


def app_id() -> str:
    return (os.environ.get("PRIVY_APP_ID") or "").strip()


def enabled() -> bool:
    return bool(app_id())


def _fetch_jwks(aid: str) -> dict:
    req = urllib.request.Request(f"https://auth.privy.io/api/v1/apps/{aid}/jwks.json", headers={"accept": "application/json", "user-agent": __import__("config").UA})
    with urllib.request.urlopen(req, timeout=5) as r:
        doc = json.load(r)
    out = {}
    for k in doc.get("keys") or []:
        try:
            out[k.get("kid")] = jwt.PyJWK(k).key
        except Exception:
            continue
    return out


def _key_for(aid: str, kid: str | None):
    pem = (os.environ.get("PRIVY_VERIFICATION_KEY") or "").strip()
    if pem:
        return pem.replace("\\n", "\n")
    with _keys_lock:
        if _keys["aid"] != aid:
            _keys.update(aid=aid, by_kid={}, at=0.0)
        k = _keys["by_kid"].get(kid)
        if k is not None:
            return k
        if time.time() - _keys["at"] < JWKS_REFETCH_S:   # unknown kid and we fetched recently: no new request (junk tokens stay cheap)
            return None
        _keys["at"] = time.time()
    try:
        got = _fetch_jwks(aid)
        STATS["jwks_fetch"] += 1
    except Exception:
        with _keys_lock:                              # a failed fetch (Privy down at start) is retried after JWKS_RETRY_S, not 5 min (v7 M4)
            _keys["at"] = time.time() - JWKS_REFETCH_S + JWKS_RETRY_S
        return None
    with _keys_lock:
        _keys["by_kid"] = got or _keys["by_kid"]
        return _keys["by_kid"].get(kid)


def user_of(authorization: str | None) -> str | None:
    """The Privy user id (did:privy:…) of a valid, unexpired access token for this app; None for anything else."""
    aid = app_id()
    if not aid or not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = _key_for(aid, kid)
        if key is None:
            return None
        claims = jwt.decode(token, key, algorithms=["ES256"], audience=aid, issuer=ISSUER, options={"require": ["exp", "sub", "aud", "iss"]}, leeway=30)
    except Exception:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub.startswith("did:privy:") else None


def pick_wallet(linked_accounts: list) -> str | None:
    """Privy's user.wallet rule (earliest first_verified_at among type "wallet"), then: an Ethereum 0x address or nothing."""
    ws = [a for a in linked_accounts or [] if isinstance(a, dict) and a.get("type") == "wallet"]
    if not ws:
        return None
    first = sorted(ws, key=lambda a: float(a.get("first_verified_at") or 0))[0]     # stable, like the SDK's sort
    addr = str(first.get("address") or "")
    if (first.get("chain_type") or "ethereum") != "ethereum" or not _ADDR.match(addr):
        return None
    return addr.lower()


def wallet_of(user: str) -> str | None:
    """The account's wallet (pick_wallet over Privy's server API), cached WALLET_TTL_S. Raises Unavailable when Privy fails."""
    now = time.time()
    with _wlock:
        hit = _wallets.get(user)
        if hit and now - hit[0] < WALLET_TTL_S:
            return hit[1]
        if now - _neg.get(user, 0) < NEG_S:
            raise Unavailable("privy recently failed")
    aid, secret = app_id(), (os.environ.get("PRIVY_APP_SECRET") or "").strip()
    if not secret:
        return None
    req = urllib.request.Request("https://auth.privy.io/api/v1/users/" + urllib.parse.quote(user, safe=":"),
                                 headers={"authorization": "Basic " + base64.b64encode(f"{aid}:{secret}".encode()).decode(),
                                          "privy-app-id": aid, "accept": "application/json", "user-agent": __import__("config").UA})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):                  # no such user for this app: genuinely not signed in
            w = None
            j = None
        else:
            STATS["privy_err"] += 1
            with _wlock:
                _neg[user] = now
            raise Unavailable(f"privy {e.code}")
    except Exception as e:
        STATS["privy_err"] += 1
        with _wlock:
            _neg[user] = now
        raise Unavailable(type(e).__name__)
    w = pick_wallet(j.get("linked_accounts") or []) if isinstance(j, dict) else None
    with _wlock:
        _wallets[user] = (now, w)
        _wallets.move_to_end(user)
        while len(_wallets) > CACHE_MAX:
            _wallets.popitem(last=False)
        _neg.pop(user, None)
        if len(_neg) > CACHE_MAX:
            _neg.clear()
    return w


# any header a proxy / CDN / edge puts in front of the app: with one of them the request did not come straight from this machine
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "fly-client-ip", "cf-connecting-ip", "true-client-ip", "x-client-ip",
                 "x-forwarded-host", "x-original-forwarded-for", "fastly-client-ip", "x-cluster-client-ip")


def local_direct(headers, client_host: str | None) -> bool:
    """A request straight from this machine: loopback peer, Host 127.0.0.1 / localhost / [::1], and no proxy header at all
    (audit v4 M18: X-Real-IP and Fly-Client-IP count too). Used by the DEV sign-in and the full /healthz under DEV."""
    host = (headers.get("host") or "").strip().lower()
    host = host[1:].split("]")[0] if host.startswith("[") else host.split(":")[0]
    if any(headers.get(h) for h in PROXY_HEADERS):
        return False
    return client_host in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "::1")


def address_of(headers, client_host: str | None) -> str | None:
    """The signed-in wallet of a request, or None. Raises Unavailable when the token is valid but Privy's API is down."""
    site = (os.environ.get("RHDEX_SITE") or "").strip()
    local_site = not site or (urllib.parse.urlsplit(site).hostname or "") in ("127.0.0.1", "localhost")   # v7 M3: never on a named site
    if (os.environ.get("DEV") or "").strip() == "1" and local_direct(headers, client_host) and local_site:
        w = (headers.get("x-dev-wallet") or "").strip()
        if _ADDR.match(w):
            return w.lower()
    user = user_of(headers.get("authorization"))
    if not user:
        return None
    if not (os.environ.get("PRIVY_APP_SECRET") or "").strip():
        return wallet_from_id_token(headers.get("privy-id-token"), user)
    return wallet_of(user)


def wallet_from_id_token(token: str | None, user: str) -> str | None:
    """Wallet from a verified Privy identity token of the same user (linked_accounts claim, a JSON string)."""
    aid = app_id()
    if not aid or not token:
        return None
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = _key_for(aid, kid)
        if key is None:
            return None
        claims = jwt.decode(token, key, algorithms=["ES256"], audience=aid, issuer=ISSUER, options={"require": ["exp", "sub", "aud", "iss"]}, leeway=30)
    except Exception:
        return None
    if claims.get("sub") != user:
        return None
    la = claims.get("linked_accounts")
    if isinstance(la, str):
        try:
            la = json.loads(la)
        except ValueError:
            return None
    # SDK rule = earliest first_verified_at. If the token does not carry it for every wallet and there are several, the choice would be a
    # guess (audit v2 minor 2): refuse instead (401) — one wallet, or all with first_verified_at/fv, is unambiguous.
    ws = [x for x in la or [] if isinstance(x, dict) and x.get("type") == "wallet"]
    if len(ws) > 1 and not all((x.get("first_verified_at") or x.get("fv")) for x in ws):
        return None
    return pick_wallet([{**x, "first_verified_at": x.get("first_verified_at") or x.get("fv") or 0} for x in ws])


def missing_reason(headers) -> str | None:
    """D13 / audit v1 P0-2: a valid access token but no way to learn the wallet — say exactly that (401 id_token_required) so the page
    knows to send its Privy identity token in the `privy-id-token` header (or the server gets PRIVY_APP_SECRET)."""
    user = user_of(headers.get("authorization"))
    if user and not (os.environ.get("PRIVY_APP_SECRET") or "").strip() and not headers.get("privy-id-token"):
        return "id_token_required"
    return None
