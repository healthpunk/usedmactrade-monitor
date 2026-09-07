"""Persistent state: the set of listings we have already seen.

Stored as a small JSON file that the GitHub Actions workflow commits back to
the repository, which is what makes it durable between runs.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from parser import Listing

log = logging.getLogger(__name__)

STATE_VERSION = 1
STATUS_BASELINE = "baseline"   # seen on the very first run; never notified
STATUS_PENDING = "pending"     # matched, Telegram send still outstanding
STATUS_SENT = "sent"
STATUS_NO_MATCH = "no_match"
STATUS_EXPIRED = "expired"     # pending for too long; dropped without sending


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_state() -> dict:
    return {"version": STATE_VERSION, "initialized_at": None, "last_checked": None,
            "last_alert_at": None, "listings": {}}


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        log.info("no state file at %s; starting fresh", path)
        return empty_state()
    with open(path, encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return empty_state()
    state = json.loads(raw)
    if state.get("version") != STATE_VERSION:
        raise RuntimeError(f"unsupported state version {state.get('version')!r} in {path}")
    state.setdefault("listings", {})
    return state


def save_state(path: str, state: dict) -> None:
    """Atomic write so a crash mid-write never corrupts the file."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def is_first_run(state: dict) -> bool:
    return not state.get("initialized_at") and not state.get("listings")


@dataclass
class Changes:
    new: list[Listing] = field(default_factory=list)
    price_changed: list[tuple[dict, Listing]] = field(default_factory=list)   # (old entry, current)
    gone: list[str] = field(default_factory=list)


def diff_listings(state: dict, current: list[Listing]) -> Changes:
    """Compare the live inventory with what the state already knows."""
    known = state["listings"]
    changes = Changes()
    seen_ids = set()
    for listing in current:
        seen_ids.add(listing.id)
        old = known.get(listing.id)
        if old is None:
            changes.new.append(listing)
        elif listing.price is not None and old.get("price") is not None and listing.price != old["price"]:
            changes.price_changed.append((old, listing))
    previous = state.get("last_checked")
    changes.gone = [pid for pid, e in known.items()
                    if pid not in seen_ids and previous and e.get("last_seen") == previous]
    return changes


def record_listings(state: dict, current: list[Listing], *, status_for_new: str, now: str) -> None:
    """Upsert every live listing; new ones get ``status_for_new``."""
    known = state["listings"]
    for listing in current:
        entry = known.get(listing.id)
        if entry is None:
            entry = {**listing.to_dict(), "first_seen": now, "status": status_for_new,
                     "notified_at": None, "match_reasons": []}
            known[listing.id] = entry
        else:
            # keep the enriched spec fields we already have; refresh volatile ones
            for k in ("title", "url", "price", "currency", "image_url"):
                v = getattr(listing, k)
                if v is not None:
                    entry[k] = v
        entry["last_seen"] = now
    state["last_checked"] = now
    if not state.get("initialized_at"):
        state["initialized_at"] = now


VOLATILE_KEYS = {"last_checked", "last_seen"}


def digest(state: dict) -> str:
    """Canonical JSON of everything except per-run timestamps. Two states with
    equal digests describe the same knowledge, so the file need not be rewritten
    (and the workflow need not commit)."""
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in VOLATILE_KEYS}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    return json.dumps(strip(state), sort_keys=True, ensure_ascii=False)


def pending_entries(state: dict) -> list[dict]:
    return [e for e in state["listings"].values() if e.get("status") == STATUS_PENDING]
