"""Tokens that look like a public company's stock. A rule, not a hand list.

Data: engine/us-listed.json — every US-listed ticker (Nasdaq Trader symbol directory, tools/us_listed_snapshot.py) -> the company's
distinctive words (the security name without legal / share-class words: «Coinbase Global, Inc. - Class A Common Stock» -> «coinbase global»).
Plus our 64 stocks (stocks.json).

A token is a lookalike when, after peeling wrappers off its ticker (prefixes wt / w / x / s / b / r and suffixes x / on /.t, e.g. wtHOOD,
NVDAx, sTSLA) and off its name («Wrapped», «Tokenized», «xStock», «Token»):
  1. its ticker (raw or peeled) is one of OUR stocks — always (clash or wrapped copy of our stock); or
  2. its ticker (raw or peeled) is a US-listed ticker AND its name carries that company's first distinctive word as a whole word
     (Coinbase / CoreWeave / Oracle / Nebius / Lumentum / Rocket Lab / Robinhood …); or
  3. its name says it is a share (wrapped / tokenized / xStock / stock / shares / backed / synthetic) AND carries the company name of one of
     our stocks (generic second word dropped: «palantir technologies» -> «palantir»): «Wrapped Tesla», «Tokenized NVIDIA» under any ticker.
     A memecoin merely starting with a word like «Penguin», «Circle» or «Meta» is kept.
Ticker alone never hides: a memecoin «MEME / A Meme Coin» matches the ticker of «Roundhill Meme Stock ETF» but not its first word
«roundhill»; «U / United Stables» vs «Unity Software» — kept."""
from __future__ import annotations

import json
import os
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "us-listed.json")
_lock = threading.Lock()
_us: dict | None = None

LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "lp", "nv", "sa", "ag", "se", "the",
         "holdings", "holding", "group", "class", "common", "stock", "shares", "share", "ordinary", "american", "depositary", "depository",
         "representing", "each", "one", "two", "three", "a", "b", "c", "units", "unit", "warrant", "warrants", "rights", "right", "preferred",
         "series", "subordinated", "notes", "due", "trust", "fund", "etf", "adr", "ads", "of", "and", "&", "s", "new", "par", "value"}
MARKERS = {"wrapped", "tokenized", "tokenised", "xstock", "xstocks", "stock", "shares", "backed", "synthetic", "st0x"}   # «this is a share»
WRAP_WORDS = {"wrapped", "tokenized", "tokenised", "xstock", "xstocks", "token", "tokens", "onchain", "on-chain", "stock", "shares"}
PREFIXES = ("wt", "w", "x", "s", "b", "r")      # lower-case wrapper prefix before an upper-case ticker: wtHOOD, sTSLA, bCOIN
SUFFIXES = ("x", "on", ".t", "t")               # NVDAx (xStocks), TSLAon, AAPL.t


def words(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def company_core(security_name: str) -> str:
    """«Coinbase Global, Inc. - Class A Common Stock» -> «coinbase global» (first two distinctive words)"""
    name = re.split(r"\s+-\s+|\s+–\s+", security_name or "")[0]
    ws = [w for w in words(name) if w not in LEGAL and not w.isdigit()]
    return " ".join(ws[:2])


def _load() -> dict:
    global _us
    with _lock:
        if _us is None:
            try:
                with open(DATA, encoding="utf-8") as f:
                    _us = json.load(f).get("tickers") or {}
            except (OSError, ValueError):
                _us = {}
        return _us


GENERIC = {"technologies", "technology", "entertainment", "automotive", "semiconductor", "interactive", "internet", "computing", "solutions",
           "platforms", "platform", "systems", "software", "motors", "airlines", "financial", "networks", "therapeutics", "energy",
           "international", "global", "markets", "communications", "devices", "micro", "labs", "enterprises", "industries"}


def _ours() -> tuple[set[str], list[str]]:
    """our tickers, and our companies' names for rule 3 (a generic second word dropped: «palantir technologies» -> «palantir»)"""
    import market as M
    syms = {s["symbol"].upper() for s in M.STOCKS}
    names = set()
    for s in M.STOCKS:
        c = company_core(s["name"])
        ws = c.split(" ") if c else []
        if len(ws) == 2 and ws[1] in GENERIC:
            ws = ws[:1]
        if ws:
            names.add(" ".join(ws))
    return syms, sorted(names, key=len, reverse=True)


def tickers_of(symbol: str) -> list[str]:
    """the raw ticker and its peeled forms (wrapper prefix / suffix removed only in front of / after an upper-case ticker)"""
    s = (symbol or "").strip().lstrip("$")
    out = [s.upper()]
    for p in PREFIXES:
        if s.startswith(p) and len(s) > len(p) + 0 and s[len(p):len(p) + 1].isupper():
            out.append(s[len(p):].upper())
    for p in ("WT",):                                   # WTHOOD written in capitals
        if s.upper().startswith(p) and len(s) > 3:
            out.append(s[len(p):].upper())
    for x in SUFFIXES:
        if s.endswith(x) and len(s) > len(x) + 1 and s[: -len(x)].isupper():
            out.append(s[: -len(x)].upper())
    return list(dict.fromkeys(t for t in out if t))


def name_words(name: str) -> set[str]:
    return {w for w in words(name) if w not in WRAP_WORDS}


def check(symbol: str, name: str) -> dict:
    """{lookalike: bool, why: str|None, ticker, company}"""
    syms, our_names = _ours()
    us = _load()
    tks = tickers_of(symbol)
    nw = name_words(name)
    for t in tks:
        if t in syms:
            return {"lookalike": True, "why": "our_stock_ticker", "ticker": t, "company": None}
    for t in tks:
        core = us.get(t)
        if core:
            first = core.split(" ")[0]
            if first in nw or (len(first) >= 5 and first in "".join(words(name))):
                return {"lookalike": True, "why": "us_ticker_and_name", "ticker": t, "company": core}
    ws = words(name)
    if set(ws) & MARKERS:                            # «Wrapped Tesla», «Tokenized NVIDIA», «Backed Coinbase Stock» under any ticker
        flat = " " + " ".join(ws) + " "
        for n in our_names:
            if len(n) >= 3 and f" {n} " in flat:
                return {"lookalike": True, "why": "wrapped_our_company", "ticker": None, "company": n}

    return {"lookalike": False, "why": None, "ticker": None, "company": None}


def is_lookalike(symbol: str, name: str) -> bool:
    return check(symbol, name)["lookalike"]
