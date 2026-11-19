"""Skiff Vault (the /token page, TOKEN-UTILITY.md): read-only state for /api/vault and /api/vault/check.

 the vault is one wallet that holds USDG (token fees are swapped to USDG and kept there; the weekly stock buys are paid from
it). So the page shows ONE balance: USDG of VAULT_ADDRESS, read with a single balanceOf on the chain, cached 60 s.
Config lives in config.py (TOKEN_PAGE, TOKEN_TICKER, TOKEN_CA, VAULT_ADDRESS, VAULT_FIRST_DROP, DROP_SHARE):
  launched   = TOKEN_CA set        → Buy + the contract address, page indexable, in the sitemap
  vault      = VAULT_ADDRESS set   → the USDG balance line, DROP_SHARE of it as «this week's drop», the explorer link
  first drop = VAULT_FIRST_DROP (override) or, once TOKEN_CA is set, launch + 7 days; weekly after that. Launch = the block time of the token's first mint (Transfer from 0x0) found with
               eth_getLogs in 10M-block windows back from the head (≈ 0.2 s a window); if the chain does not answer, the moment the engine
               first saw this CA. Stored in the meta table under vault.launch:<ca> (the data volume), so restarts and redeploys never shift it;
               a new CA gets its own key. A first_seen value from a chain error is upgraded to the chain time when the chain answers again
               within 24 h (before anyone relies on it), then it is final.
There is NO distribution engine here (snapshot, recipients, drops are a later step): log and received are empty lists, drops = false.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time

from fractions import Fraction

import chain
import config as C
import market as M
import store as S
from track import RH_EXPLORER

log = logging.getLogger("rhdex.vault")
ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
WEEK_S = 7 * 86_400
BAL_TTL_S = 60
USDG_DEC = 6
_bal = {"at": 0.0, "doc": None}
_lock = threading.Lock()
_llock = threading.Lock()
_launch: dict = {"ca": None, "doc": None, "tried": 0.0}
LOG_WINDOW = 10_000_000          # the official node's eth_getLogs span limit
LOG_WINDOWS_MAX = 14             # 140M blocks back (the chain is at ~117M, 18.11)
RETRY_S = 300                    # a provisional first_seen retries the chain at most every 5 min ...
PROVISIONAL_S = 86_400           # ... for 24 h, then it is final
_SHARE = Fraction(str(C.DROP_SHARE)).limit_denominator(10_000)


def launched() -> bool:
    return bool(C.TOKEN_CA)


def ticker() -> str | None:
    """$TICKER once the owner sets it; None until then (no placeholder anywhere)"""
    return ("$" + C.TOKEN_TICKER) if C.TOKEN_TICKER else None


def ticker_text() -> str:
    """what every {ticker} slot shows: «$SKIFF» once set, else token.ticker.fallback («the {brand} token», brand filled)"""
    t = ticker()
    if t:
        return t
    import copytext
    v = copytext.s("token.ticker.fallback")
    return v if v != "token.ticker.fallback" and "{" not in v else f"the {copytext.brand()} token"


def explorer(addr: str) -> str:
    return f"{RH_EXPLORER}/address/{addr}"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _override() -> float | None:
    v = C.VAULT_FIRST_DROP
    if not v:
        return None
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        log.warning("VAULT_FIRST_DROP is not ISO: %r", v)
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


class _NoMint(Exception):
    pass


def _chain_launch(ca: str) -> tuple[int, int]:
    """(block, unix time) of the token's first mint. Raises chain.RpcError / OSError when the node fails, _NoMint when there is none."""
    head = chain.block_number()
    first = None
    hi = head
    for _ in range(LOG_WINDOWS_MAX):
        lo = max(0, hi - LOG_WINDOW + 1)
        r = chain.rpc("eth_getLogs", [{"address": ca, "fromBlock": hex(lo), "toBlock": hex(hi),
                                       "topics": [C.TOPIC_TRANSFER, "0x" + "0" * 64]}], retries=2, timeout=20)
        blocks = [chain.u(x["blockNumber"]) for x in (r or [])]
        if blocks:
            first = min(blocks)
        elif first is not None:
            break                      # a window with no mint after one with mints: the contract did not exist before
        if lo == 0:
            break
        hi = lo - 1
    if first is None:
        raise _NoMint(ca)
    b = chain.rpc_any("eth_getBlockByNumber", [hex(first), False])
    return first, chain.u(b["timestamp"])


