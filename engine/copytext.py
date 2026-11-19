"""Copy strings (the marketer's file, never hard-coded here): ../copy/strings.json next to the repo, or COPY_STRINGS=<path>, or the bundled
engine/copy-strings.json (written by tools/setup_assets.py for a deploy where copy/ is not shipped). Keys are flat dotted paths
("tg.alert"); {brand} = brand.name (BRAND_NAME env overrides), {site} = RHDEX_SITE. Unknown placeholders stay as they are."""
from __future__ import annotations

import json
import os
import string
import threading

import config as C

_lock = threading.Lock()
_cache: dict = {"mtime": None, "flat": {}, "path": None}


def _path() -> str | None:
    for p in (os.environ.get("COPY_STRINGS"), os.path.abspath(os.path.join(C.SITE, "..", "copy", "strings.json")),
              os.path.join(C.HERE, "copy-strings.json")):
        if p and os.path.isfile(p):
            return p
    return None


def _flat(o, pre="", out=None):
    out = {} if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            _flat(v, f"{pre}.{k}" if pre else k, out)
    else:
        out[pre] = o
    return out


def strings() -> dict:
    p = _path()
    mt = os.path.getmtime(p) if p else None
    with _lock:
        if p != _cache["path"] or mt != _cache["mtime"]:
            try:
                _cache.update(flat=_flat(json.load(open(p))) if p else {}, mtime=mt, path=p)
            except Exception:
                pass
        return _cache["flat"]


class _Keep(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def brand() -> str:
    return (os.environ.get("BRAND_NAME") or strings().get("brand.name") or C.BRAND_NAME).strip()


def s(key: str, **kw) -> str:
    t = strings().get(key)
    if not isinstance(t, str):
        return key                                    # visible in tests, never silently invented
    return string.Formatter().vformat(t, (), _Keep({"brand": brand(), "site": C.SITE_URL, **kw}))
