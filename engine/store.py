"""SQLite (WAL, one connection per thread): big trades (24 h), per-minute volume, board snapshots, signed-in features, Telegram, meta."""
from __future__ import annotations

import json
import os
import sqlite3
import threading

import config as C

DB = os.environ.get("RHDEX_DB", os.path.join(C.DATA, "rhdex.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  tx TEXT NOT NULL, stock TEXT NOT NULL, side TEXT NOT NULL, ts INTEGER NOT NULL, block INTEGER NOT NULL,
  usd REAL NOT NULL, amount TEXT NOT NULL, legs INTEGER NOT NULL, venues TEXT NOT NULL, pools TEXT NOT NULL,
  PRIMARY KEY (tx, stock, side)
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades (ts);
CREATE TABLE IF NOT EXISTS volume (minute INTEGER NOT NULL, dex TEXT NOT NULL, stock TEXT NOT NULL, usd REAL NOT NULL, swaps INTEGER NOT NULL,
  PRIMARY KEY (minute, dex, stock));
CREATE TABLE IF NOT EXISTS board (stock TEXT NOT NULL, size INTEGER NOT NULL, at INTEGER NOT NULL, doc TEXT NOT NULL, PRIMARY KEY (stock, size));
CREATE TABLE IF NOT EXISTS watch (wallet TEXT NOT NULL, ticker TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (wallet, ticker));
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT NOT NULL, ticker TEXT NOT NULL, min_usd REAL NOT NULL,
  tg INTEGER NOT NULL DEFAULT 1, at INTEGER NOT NULL, UNIQUE (wallet, ticker));
CREATE TABLE IF NOT EXISTS alert_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER NOT NULL, wallet TEXT NOT NULL, tx TEXT NOT NULL,
  ticker TEXT NOT NULL, usd REAL NOT NULL, side TEXT NOT NULL, ts INTEGER NOT NULL, tg_sent INTEGER NOT NULL DEFAULT 0, UNIQUE (alert_id, tx));
CREATE INDEX IF NOT EXISTS hits_wallet ON alert_hits (wallet, ts);
CREATE TABLE IF NOT EXISTS tg_links (wallet TEXT PRIMARY KEY, chat_id INTEGER, code TEXT UNIQUE, code_at INTEGER, linked_at INTEGER);
CREATE TABLE IF NOT EXISTS tg_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0, transport TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (wallet TEXT NOT NULL, tx TEXT NOT NULL, chain INTEGER NOT NULL, ts INTEGER NOT NULL, status TEXT NOT NULL,
  doc TEXT NOT NULL, PRIMARY KEY (wallet, tx));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def db() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
        c = sqlite3.connect(DB, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        _local.c = c
    return c


def q(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def x(sql: str, args=()):
    return db().execute(sql, args)


def meta_get(k: str, default=None):
    r = db().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(k: str, v) -> None:
    db().execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v if isinstance(v, str) else json.dumps(v)))
