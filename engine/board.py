"""/api/board: the headline snapshot — for the top stocks, $50,000 and $100,000 USDG -> stock: best simulated route vs the best single pool,
the $ difference, venues ranked, pool split of the best route, block and time. Each row is a normal quote (quote.py) at priority "board"
(the router client keeps half of every bucket for users). With a router key the whole board refreshes every BOARD_PERIOD_S; without one,
one row every BOARD_ROW_NOKEY_S, oldest first (12 rows ~ once an hour). A row whose refresh fails keeps its last snapshot, marked stale."""
from __future__ import annotations

import json
import logging
import threading
import time

import config as C
import tokens as TK
import market as M
import quote as Q
import router
import store as S

log = logging.getLogger("rhdex.board")
STATE = {"last_run": 0.0, "last_err": "", "rows_ok": 0, "rows_fail": 0}


def _row_doc(sym: str, size: int, res: dict) -> dict:
    best = next((r for r in res["routes"] if r["id"] == res.get("best")), None)
    venues = [{"venue": r["venue"], "status": r["status"], "rank": r["rank"], "most": r.get("most", False),
               "out": r["out"]["simulated"] or None, "quoted": r["out"]["quoted"], "min": r["out"]["min"],
               "routeFeeUsd": r["fees"]["route"]["usd"], "gasUsd": r["fees"]["gas"]["usd"]} for r in res["routes"]]
    quoted_only = [{"venue": r["venue"], "status": r["status"], "quoted": r["out"]["quoted"], "minGuaranteed": r["out"]["min"]}
                   for r in res.get("quotedOnly") or []]
    s = M.STOCK_BY_SYM[sym]
    return {"stock": sym, "token": s["address"], "name": s["name"],
            "logo": TK._logo_ok(f"stocks/{sym}.png"),     # every stock has its file (SPCX, SKHY baked 17.11); null only if it is missing
            "sizeUsd": size, "amountIn": res["amountIn"], "block": res["block"], "at": res["at"],
            "best": {"kind": best.get("kind", "route"), "venue": best["venue"], "out": best["out"]["simulated"], "split": best["splitByDex"], "pools": best["split"], "path": best.get("path") or [],
                     "routeFee": best["fees"]["route"], "gasUsd": best["fees"]["gas"]["usd"], "priceImpactPct": best["priceImpactPct"]}
            if best else None,
            "singlePool": res.get("baseline"), "vsSinglePool": res.get("vsSinglePool"), "vsSinglePoolUsd": res.get("vsSinglePoolUsd"),
            "vsSinglePoolReason": res.get("vsSinglePoolReason"), "vsSinglePoolDebug": res.get("vsSinglePoolDebug"),
            "directUncontested": res.get("directUncontested"),
            # postable: a real positive gain under the thin-pool threshold (vsSinglePoolUsd is already null at >= VS_POOL_MAX_GAP_PCT)
            "postable": bool(res.get("vsSinglePoolUsd") is not None and res["vsSinglePoolUsd"] > 0),
            # hero: the headline row — postable, a fat gain (>= HERO_MIN_USD) and a big size (>= $50k); small profits never are
            "hero": bool(res.get("vsSinglePoolUsd") is not None and res["vsSinglePoolUsd"] > 0 and res["vsSinglePoolUsd"] >= C.HERO_MIN_USD
                         and size >= 50_000),
            "venues": venues, "quotedOnly": quoted_only, "notes": res.get("notes")}


def refresh_row(sym: str, size: int) -> bool:
    s = M.STOCK_BY_SYM.get(sym)
    if not s:
        return False
    bs = router.budget_state()
    for b in ("routes", "step"):
        x = bs.get(b)
        if x and not C.ROUTE_KEYED and (x["paused"] or x["remaining"] <= (x["limit"] or 75) * router.RESERVE["board"] + (2 if b == "step" else 0)):
            STATE["last_err"] = f"budget {b}: waiting {x['resetIn']}s (board keeps half of every bucket for users)"
            STATE["rows_fail"] += 1
            return False
    try:
        job, res = Q.get({"fromToken": C.USDG, "toToken": s["address"], "amount": str(size * 10 ** 6)}, prio="board")
        if res is None:
            res = job.fut.result(timeout=120)
        if res.get("stale"):
            raise router.Budget("routes", res.get("retryInS") or 60)
        doc = _row_doc(sym, size, res)
        if not doc["best"]:
            prev = S.q("SELECT doc FROM board WHERE stock=? AND size=?", (sym, size))
            if prev and json.loads(prev[0]["doc"]).get("best"):
                STATE["last_err"] = f"{sym} {size}: no route simulated, kept the previous snapshot"
                STATE["rows_fail"] += 1
                return False
        S.x("INSERT OR REPLACE INTO board (stock, size, at, doc) VALUES (?,?,?,?)", (sym, size, doc["at"], json.dumps(doc)))
        STATE["rows_ok"] += 1
        return True
    except router.Budget as e:
        STATE["last_err"] = f"budget {e.bucket} {e.reset_in}s"
    except Exception as e:
        STATE["last_err"] = str(e)[:160]
        log.warning("board row %s %s: %s", sym, size, e)
    STATE["rows_fail"] += 1
    return False


def rows() -> list[tuple[str, int]]:
    return [(s, z) for s in C.BOARD_STOCKS for z in C.BOARD_SIZES_USD]


def loop(stop: threading.Event):
    while not stop.is_set() and not (M.POOLS and M.PRICES):
        stop.wait(3)
    stop.wait(5)
    while not stop.is_set():
        have = {(r["stock"], r["size"]): r["at"] for r in S.q("SELECT stock, size, at FROM board")}
        todo = sorted(rows(), key=lambda k: have.get(k, 0))
        if C.ROUTE_KEYED:
            for k in todo:
                if stop.is_set():
                    break
                refresh_row(*k)
            STATE["last_run"] = time.time()
            stop.wait(C.BOARD_PERIOD_S)
        else:
            last = max(have.values()) if have else 0          # a restart does not spend budget early
            wait = last + C.BOARD_ROW_NOKEY_S - time.time()
            if wait > 0:
                stop.wait(min(wait, C.BOARD_ROW_NOKEY_S))
                continue
            refresh_row(*todo[0])
            STATE["last_run"] = time.time()
            stop.wait(C.BOARD_ROW_NOKEY_S)


def period_s() -> int:
    return C.BOARD_PERIOD_S if C.ROUTE_KEYED else C.BOARD_ROW_NOKEY_S * len(rows())


def get() -> dict:
    docs = {(r["stock"], r["size"]): json.loads(r["doc"]) for r in S.q("SELECT * FROM board")}
    now = time.time()
    out = []
    for k in rows():
        d = docs.get(k)
        if d:
            g = d.get("vsSinglePoolUsd")      # rows stored before postable/hero existed get them from the same rule (smoke 16.11)
            d.setdefault("postable", bool(g is not None and g > 0))
            d.setdefault("hero", bool(g is not None and g > 0 and g >= C.HERO_MIN_USD and int(d.get("sizeUsd") or 0) >= 50_000))
            d["logo"] = TK._logo_ok(f"stocks/{k[0]}.png")      # rows stored before a logo file existed get it on read
            d["ageS"] = int(now - d["at"])
            d["stale"] = d["ageS"] > 2 * period_s()
            out.append(d)
        else:
            out.append({"stock": k[0], "sizeUsd": k[1], "pending": True, "stale": True})
    return {"rows": out, "refreshPeriodS": period_s(), "at": int(now), "lastError": STATE["last_err"] or None}
