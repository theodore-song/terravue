"""Pluggable JSON storage.

Uses a cloud key-value store (Vercel KV / Upstash Redis) over its REST API when
the env vars are present, otherwise falls back to local JSON files. This lets the
exact same code run locally (files) and publish to the cloud (KV) so a read-only
Vercel deployment can serve the data.

Recognized env vars (Vercel KV injects the KV_* names automatically):
    KV_REST_API_URL / KV_REST_API_TOKEN
    UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request

from .config import DATA_DIR

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

_URL = (os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
_TOKEN = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
USE_KV = bool(_URL and _TOKEN)


def _command(args: list[str]):
    """Run one Redis command via the Upstash/Vercel-KV REST API."""
    req = urllib.request.Request(
        _URL,
        data=json.dumps(args).encode(),
        headers={"Authorization": f"Bearer {_TOKEN}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_CTX, timeout=15) as resp:
        return json.loads(resp.read().decode()).get("result")


def _pipeline(commands: list[list]):
    """Run many Redis commands in one Upstash REST call."""
    req = urllib.request.Request(
        _URL + "/pipeline",
        data=json.dumps(commands).encode(),
        headers={"Authorization": f"Bearer {_TOKEN}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_CTX, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _file(key: str):
    return DATA_DIR / f"{key}.json"


def write_many(mapping: dict[str, object], chunk: int = 150) -> int:
    """Write many key->obj pairs efficiently (pipelined to KV; files locally)."""
    items = [(k, json.dumps(v)) for k, v in mapping.items()]
    written = 0
    if USE_KV:
        for i in range(0, len(items), chunk):
            batch = items[i:i + chunk]
            try:
                _pipeline([["SET", k, v] for k, v in batch])
                written += len(batch)
            except Exception as exc:
                print(f"[store] pipeline write failed: {exc}")
    for k, v in items:
        try:
            _file(k).write_text(v)
        except OSError:
            pass
    return written or len(items)


def read_json(key: str, default=None):
    if USE_KV:
        try:
            raw = _command(["GET", key])
            if raw:
                return json.loads(raw)
            return default
        except Exception as exc:
            print(f"[store] KV read failed for {key}: {exc}")
    path = _file(key)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def write_json(key: str, obj) -> bool:
    """Write to KV when configured; always also try a local file copy."""
    payload = json.dumps(obj)
    ok = False
    if USE_KV:
        try:
            _command(["SET", key, payload])
            ok = True
        except Exception as exc:
            print(f"[store] KV write failed for {key}: {exc}")
    try:
        _file(key).write_text(payload)
    except OSError:
        pass  # read-only filesystem (e.g. Vercel)
    return ok or USE_KV
