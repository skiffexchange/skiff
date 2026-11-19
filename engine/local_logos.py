"""Logos baked into the build (prod audit 1, S1: on Railway the outside picture hosts — static.debank.com and others — do not answer the
engine's egress, so /api/img 404'd for 34 of 73 ids). tools/bake_logos.py downloads them here with the same checks as /api/img (https,
public host, image type by content-type AND magic bytes, ≤ 2 MB, no SVG):
  web/assets/logos/net/<address>.<png|jpg|webp|gif>     network tokens of /api/network-tokens
  web/assets/logos/chains/<chainId>.<png|jpg|webp|gif>  Pay-from networks of /api/tokens / /api/reach
  web/assets/logos/src/<chainId>-<address>.<ext>        tokens of the other Pay-from networks in /api/tokens (MON, HYPE, USDT0 …)
The engine serves such a file as /assets/logos/<dir>/<file>?v=<content hash> (immutable) and /api/img only for the rest."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time

import config as C

DIRS = {"net": os.path.join(C.WEB, "assets", "logos", "net"), "chains": os.path.join(C.WEB, "assets", "logos", "chains"),
        "src": os.path.join(C.WEB, "assets", "logos", "src")}    # src: tokens of other Pay-from networks, <chainId>-<address>
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/avif": "avif"}
# pictures that show ANOTHER company's brand on a foreign token (no logos of other companies on foreign addresses) — a letter
# instead, whatever source offers one. A rule by image would need perceptual matching; a short reviewed list is enough here.
DENY = {
    ("net", "0x385f4f8ae47651ce5f58f5265395a669f8281e18"),   # MEME «A Meme Coin»: its profile picture is AMC Entertainment's logo
}
_lock = threading.Lock()
_idx: dict = {"at": 0.0, "map": {}}


def _scan() -> dict[tuple[str, str], str]:
    out = {}
    for kind, d in DIRS.items():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            m = re.fullmatch(r"(0x[0-9a-f]{40}|\d{1,10}|\d{1,10}-0x[0-9a-f]{40})\.(png|jpg|webp|gif|avif)", f)
            if not m:
                continue
            try:
                h = hashlib.sha1(open(os.path.join(d, f), "rb").read()).hexdigest()[:10]
            except OSError:
                continue
            out[(kind, m.group(1))] = f"/assets/logos/{kind}/{f}?v={h}"
    return out


def denied(kind: str, key) -> bool:
    return (kind, str(key).lower()) in DENY


def path(kind: str, key) -> str | None:
    """the baked logo of a network token (kind net, key = address) or a chain (kind chains, key = chain id), or None"""
    if denied(kind, key):
        return None
    now = time.time()
    with _lock:
        if now - _idx["at"] > 60:
            _idx.update(at=now, map=_scan())
        return _idx["map"].get((kind, str(key).lower()))


def save(kind: str, key, data: bytes, ctype: str) -> str:
    os.makedirs(DIRS[kind], exist_ok=True)
    for f in os.listdir(DIRS[kind]):                  # one file per key (an older type goes)
        if f.split(".")[0] == str(key).lower():
            os.remove(os.path.join(DIRS[kind], f))
    fn = f"{str(key).lower()}.{EXT[ctype]}"
    tmp = os.path.join(DIRS[kind], fn + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(DIRS[kind], fn))
    with _lock:
        _idx["at"] = 0.0
    return fn
