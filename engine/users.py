"""Signed-in features (all open to everyone in the MVP): watchlist of tickers, big-trade alerts per ticker (threshold in USD) -> site feed
+ Telegram, swap history from the user's own tx hashes. One access point: access(feature, wallet) — today always open; the future token
gate replaces only this function."""
from __future__ import annotations

import json
import time

import config as C
import market as M
import store as S
import tg
from chain import RpcError, rpc_any, u, word_addr

class ChainDown(Exception):
    """The node did not answer: 503 chain_unavailable, never "invalid hash" (audit v2 minor 4)."""


MAX_HISTORY = 500
HISTORY_CHAINS = {C.CHAIN, 1, 10, 56, 137, 8453, 42161, C.SOLANA}

FEATURES = tuple(f for f in ("watchlist", "alerts", "telegram", "history", "split", "limits")
                 if (f != "alerts" or C.ALERTS_ENABLED) and (f != "telegram" or tg.enabled()))   # alerts and Telegram off since 16.11
MAX_WATCH = 100
MAX_ALERTS = 50


def access(feature: str, wallet: str | None) -> dict:
    """{"open": bool, "reason": str|None}. MVP: everything open to everyone (limits: not built, LIMITS_ENABLED stub)."""
    if feature == "limits" and not C.LIMITS_ENABLED:
        return {"open": False, "reason": "not_built"}
    return {"open": True, "reason": None}


def _ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if t not in M.STOCK_BY_SYM:
        raise ValueError("unknown ticker")
    return t


def watchlist(w: str) -> list[str]:
    return [r["ticker"] for r in S.q("SELECT ticker FROM watch WHERE wallet=? ORDER BY at", (w,))]


def watch_set(w: str, tickers: list[str]) -> list[str]:
    ts = []
    for t in tickers[:MAX_WATCH]:
        tt = _ticker(t)
        if tt not in ts:
            ts.append(tt)
    S.x("DELETE FROM watch WHERE wallet=?", (w,))
    now = int(time.time())
    for i, t in enumerate(ts):
        S.x("INSERT INTO watch (wallet, ticker, at) VALUES (?,?,?)", (w, t, now + i))
    return ts


def alerts(w: str) -> list[dict]:
    return [{"id": r["id"], "ticker": r["ticker"], "minUsd": r["min_usd"], "telegram": bool(r["tg"]), "createdAt": r["at"]}
            for r in S.q("SELECT * FROM alerts WHERE wallet=? ORDER BY at", (w,))]


def alert_put(w: str, ticker: str, min_usd: float, telegram: bool = True) -> dict:
    t = _ticker(ticker)
    if not (C.TRADE_MIN_USD <= float(min_usd) <= 10_000_000):
        raise ValueError(f"minUsd must be between {C.TRADE_MIN_USD} and 10000000")
    if len(alerts(w)) >= MAX_ALERTS and not S.q("SELECT 1 FROM alerts WHERE wallet=? AND ticker=?", (w, t)):
        raise ValueError("too many alerts")
    S.x("INSERT INTO alerts (wallet, ticker, min_usd, tg, at) VALUES (?,?,?,?,?) ON CONFLICT(wallet, ticker) DO UPDATE SET "
        "min_usd=excluded.min_usd, tg=excluded.tg", (w, t, float(min_usd), int(bool(telegram)), int(time.time())))
    return next(a for a in alerts(w) if a["ticker"] == t)


def alert_del(w: str, aid: int) -> bool:
    return S.x("DELETE FROM alerts WHERE wallet=? AND id=?", (w, aid)).rowcount > 0


def hits(w: str, limit: int = 100) -> list[dict]:
    return [{"id": r["id"], "ticker": r["ticker"], "usd": r["usd"], "side": r["side"], "tx": r["tx"], "ts": r["ts"], "telegramSent": bool(r["tg_sent"])}
            for r in S.q("SELECT * FROM alert_hits WHERE wallet=? ORDER BY ts DESC LIMIT ?", (w, limit))]


