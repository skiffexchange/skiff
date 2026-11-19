"""Any token address on Robinhood Chain. Ported from an earlier in-house swap server (_erc20, _abi_str, _sym, the route API token lookup,
_img / fetch_image), adapted:
  - symbol / name / decimals straight from the contract (one multicall), cached in memory + the meta table;
  - picture: the launch record first, then the router's token list (logoURI) — never shown as an outside URL: the page gets
    /api/img/<id> (an opaque id), the engine fetches it once (https, public host, no blind redirects, image type, size cap) and caches it on disk;
  - our listed tokens are never substituted onto a foreign address: a contract whose symbol equals a listed ticker gets `clash: true`,
    its own (cleaned) symbol, no stock logo, no stock name."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config as C
import market as M
import store as S
from chain import RpcError, multicall, rpc_any

ADDR = re.compile(r"0x[0-9a-f]{40}")
_mem: dict[str, tuple[float, dict | None]] = {}       # addr -> (at, doc | None = not a token)
_lock = threading.Lock()
_inflight: dict[str, threading.Lock] = {}
TTL_OK, TTL_MISS = 6 * 3600, 600


class NotToken(Exception):
    pass


class ChainDown(Exception):
    pass


def _abi_str(h: str) -> str:
    b = bytes.fromhex(h[2:] if h.startswith("0x") else h) if h else b""
    if not b:
        return ""
    if len(b) >= 96 and int.from_bytes(b[:32], "big") == 32:        # dynamic string: offset, length, bytes
        n = int.from_bytes(b[32:64], "big")
        return b[64:64 + min(n, 256)].decode("utf-8", "ignore")
    return b[:32].rstrip(b"\0").decode("utf-8", "ignore")            # bytes32 symbol


def clean_symbol(s: str) -> str:
    return re.sub(r"[^\w.$-]", "", s or "")[:16] or "TOKEN"


def clean_name(s: str) -> str:
    s = re.sub(r"[\x00-\x1f\x7f<>\"'`&]", "", s or "")
    return re.sub(r"\s+", " ", s).strip()[:48]


# ------------------------------------------------------------------ pictures (/api/img/<id>)
IMG_DIR = os.path.join(C.DATA, "img")
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}   # no SVG: scripts inside are not worth the risk
IMG_MAX = 2_000_000
IMG_DIR_CAP = 100 * 1024 * 1024
IPFS = ["https://ipfs.filebase.io/ipfs/", "https://4everland.io/ipfs/", "https://ipfs.io/ipfs/"]
_img_src: dict[str, str] = {}                          # id -> source URL (also kept in meta, so ids survive a restart)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _public_ips(h: str) -> list[str]:
    """every address the name resolves to, once; [] when any of them is not a public (global) address"""
    try:
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)})
        return ips if ips and all(ipaddress.ip_address(ip).is_global for ip in ips) else []
    except (socket.gaierror, ValueError, OSError):
        return []


def _public_host(h: str) -> bool:
    return bool(_public_ips(h))


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to one already-checked IP: TCP goes to that address, TLS (SNI + certificate check) is for the host name. No second DNS
    lookup between the check and the connection — no DNS rebinding to an internal address (audit v4 M19)."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get_pinned(u: str, timeout: float = 8) -> tuple[int, dict, bytes]:
    pu = urllib.parse.urlsplit(u)
    host = pu.hostname or ""
    ips = _public_ips(host)
    if not ips:
        raise OSError("not a public host")
    conn = _PinnedHTTPS(host, ips[0], timeout)
    try:
        path = (pu.path or "/") + ("?" + pu.query if pu.query else "")
        conn.request("GET", path, headers={"User-Agent": C.UA, "Accept": "image/*", "Host": host})
        r = conn.getresponse()
        hdr = {k.lower(): v for k, v in r.getheaders()}
        data = r.read(IMG_MAX + 1) if r.status == 200 else b""
        return r.status, hdr, data
    finally:
        conn.close()


def _download(u: str) -> tuple[bytes, str] | None:
    for _hop in range(4):
        pu = urllib.parse.urlparse(u)
        host = pu.hostname or ""
        if pu.scheme != "https" or not host or pu.port not in (None, 443):
            return None
        try:
            status, hdr, data = _get_pinned(u)
        except (OSError, http.client.HTTPException, ValueError, ssl.SSLError):
            return None
        if status in (301, 302, 303, 307, 308) and hdr.get("location"):
            u = urllib.parse.urljoin(u, hdr["location"])
            continue
        if status != 200:
            return None
        ctype = (hdr.get("content-type") or "").split(";")[0].strip().lower()
        break
    else:
        return None
    if ctype not in IMG_TYPES or len(data) > IMG_MAX or not _magic_ok(data, ctype):
        return None
    return data, ctype


def _magic_ok(b: bytes, ctype: str) -> bool:
    """The bytes must be the image type the host claims (a text/HTML body labelled image/png is refused)."""
    return {"image/png": b[:8] == b"\x89PNG\r\n\x1a\n", "image/jpeg": b[:3] == b"\xff\xd8\xff", "image/gif": b[:6] in (b"GIF87a", b"GIF89a"),
            "image/webp": b[:4] == b"RIFF" and b[8:12] == b"WEBP", "image/avif": b[4:12] in (b"ftypavif", b"ftypavis")}.get(ctype, False)


def img_url(src: str | None) -> str | None:
    """An outside picture as our own opaque path; None for anything that isn't https:// or ipfs://."""
    if not src or not isinstance(src, str) or len(src) > 600 or not src.startswith(("https://", "ipfs://")):
        return None
    iid = hashlib.sha256(src.encode()).hexdigest()[:24]
    if iid not in _img_src:
        _img_src[iid] = src
        try:
            S.meta_set("img:" + iid, src)
        except Exception:
            pass
    return "/api/img/" + iid


