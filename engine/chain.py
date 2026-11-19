"""Chain access: JSON-RPC with UA + 429 backoff, Multicall3 aggregate3, eth_simulateV1, tiny ABI helpers. Read-only: nothing signs or sends.

Official node quirks: refuses the Python-urllib UA; answers 429 to a JSON-RPC batch of eth_call (so bulk reads go through
one Multicall3.aggregate3 call); on a limit it may return ONE error object instead of a list — that is a retry, not data."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import config as C
from keccak import keccak256

STATS = {"rpc_calls": 0, "rpc_errors": 0, "rpc_429": 0, "last_ok": 0.0, "last_err": ""}
_gate = threading.BoundedSemaphore(4)          # at most 4 concurrent requests to the official node


class RpcError(Exception):
    pass


def http_json(url: str, body=None, headers=None, timeout=20, method=None):
    """(status, json|None, headers). Never raises on HTTP status; raises on network errors."""
    h = {"User-Agent": C.UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read() or b"null")
        except Exception:
            j = None
        return e.code, j, dict(e.headers or {})


def rpc(method: str, params: list, url: str | None = None, retries: int = 3, timeout: int = 15):
    url = url or C.RPC
    last = ""
    for i in range(retries):
        STATS["rpc_calls"] += 1
        try:
            with _gate:
                code, d, _ = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
        except Exception as e:                       # network / timeout
            last = f"{type(e).__name__}: {e}"[:200]
            STATS["rpc_errors"] += 1
            time.sleep(0.6 * (i + 1))
            continue
        if code == 429 or (isinstance(d, dict) and "error" in d and ("429" in str(d["error"]) or "rate" in str(d["error"]).lower())):
            STATS["rpc_429"] += 1
            last = "429"
            time.sleep(1.2 * (i + 1))
            continue
        if code >= 500 or not isinstance(d, dict):
            last = f"http {code}"
            STATS["rpc_errors"] += 1
            time.sleep(0.6 * (i + 1))
            continue
        if "error" in d:
            STATS["last_err"] = str(d["error"])[:200]
            raise RpcError(d["error"].get("message") if isinstance(d["error"], dict) else str(d["error"]))
        STATS["last_ok"] = time.time()
        return d.get("result")
    STATS["last_err"] = last
    raise RpcError(f"rpc unavailable: {last}")


def rpc_any(method: str, params: list, retries: int = 3):
    """Plain reads (blockNumber, eth_call, getCode, receipts): official node first, then the public fallback."""
    try:
        return rpc(method, params, retries=retries)
    except RpcError as e:
        if "unavailable" not in str(e):
            raise
        return rpc(method, params, url=C.RPC_FALLBACK, retries=retries)


# ---------------------------------------------------------------- ABI helpers
def pad(x, n: int = 64) -> str:
    if isinstance(x, int):
        return format(x % (1 << 256), "064x")
    return x.lower().replace("0x", "").rjust(n, "0")


def u(h) -> int:
    return int(h, 16) if h and h != "0x" else 0


def s256(x: int) -> int:
    return x - (1 << 256) if x >= 1 << 255 else x


def word_addr(w: str) -> str:
    return "0x" + w[-40:].lower()


def sel(sig: str) -> str:
    return keccak256(sig.encode()).hex()[:8]


def words(hexdata: str) -> list[int]:
    d = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [int(d[i:i + 64], 16) for i in range(0, len(d) - len(d) % 64, 64)]


def enc_bytes_tail(b: bytes) -> str:
    return pad(len(b)) + b.hex().ljust(((len(b) + 31) // 32) * 64, "0")


def mapping_slot(key_addr: str, slot: int) -> str:
    return "0x" + keccak256(bytes.fromhex(pad(key_addr) + pad(slot))).hex()


# ---------------------------------------------------------------- Multicall3
def enc_aggregate3(calls: list[tuple[str, str]]) -> str:
    """aggregate3((address target, bool allowFailure, bytes callData)[]) with allowFailure = true."""
    n = len(calls)
    head = pad(0x20) + pad(n)
    offs, tails, cur = [], [], 32 * n
    for to, data in calls:
        b = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        t = pad(to) + pad(1) + pad(0x60) + enc_bytes_tail(b)
        offs.append(pad(cur))
        tails.append(t)
        cur += len(t) // 2
    return "0x82ad56cb" + head + "".join(offs) + "".join(tails)


def dec_aggregate3(ret: str) -> list[tuple[bool, str]]:
    d = bytes.fromhex(ret[2:])
    def w(o):
        return int.from_bytes(d[o:o + 32], "big")
    base = w(0)
    n = w(base)
    out = []
    for i in range(n):
        eo = base + 32 + w(base + 32 + 32 * i)
        ok = w(eo) != 0
        bo = eo + w(eo + 32)
        ln = w(bo)
        out.append((ok, "0x" + d[bo + 32:bo + 32 + ln].hex()))
    return out


def multicall(calls: list[tuple[str, str]], block: str = "latest", chunk: int = 120) -> list[tuple[bool, str]]:
    out = []
    for i in range(0, len(calls), chunk):
        part = calls[i:i + chunk]
        r = rpc_any("eth_call", [{"to": C.MC3, "data": enc_aggregate3(part)}, block])
        out.extend(dec_aggregate3(r))
    return out


def block_number() -> int:
    """short: the first call of every quote; a slow node must fail fast into chain_unavailable (audit v2 minor 6)"""
    try:
        return u(rpc("eth_blockNumber", [], retries=2, timeout=6))
    except RpcError:
        return u(rpc("eth_blockNumber", [], url=C.RPC_FALLBACK, retries=1, timeout=6))


# ---------------------------------------------------------------- simulation
def simulate(calls: list[dict], overrides: dict | None = None, block: str = "latest") -> dict:
    """eth_simulateV1, one block, validation off. Returns {"block": n, "calls": [...]}. Raises RpcError."""
    req = [{"blockStateCalls": [{"stateOverrides": overrides or {}, "calls": calls}], "validation": False, "traceTransfers": False}, block]
    res = rpc("eth_simulateV1", req, retries=3, timeout=40)
    b = res[0]
    return {"block": u(b.get("number")), "timestamp": u(b.get("timestamp")), "calls": b.get("calls") or []}


def approve_data(spender: str, amount: int) -> str:
    return "0x095ea7b3" + pad(spender) + pad(amount)


def balance_of_data(owner: str) -> str:
    return "0x70a08231" + pad(owner)


def allowance_data(owner: str, spender: str) -> str:
    return "0xdd62ed3e" + pad(owner) + pad(spender)


def enc_execute_batch(calls: list[tuple[str, int, str]]) -> str:
    """Simple7702Account.executeBatch((address target,uint256 value,bytes data)[]) — selector 0x34fcd5be (research 2.5, probe/batch7702.py)."""
    n = len(calls)
    head = pad(0x20) + pad(n)
    offs, tails, cur = [], [], 32 * n
    for to, val, data in calls:
        b = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        body = pad(to) + pad(val) + pad(0x60) + enc_bytes_tail(b)
        offs.append(pad(cur))
        tails.append(body)
        cur += len(body) // 2
    return "0x34fcd5be" + head + "".join(offs) + "".join(tails)


# ---------------------------------------------------------------- balance slot discovery (for simulation overrides)
_SLOT_CANDIDATES = list(range(0, 12)) + [51, 101, 151, 201]
OZ7201_ERC20 = int("52c63247e1f47db19d5ce0460030c497f067ca4cebf71ba98eeadabe20bace00", 16)   # ERC20Upgradeable (OZ 5) namespaced base
_slot_cache: dict[str, int | None] = {C.USDG: 1, C.WETH: 51}
_slot_lock = threading.Lock()


def balance_slot(token: str) -> int | None:
    """The storage slot of `balances` for an ERC20, found in ONE eth_call: every candidate slot gets a different marker value through a
    stateDiff override and balanceOf(probe) tells which one the contract reads. Cached per token (None = not a plain mapping)."""
    token = token.lower()
    with _slot_lock:
        if token in _slot_cache:
            return _slot_cache[token]
    cands = _SLOT_CANDIDATES + [OZ7201_ERC20]
    diff = {mapping_slot(C.PROBE, s): "0x" + pad(1_000_003 + i) for i, s in enumerate(cands)}
    found = None
    try:
        r = rpc_any("eth_call", [{"to": token, "data": balance_of_data(C.PROBE)}, "latest", {token: {"stateDiff": diff}}])
        v = u(r[:66]) if r and len(r) >= 66 else 0
        if 1_000_003 <= v < 1_000_003 + len(cands):
            found = cands[v - 1_000_003]
    except Exception:
        return None                                   # do not cache a network failure
    with _slot_lock:
        _slot_cache[token] = found
    return found


def balance_override(token: str, owner: str, amount: int) -> dict | None:
    """stateOverrides entry that gives `owner` `amount` of `token` (native = account balance)."""
    if token.lower() == C.NATIVE:
        return {owner: {"balance": hex(amount + 10 ** 18)}}
    slot = balance_slot(token)
    if slot is None:
        return None
    return {token: {"stateDiff": {mapping_slot(owner, slot): "0x" + pad(amount)}}}
