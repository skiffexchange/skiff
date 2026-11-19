"""QUOTE_REPLAY — dev only. Without a router key, layout work and audits would eat the route provider's budget, so a dev engine can answer
/api/quote, /api/quote/stream and /api/board from recorded REAL answers.

Recording (always on, live mode): every live, non-stale quote answer is written to data/replay/<pair>/<amount>.json with its SSE timeline
(event order and the ms offsets at which each event was emitted) and the time of recording. One file per (fromChain, fromToken, toChain,
toToken, amount raw); a newer live run overwrites it. Addresses of signed-in senders are not stored (request.fromAddress is dropped).
Replay (QUOTE_REPLAY=1 AND DEV=1, never in production — guard.py refuses to start with QUOTE_REPLAY set): the recording of the same pair
with the nearest amount (by ratio) is returned unchanged except for `replay: true`, `recordedAt`, `replayAmount` (raw amount recorded) and
`replayExactAmount` (false when the nearest recording is another size). The stream replays the recorded events with the recorded delays
(recordings older than SSE `source` events get them rebuilt from their own rows, see with_sources).
No recording for the pair -> 404 {"error":"no_replay", "available":[…]} (nothing live is called). The board is rebuilt from the recorded
USDG -> stock $50k / $100k answers."""
from __future__ import annotations

import json
import math
import os
import threading
import time

import config as C

DIR = os.path.join(C.DATA, "replay")
_lock = threading.Lock()


def recording_enabled() -> bool:
    """Recording writes a file per pair x amount: local dev only (DEV=1), never in production (audit v1 P1-4)."""
    import guard
    return (os.environ.get("DEV") or "") == "1" and not guard.is_prod()


def enabled() -> bool:
    return (os.environ.get("QUOTE_REPLAY") or "") == "1" and (os.environ.get("DEV") or "") == "1"


def _pair_dir(fc, ft, tc, tt) -> str:
    return os.path.join(DIR, f"{fc}-{ft}-{tc}-{tt}".lower() if str(ft).startswith("0x") else f"{fc}-{ft}-{tc}-{tt}")


def _strip(d):
    if isinstance(d, dict):
        out = {k: _strip(v) for k, v in d.items()}
        if "request" in out and isinstance(out["request"], dict):
            out["request"] = {**out["request"], "fromAddress": None}
        return out
    if isinstance(d, list):
        return [_strip(v) for v in d]
    return d


def record(job, res: dict) -> None:
    if not res or res.get("stale") or res.get("replay") or res.get("routesUnavailable"):
        return
    if not recording_enabled():
        return
    r = job.r
    d = _pair_dir(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"])
    rec = {"recordedAt": int(time.time()), "pair": {"fromChain": r["fromChain"], "fromToken": r["fromToken"], "toChain": r["toChain"],
                                                    "toToken": r["toToken"]},
           "amount": str(r["amount"]), "probeSender": res.get("probeSender"), "prio": job.prio,
           "timeline": [[ms, ev, _strip(data)] for ms, ev, data in job.timeline], "doneMs": int((time.time() - job.t0) * 1000),
           "final": _strip(res)}
    with _lock:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{r['amount']}.json")
        def routes_ok(f):
            return sum(1 for x in f.get("routes", []) if x.get("status") == "ok" and x.get("kind") != "direct")
        if os.path.exists(p):                                  # never replace a recording with one that simulated fewer routes
            try:
                if routes_ok(res) == 0 and routes_ok(json.load(open(p))["final"]) > 0:
                    return
            except Exception:
                pass
        tmp = p + ".tmp"
        json.dump(rec, open(tmp, "w"), separators=(",", ":"))
        os.replace(tmp, p)


def available() -> list[dict]:
    out = []
    if not os.path.isdir(DIR):
        return out
    for pd in sorted(os.listdir(DIR)):
        full = os.path.join(DIR, pd)
        if not os.path.isdir(full):
            continue
        amts = sorted(int(f[:-5]) for f in os.listdir(full) if f.endswith(".json"))
        fc, ft, tc, tt = pd.split("-", 3)
        out.append({"fromChain": int(fc), "fromToken": ft, "toChain": int(tc), "toToken": tt, "amounts": [str(a) for a in amts]})
    return out


def lookup(r: dict) -> dict | None:
    d = _pair_dir(r["fromChain"], r["fromToken"], r["toChain"], r["toToken"])
    if not os.path.isdir(d):
        return None
    amts = [int(f[:-5]) for f in os.listdir(d) if f.endswith(".json")]
    if not amts:
        return None
    want = r["amount"]
    best = min(amts, key=lambda a: abs(math.log(a / want)))
    rec = json.load(open(os.path.join(d, f"{best}.json")))
    mark = {"replay": True, "recordedAt": rec["recordedAt"], "replayAmount": str(best), "replayExactAmount": best == want}
    rec["mark"] = mark
    rec["final"] = {**rec["final"], **mark}
    rec["timeline"] = with_sources(rec.get("timeline") or [], rec["final"])
    base = rec["final"].get("baseline")
    if isinstance(base, dict) and "thin" not in base and base.get("out"):     # recordings made before the flag existed
        import market as M
        tin = C.WETH if r["fromToken"] == C.NATIVE else r["fromToken"]
        pools = [p for p in M.pools_for_pair(tin, r["toToken"]) if p["dex"] == "uniswap-v3"]
        liq = max((p["liqUsd"] or 0) for p in pools) if pools else None
        in_usd = (rec["final"].get("amountIn") or {}).get("usd")
        rec["final"]["baseline"] = {**base, "poolLiqUsd": liq, "thin": bool(liq is not None and in_usd and liq < 2 * in_usd),
                                    "thinAnnotatedAtReplay": True}
    return rec


def with_sources(timeline: list, final: dict) -> list:
    """Recordings made before SSE `source` events existed get them rebuilt from their own routes / quotedOnly / unavailable / baseline /
    direct rows at the recorded ms (quote.derive_sources, each marked derived: true). Newer recordings already carry the live ones."""
    if any(ev == "source" for _ms, ev, _d in timeline):
        return timeline
    import quote as Q
    try:
        derived = Q.derive_sources(final, timeline)
    except Exception:
        return timeline
    rows = [(ms, 0, i, ev, d) for i, (ms, ev, d) in enumerate(timeline)] + [(ms, 1, i, "source", d) for i, (ms, d) in enumerate(derived)]
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    return [[ms, ev, d] for ms, _o, _i, ev, d in rows]


def board_rows(stocks: list[str], sizes: list[int]) -> dict:
    import board
    import market as M
    rows = []
    for sym in stocks:
        s = M.STOCK_BY_SYM.get(sym)
        for z in sizes:
            rec = lookup({"fromChain": C.CHAIN, "fromToken": C.USDG, "toChain": C.CHAIN, "toToken": s["address"], "amount": z * 10 ** 6}) if s else None
            if not rec:
                rows.append({"stock": sym, "sizeUsd": z, "pending": True, "stale": True, "replay": True})
                continue
            doc = board._row_doc(sym, z, rec["final"])
            doc.update(rec["mark"], ageS=int(time.time() - doc["at"]), stale=False)
            rows.append(doc)
    return {"rows": rows, "refreshPeriodS": None, "at": int(time.time()), "lastError": None, "replay": True}