def fetch_image(iid: str) -> tuple[bytes, str] | None:
    if not re.fullmatch(r"[0-9a-f]{24}", iid or ""):
        return None
    src = _img_src.get(iid) or S.meta_get("img:" + iid)
    if not src:
        return None
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, iid)
    if os.path.exists(path) and os.path.exists(path + ".type"):
        try:
            return open(path, "rb").read(), open(path + ".type").read().strip()
        except OSError:
            pass
    if os.path.exists(path + ".miss") and time.time() - os.path.getmtime(path + ".miss") < 600:
        return None
    lk = _inflight.setdefault("img:" + iid, threading.Lock())
    with lk:
        if os.path.exists(path) and os.path.exists(path + ".type"):
            return open(path, "rb").read(), open(path + ".type").read().strip()
        m = re.search(r"/ipfs/([A-Za-z0-9]{46,})", src)
        cid = src[7:].lstrip("/").split("?")[0] if src.startswith("ipfs://") else (m.group(1) if m else None)
        urls = ([src] if src.startswith("https://") else []) + ([g + cid for g in IPFS] if cid else [])
        got = None
        for u in urls:
            got = _download(u)
            if got:
                break
        _inflight.pop("img:" + iid, None)
        if not got:
            open(path + ".miss", "w").close()
            return None
        _cap_dir()
        with open(path, "wb") as f:
            f.write(got[0])
        with open(path + ".type", "w") as f:
            f.write(got[1])
        return got


def _cap_dir():
    try:
        files = []
        for f in os.listdir(IMG_DIR):
            try:
                st = os.stat(os.path.join(IMG_DIR, f))
                files.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
        total = sum(z for _, z, _f in files)
        for _, z, f in sorted(files):
            if total < IMG_DIR_CAP:
                break
            try:
                os.remove(os.path.join(IMG_DIR, f))
                total -= z
            except OSError:
                pass
    except OSError:
        pass


