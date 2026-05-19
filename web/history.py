"""Disk-backed analysis history for the web backend.

The job manager is in-memory only, so a backend restart loses every result.
This module persists each completed analysis to reports/web/<ID>/report.md and
keeps a reports/web/history.json index, so past analyses survive restarts and
can be browsed from any device. The index is written atomically (temp file +
os.replace) so a concurrent reader never sees a half-written file.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = _REPO_ROOT / "reports" / "web"
_INDEX = HISTORY_DIR / "history.json"
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_HISTORY = 200  # cap retained analyses; oldest dirs are pruned on save


def _safe_id(entry_id: str) -> bool:
    """Reject anything that isn't a plain id (path-traversal hardening)."""
    return bool(_ID_RE.match(entry_id)) and ".." not in entry_id


def list_history(limit: int | None = None) -> list[dict]:
    """Return history entries, newest first. Empty/corrupt index -> []."""
    if not _INDEX.exists():
        return []
    try:
        data = json.loads(_INDEX.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []
    return items[:limit] if limit else items


def get_report(entry_id: str) -> str | None:
    """Return the stored markdown for an entry, or None if missing/invalid."""
    if not _safe_id(entry_id):
        return None
    f = HISTORY_DIR / entry_id / "report.md"
    if not f.exists():
        return None
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return None


def save_analysis(ticker: str, date: str, action: str, report_md: str) -> dict:
    """Persist one completed analysis and prepend it to the index."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    # Short random suffix so two analyses in the same second can't collide
    # on the id / overwrite each other's directory.
    entry_id = f"{ticker}_{ts.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d = HISTORY_DIR / entry_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(report_md, encoding="utf-8")
    entry = {
        "id": entry_id,
        "ticker": ticker,
        "date": date,
        "action": action,
        "created_at": ts.isoformat(timespec="seconds"),
    }
    items = list_history()
    items.insert(0, entry)
    # Cap retained history: drop the oldest entries beyond MAX_HISTORY and
    # delete their report dirs so disk does not grow without bound.
    if len(items) > MAX_HISTORY:
        for old in items[MAX_HISTORY:]:
            oid = old.get("id", "")
            if _safe_id(oid):
                shutil.rmtree(HISTORY_DIR / oid, ignore_errors=True)
        items = items[:MAX_HISTORY]
    _atomic_write_index(items)
    return entry


def _atomic_write_index(items: list[dict]) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(HISTORY_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _INDEX)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
