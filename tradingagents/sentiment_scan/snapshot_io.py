"""Atomic JSON snapshot read/write for the sentiment-scan cross-process state."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def save_snapshot(path: str, snapshot: dict) -> None:
    """Atomically write `snapshot` to `path` via tmp + rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)  # atomic on POSIX


def load_snapshot(path: str) -> dict | None:
    """Return snapshot dict, or None on missing/malformed/schema-mismatch.

    Logs at WARNING on every failure mode so an operator can diagnose.
    """
    p = Path(path)
    if not p.exists():
        _log.warning("snapshot not found at %s", path)
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("failed to read snapshot %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        _log.warning("snapshot %s is not a dict", path)
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        _log.warning(
            "snapshot %s schema_version=%r != expected %r",
            path, data.get("schema_version"), SCHEMA_VERSION,
        )
        return None
    return data