# ------------------------------------------------------------------ metadata
def _launch_image(addr: str) -> str | None:
    """optional metadata source (LAUNCH_META_URL with {} = address): the launch record's picture; off when the variable is empty"""
    if not C.LAUNCH_META_URL or "{}" not in C.LAUNCH_META_URL:
        return None
    try:
        req = urllib.request.Request(C.LAUNCH_META_URL.format(addr), headers={"User-Agent": C.UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            j = json.loads(r.read(200_000) or b"null") or {}
        if (j.get("address") or "").lower() != addr:
            return None
        return j.get("artwork") or j.get("image") or None
    except Exception:
        return None


def _router_logo(addr: str) -> str | None:
    try:
        import router
        j = router.tokens([C.CHAIN])                                   # one list call, cached 5 min by router.call
        for t in (j.get("tokens") or {}).get(str(C.CHAIN), []):
            if (t.get("address") or "").lower() == addr:
                return t.get("logoURI") or None
    except Exception:
        return None
    return None


def _read(addr: str) -> dict:
    try:
        code = rpc_any("eth_getCode", [addr, "latest"])
    except (RpcError, OSError, ValueError) as e:
        raise ChainDown(str(e)) from e
    if code in (None, "0x", "0x0"):
        raise NotToken("no contract at this address")
    try:
        r = multicall([(addr, "0x06fdde03"), (addr, "0x95d89b41"), (addr, "0x313ce567"), (addr, "0x18160ddd")])
    except (RpcError, OSError, ValueError) as e:
        raise ChainDown(str(e)) from e
    (ok_n, n), (ok_s, s), (ok_d, d), (ok_t, ts) = r
    if not (ok_s and ok_d and ok_t) or len(d) < 66 or len(ts) < 66:
        raise NotToken("not an ERC-20")
    dec = int(d[2:66], 16)
    if dec > 36:
        raise NotToken("decimals out of range")
    sym_raw = _abi_str(s)
    sym = clean_symbol(sym_raw)
    listed_syms = {x["symbol"].upper() for x in M.STOCKS}
    src = _launch_image(addr) or _router_logo(addr)
    return {"chainId": C.CHAIN, "address": addr, "symbol": sym, "name": clean_name(_abi_str(n) if ok_n else "") or sym,
            "decimals": dec, "logo": img_url(src), "listed": False, "kind": "custom", "clash": sym.upper() in listed_syms}


def lookup(addr: str) -> dict:
    """Listed token -> our own record (listed: true). Else the contract read (listed: false), cached. Raises NotToken / ChainDown."""
    a = (addr or "").strip().lower()
    if not ADDR.fullmatch(a):
        raise ValueError("bad address")
    import tokens as TK
    known = TK.find_listed(C.CHAIN, a)
    if known:
        return {"chainId": C.CHAIN, "address": a, **known, "listed": True, "clash": False}
    now = time.time()
    with _lock:
        hit = _mem.get(a)
    if hit and now - hit[0] < (TTL_OK if hit[1] else TTL_MISS):
        if hit[1] is None:
            raise NotToken("cached")
        return hit[1]
    if not hit:
        try:
            v = S.meta_get("ctok:" + a)
            if v:
                at, doc = json.loads(v)
                if now - at < TTL_OK:
                    with _lock:
                        _mem[a] = (at, doc)
                    return doc
        except Exception:
            pass
    lk = _inflight.setdefault(a, threading.Lock())
    with lk:
        with _lock:
            hit = _mem.get(a)
        if hit and time.time() - hit[0] < 5:
            if hit[1] is None:
                raise NotToken("cached")
            return hit[1]
        try:
            doc = _read(a)
        except NotToken:
            with _lock:
                _mem[a] = (time.time(), None)
            raise
        finally:
            _inflight.pop(a, None)
        with _lock:
            _mem[a] = (time.time(), doc)
            if len(_mem) > 20_000:
                _mem.clear()
        try:
            S.meta_set("ctok:" + a, json.dumps([time.time(), doc]))
        except Exception:
            pass
        return doc


def cached(addr: str) -> dict | None:
    """What quote/build may use without a chain read: a token someone already looked up."""
    a = (addr or "").lower()
    with _lock:
        hit = _mem.get(a)
    if hit and hit[1]:
        return hit[1]
    try:
        v = S.meta_get("ctok:" + a)
        return json.loads(v)[1] if v else None
    except Exception:
        return None