def on_trade(t: dict):
    """Trade listener (trades.py): every alert whose ticker and threshold match gets a hit; Telegram when linked and wanted."""
    for a in S.q("SELECT * FROM alerts WHERE ticker=? AND min_usd <= ?", (t["stock"], t["usd"])):
        if not access("alerts", a["wallet"])["open"]:
            continue
        cur = S.x("INSERT OR IGNORE INTO alert_hits (alert_id, wallet, tx, ticker, usd, side, ts) VALUES (?,?,?,?,?,?,?)",
                  (a["id"], a["wallet"], t["tx"], t["stock"], t["usd"], t["side"], t["ts"]))
        if not cur.rowcount or not a["tg"] or not tg.enabled():
            continue
        chat = tg.chat_of(a["wallet"])
        if chat and access("telegram", a["wallet"])["open"]:
            import copytext as T
            ok = tg.send(chat, T.s("tg.alert", ticker=t["stock"], side=t["side"], usd=f"{t['usd']:,.0f}",
                                   venues=" · ".join(v["name"] for v in t["venues"][:4]), block=t["block"],
                                   time=time.strftime("%H:%M", time.gmtime(t["ts"])), min=f"{a['min_usd']:,.0f}", explorer=t["explorer"]))
            if ok:
                S.x("UPDATE alert_hits SET tg_sent=1 WHERE alert_id=? AND tx=?", (a["id"], t["tx"]))


# ---------------------------------------------------------------- swap history from the user's own tx hashes
def history(w: str) -> list[dict]:
    return [json.loads(r["doc"]) for r in S.q("SELECT doc FROM history WHERE wallet=? ORDER BY ts DESC LIMIT 200", (w,))]


def history_add(w: str, tx: str, chain: int = C.CHAIN) -> dict:
    if chain not in HISTORY_CHAINS:
        import tokens as TK
        if not TK.chain_info(chain):                    # any Pay-from network (reach.py measurement)
            raise ValueError("unknown chainId")
    if chain != C.SOLANA:
        tx = tx.lower()
        if not (tx.startswith("0x") and len(tx) == 66 and all(ch in "0123456789abcdef" for ch in tx[2:])):
            raise ValueError("tx must be a 0x transaction hash")
    elif not (40 <= len(tx) <= 100 and tx.isalnum()):
        raise ValueError("tx must be a Solana signature")
    if S.q("SELECT COUNT(*) n FROM history WHERE wallet=?", (w,))[0]["n"] >= MAX_HISTORY and not S.q(
            "SELECT 1 FROM history WHERE wallet=? AND tx=?", (w, tx)):
        S.x("DELETE FROM history WHERE wallet=? AND tx IN (SELECT tx FROM history WHERE wallet=? ORDER BY ts ASC LIMIT 1)", (w, w))
    if chain != C.CHAIN:
        doc = {"tx": tx, "chainId": chain, "status": "SUBMITTED", "ts": int(time.time()), "note": "cross-chain: track with /api/status"}
    else:
        try:
            t = rpc_any("eth_getTransactionByHash", [tx])
            rc = rpc_any("eth_getTransactionReceipt", [tx])
        except RpcError:
            raise ChainDown()
        if not t:
            raise ValueError("transaction not found")
        if (t.get("from") or "").lower() != w:
            raise ValueError("transaction was not sent by this wallet")
        moves = []
        for l in (rc or {}).get("logs") or []:
            tp = l.get("topics") or []
            if len(tp) == 3 and tp[0] == C.TOPIC_TRANSFER:
                frm, to = word_addr(tp[1]), word_addr(tp[2])
                if w in (frm, to):
                    a = l["address"].lower()
                    s = M.STOCK_BY_ADDR.get(a)
                    meta = (M.STATE.get("token_meta") or {}).get(a) or {}
                    moves.append({"token": a, "symbol": s["symbol"] if s else meta.get("symbol"), "decimals": 18 if s else meta.get("decimals"),
                                  "amount": str(u(l["data"][:66])), "direction": "out" if frm == w else "in"})
        blk = u((rc or {}).get("blockNumber")) if rc else None
        ts = None
        if blk:
            try:
                ts = u(rpc_any("eth_getBlockByNumber", [hex(blk), False])["timestamp"])
            except Exception:
                ts = None
        doc = {"tx": tx, "chainId": chain, "status": ("DONE" if rc.get("status") == "0x1" else "FAILED") if rc else "PENDING",
               "block": blk, "ts": ts or int(time.time()), "moves": moves}
    S.x("INSERT OR REPLACE INTO history (wallet, tx, chain, ts, status, doc) VALUES (?,?,?,?,?,?)",
        (w, tx, chain, doc["ts"], doc["status"], json.dumps(doc)))
    return doc
