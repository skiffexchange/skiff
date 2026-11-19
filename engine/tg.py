"""Telegram: link a wallet to a chat (t.me/<bot>?start=<code>), deliver big-trade alerts. Transport is pluggable:
  TG_BOT_TOKEN set  -> real Bot API (sendMessage; getUpdates long-poll for /start <code>) — not exercised yet, no token exists;
  unset             -> FakeTransport: messages are written to the tg_outbox table only (tests and local runs).
No message is ever sent from tests. The bot username comes from TG_BOT_NAME (default "<brand>_bot", placeholder until the owner creates it).
OFF by default (back together with the token): TG_ENABLED=1 turns the whole feature on (API routes, poller,
alert delivery, /api/config). Off = /api/me/telegram* and /api/dev/tg/start are 404, no poller even with a token, alerts stay on the site."""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import urllib.parse

import copytext as T
import store as S
from chain import http_json

log = logging.getLogger("rhdex.tg")


def enabled() -> bool:
    return (os.environ.get("TG_ENABLED") or "").strip() == "1" and (os.environ.get("ALERTS_ENABLED") or "").strip() == "1"


def token() -> str:
    return (os.environ.get("TG_BOT_TOKEN") or "").strip()


def bot_name() -> str:
    import re
    return (os.environ.get("TG_BOT_NAME") or re.sub(r"[^a-z0-9]+", "_", T.brand().lower()).strip("_") + "_bot").strip()


class FakeTransport:
    name = "fake"

    def send(self, chat_id: int, text: str) -> bool:
        return True


class BotTransport:
    name = "bot"

    def send(self, chat_id: int, text: str) -> bool:
        try:
            code, j, _ = http_json(f"https://api.telegram.org/bot{token()}/sendMessage",
                                   body={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=15)
            return code == 200 and bool((j or {}).get("ok"))
        except Exception:
            return False


def transport():
    return BotTransport() if token() else FakeTransport()


def send(chat_id: int, text: str) -> bool:
    t = transport()
    ok = t.send(chat_id, text)
    S.x("INSERT INTO tg_outbox (chat_id, text, ts, sent, transport) VALUES (?,?,?,?,?)", (chat_id, text, int(time.time()), int(ok), t.name))
    return ok


def link(wallet: str) -> dict:
    row = S.q("SELECT * FROM tg_links WHERE wallet=?", (wallet,))
    if row and row[0]["chat_id"]:
        return {"linked": True, "bot": bot_name()}
    code = secrets.token_urlsafe(12)
    S.x("INSERT INTO tg_links (wallet, code, code_at) VALUES (?,?,?) ON CONFLICT(wallet) DO UPDATE SET code=excluded.code, code_at=excluded.code_at",
        (wallet, code, int(time.time())))
    return {"linked": False, "bot": bot_name(), "buttonLabel": T.s("app.k.act.telegram"), "url": f"https://t.me/{bot_name()}?start={urllib.parse.quote(code)}", "code": code,
            "expiresInS": 3600, "transport": transport().name}


def status(wallet: str) -> dict:
    row = S.q("SELECT chat_id, linked_at FROM tg_links WHERE wallet=?", (wallet,))
    return {"linked": bool(row and row[0]["chat_id"]), "linkedAt": row[0]["linked_at"] if row else None, "bot": bot_name(),
            "transport": transport().name}


def unlink(wallet: str) -> None:
    S.x("DELETE FROM tg_links WHERE wallet=?", (wallet,))


def handle_start(chat_id: int, code: str) -> bool:
    """/start <code> from Telegram (or the dev endpoint): binds the chat to the wallet that asked for the code within the last hour."""
    row = S.q("SELECT wallet, code_at FROM tg_links WHERE code=?", (code,))
    if not row or time.time() - (row[0]["code_at"] or 0) > 3600:
        send(chat_id, T.s("tg.expired"))
        return False
    S.x("UPDATE tg_links SET chat_id=?, linked_at=?, code=NULL WHERE wallet=?", (chat_id, int(time.time()), row[0]["wallet"]))
    w = row[0]["wallet"]
    send(chat_id, T.s("tg.linked", wallet=f"{w[:6]}…{w[-4:]}"))
    return True


def command(chat_id: int, txt: str) -> None:
    """A message to the bot: /start <code> links; /start alone, /help, /stop, anything else -> copy strings tg.*."""
    if txt.startswith("/start "):
        handle_start(chat_id, txt.split(" ", 1)[1].strip())
    elif txt == "/start":
        send(chat_id, T.s("tg.start_plain"))
    elif txt == "/stop":
        S.x("UPDATE tg_links SET chat_id=NULL, linked_at=NULL WHERE chat_id=?", (chat_id,))
        send(chat_id, T.s("tg.unlinked"))
    elif txt == "/help":
        send(chat_id, T.s("tg.help"))
    else:
        send(chat_id, T.s("tg.unknown"))


def chat_of(wallet: str) -> int | None:
    row = S.q("SELECT chat_id FROM tg_links WHERE wallet=?", (wallet,))
    return row[0]["chat_id"] if row and row[0]["chat_id"] else None


def poll_loop(stop: threading.Event):
    """Real bot only: long-poll getUpdates for /start <code>."""
    if not (enabled() and token()):
        return
    off = int(S.meta_get("tg_offset", "0") or 0)
    while not stop.is_set():
        try:
            code, j, _ = http_json(f"https://api.telegram.org/bot{token()}/getUpdates?timeout=25&offset={off}", timeout=35)
            for upd in (j or {}).get("result") or []:
                off = max(off, upd["update_id"] + 1)
                msg = upd.get("message") or {}
                txt = (msg.get("text") or "").strip()
                chat = (msg.get("chat") or {}).get("id")
                if chat:
                    command(chat, txt)
            S.meta_set("tg_offset", str(off))
        except Exception as e:
            log.warning("tg poll: %s", e)
            stop.wait(10)
