"""Helper for reading and writing research/test_history.json.

Provides:
  - load() / save()
  - hash_params(params) — stable sha1 of a param dict for dedupe
  - record_test(entry) — append a test result, dedupe by hash
  - is_blacklisted(hash) — check 30-day rejection cooldown
  - add_to_blacklist(hash, reason) — record a user rejection
  - sync_rolling_baseline() — refresh rolling baseline from optimized_params.json
  - tests_in_last_n_days(n) — for budget / dedupe checks
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.params import load_strategy_params

HISTORY_FILE = Path(__file__).parent / "test_history.json"
BLACKLIST_COOLDOWN_DAYS = 30


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_params(params: dict) -> str:
    """Stable sha1 hash of a param dict (sorted keys, no whitespace)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "sha1:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def load() -> dict:
    with open(HISTORY_FILE) as f:
        return json.load(f)


def save(data: dict) -> None:
    data["last_updated"] = _utcnow_iso()
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def sync_rolling_baseline(data: dict | None = None) -> dict:
    """Refresh rolling_baseline from current optimized_params.json."""
    if data is None:
        data = load()
    current = load_strategy_params()
    # Add sl_method explicitly so the hash is comparable to anchor
    current_full = dict(current)
    current_full.setdefault("sl_method", "atr")
    data["rolling_baseline"]["params_hash"] = hash_params(current_full)
    data["rolling_baseline"]["params"] = current_full
    data["rolling_baseline"]["synced_at"] = _utcnow_iso()
    return data


def is_blacklisted(data: dict, params_hash: str) -> bool:
    """True if this hash was rejected within the last 30 days."""
    now = datetime.now(timezone.utc)
    for entry in data.get("rejected_blacklist", []):
        if entry["params_hash"] != params_hash:
            continue
        retest_after = datetime.fromisoformat(entry["retest_after"].replace("Z", "+00:00"))
        if now < retest_after:
            return True
    return False


def add_to_blacklist(data: dict, params_hash: str, reason: str) -> None:
    retest_after = datetime.now(timezone.utc) + timedelta(days=BLACKLIST_COOLDOWN_DAYS)
    data["rejected_blacklist"].append({
        "params_hash": params_hash,
        "rejected_at": _utcnow_iso(),
        "reason": reason,
        "retest_after": retest_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def already_tested(data: dict, params_hash: str) -> bool:
    return any(t["params_hash"] == params_hash for t in data.get("tests", []))


def tests_in_last_n_days(data: dict, n: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=n)
    out = []
    for t in data.get("tests", []):
        ts = datetime.fromisoformat(t["tested_at"].replace("Z", "+00:00"))
        if ts >= cutoff:
            out.append(t)
    return out


def next_test_id(data: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = sum(1 for t in data.get("tests", []) if t["id"].startswith(f"T-{today}")) + 1
    return f"T-{today}-{n:03d}"


def record_test(data: dict, entry: dict) -> None:
    """Append a test entry. Caller is responsible for hashing + verdict."""
    data.setdefault("tests", []).append(entry)
    data.setdefault("budget", {}).setdefault("tests_this_quarter", 0)
    data["budget"]["tests_this_quarter"] += 1


def get_anchor_baseline(data: dict) -> dict:
    return data["anchor_baseline"]


def get_rolling_baseline(data: dict) -> dict:
    return data["rolling_baseline"]