def launch() -> dict | None:
    """{at, block, source: chain | first_seen} for TOKEN_CA, persisted in meta (vault.launch:<ca>); None without a CA"""
    ca = C.TOKEN_CA
    if not ca:
        return None
    key = "vault.launch:" + ca
    with _llock:
        if _launch["ca"] != ca:
            _launch.update(ca=ca, doc=None, tried=0.0)
            try:
                v = S.meta_get(key)
                _launch["doc"] = json.loads(v) if isinstance(v, str) else v
            except Exception as e:
                log.warning("vault launch meta read: %s", e)
        d = _launch["doc"]
        now = time.time()
        if d and (d.get("source") == "chain" or not d.get("provisional") or now - d["at"] > PROVISIONAL_S or now - _launch["tried"] < RETRY_S):
            return d
        _launch["tried"] = now
        try:
            blk, ts = _chain_launch(ca)
            d = {"at": ts, "block": blk, "source": "chain"}
        except _NoMint:
            d = d or {"at": int(now), "block": None, "source": "first_seen", "provisional": False}   # no mint log: never will be
        except Exception as e:
            log.warning("vault launch lookup: %s", e)
            d = d or {"at": int(now), "block": None, "source": "first_seen", "provisional": True}
        try:
            S.meta_set(key, d)
        except Exception as e:
            log.warning("vault launch meta write: %s", e)
        _launch["doc"] = d
        return d


def first_drop() -> tuple[float, str] | None:
    """(unix time, source: override | launch) of the first drop; None before launch without an override"""
    o = _override()
    if o is not None:
        return o, "override"
    lz = launch()
    return (lz["at"] + WEEK_S, "launch") if lz else None


def next_drop(now: float | None = None) -> str | None:
    """weekly from the first drop; None before launch without an override (the page shows dashes, no countdown)"""
    f = first_drop()
    if not f:
        return None
    now = time.time() if now is None else now
    t0 = f[0]
    n = 0 if now < t0 else int((now - t0) // WEEK_S) + 1
    return _iso(t0 + n * WEEK_S)


def _amount(raw: int, dec: int, dp: int = 2) -> str:
    """integer math only (raw → «1,234.56»), rounded half-up"""
    q = 10 ** (dec - dp) if dec > dp else 1
    units = (raw + q // 2) // q if dec > dp else raw * 10 ** (dp - dec)
    ip, fp = divmod(units, 10 ** dp)
    return f"{ip:,}" + (f".{fp:0{dp}d}" if dp else "")


def balance() -> dict | None:
    """{usdg: {raw, decimals, amount}, usd} of VAULT_ADDRESS, or None before the address is set / while the chain does not answer"""
    if not C.VAULT_ADDRESS:
        return None
    with _lock:
        if _bal["doc"] is not None and time.time() - _bal["at"] < BAL_TTL_S:
            return _bal["doc"]
    try:
        ret = chain.rpc_any("eth_call", [{"to": C.USDG, "data": chain.balance_of_data(C.VAULT_ADDRESS)}, "latest"])
        raw = int(ret[2:66] or "0", 16) if ret and len(ret) >= 66 else 0
    except Exception as e:
        log.warning("vault USDG balance: %s", e)
        with _lock:
            return _bal["doc"]
    v = M.usd(raw, USDG_DEC, C.USDG)
    drop = raw * _SHARE.numerator // _SHARE.denominator   # integer math on raw: DROP_SHARE of the vault goes out in the weekly drop
    doc = {"usdg": {"raw": str(raw), "decimals": USDG_DEC, "amount": _amount(raw, USDG_DEC)}, "usd": round(v, 2) if v is not None else None,
           "dropUsdg": {"raw": str(drop), "amount": _amount(drop, USDG_DEC)}}
    with _lock:
        _bal.update(at=time.time(), doc=doc)
    return doc


def doc() -> dict:
    return {"ticker": ticker(), "tickerText": ticker_text(), "ca": C.TOKEN_CA or None, "vault": C.VAULT_ADDRESS or None, "launched": launched(),
            "explorer": {"vault": explorer(C.VAULT_ADDRESS) if C.VAULT_ADDRESS else None, "ca": explorer(C.TOKEN_CA) if C.TOKEN_CA else None},
            "holdings": balance(), "log": [], "drops": False, **_drops(), "at": int(time.time())}


def _drops() -> dict:
    lz = launch()
    f = first_drop()
    o = f is not None and f[1] == "override"
    return {"launchAt": _iso(lz["at"]) if lz else None, "launchSource": lz["source"] if lz else None,
            "firstDrop": _iso(f[0]) if f else None, "nextDrop": next_drop(),
            "source": "override" if o else (lz["source"] if lz else None)}


def check(addr: str) -> dict:
    """what an address got in past drops. No drops yet (no distribution engine): drops false, received []."""
    a = (addr or "").strip()
    if not ADDR.match(a):
        raise ValueError("bad_address")
    return {"address": a.lower(), "launched": launched(), "drops": False, "received": [], "totalUsd": None}
