"""Offline status, inventory freshness, local history and retention."""

import json
import hashlib
import math
from pathlib import Path
import time

from .core import Error, LOG, Store, write_json


IDLE = ("READY", "PROTECTION_RESTORED")


def raw_state(directory):
    path = Path(directory) / "state.json"
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("schema") != 2 or not isinstance(state.get("phase"), str):
        raise Error("Invalid FSDR state; do not use the lab directory")
    return state


def freshness(state, max_age_seconds, now=None):
    timestamp = (state or {}).get("inventory_at")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        return {"status": "missing", "age_seconds": None, "collected_at": None, "max_age_seconds": max_age_seconds}
    age = (time.time() if now is None else now) - timestamp
    status = "future" if age < -60 else "stale" if age > max_age_seconds else "fresh"
    return {"status": status, "age_seconds": round(max(age, 0), 1),
            "collected_at": timestamp, "max_age_seconds": max_age_seconds}


def summary(result, state, max_age_seconds, elapsed, outcome):
    state = state or {}
    tr = state.get("transition", {})
    progress = tr.get("reprotect", {})
    return {"outcome": outcome, "duration_seconds": round(elapsed, 3),
            "phase": state.get("phase"), "active": state.get("active"),
            "source": tr.get("source"), "target": tr.get("target"),
            "index_count": len(state.get("indices", [])),
            "current_step": tr.get("current_step"), "current_index": tr.get("current_index"),
            "completed_steps": tr.get("steps", []),
            "reprotected_indices": sum(item.get("stage") == "synced" for item in progress.values()),
            "inventory": freshness(state, max_age_seconds),
            "rpo": state.get("rpo"),
            "component": "OpenSearch; application and routing are external FSDR steps"}


def record_inventory_attempt(directory, outcome, reason=None, collected_at=None, duration_seconds=None):
    # Separate lock: recording a skipped collection must not compete for the DR lock.
    with Store({"state_dir": str(directory)}).lock(name="inventory-health"):
        path = Path(directory) / "inventory-health.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data.update(last_attempt_at=time.time(), outcome=outcome, reason=reason)
        if isinstance(duration_seconds, (int, float)):
            data["duration_seconds"] = round(max(duration_seconds, 0), 3)
        if outcome == "success":
            data["last_success_at"] = collected_at
        write_json(path, data)


def record_evidence(directory, command, report):
    """Keep local, password-free evidence when a DR mutation reaches completion."""
    directory = Path(directory)
    names = ("state.json", "last-run.json", "inventory-health.json")
    files = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
             for name in names if (directory / name).exists()}
    evidence = {"schema": 1, "created_at": time.time(), "command": command,
                "transition_id": ((raw_state(directory) or {}).get("transition") or {}).get("id"),
                "outcome": report.get("outcome"), "files": files}
    from .core import archive
    archive(directory, "evidence", evidence)
    LOG.debug("evidence_recorded command=%s transition=%s", command, evidence["transition_id"])
    return evidence


def cleanup(directory, log_dir, retention_days):
    state = raw_state(directory)
    if state and state["phase"] not in IDLE:
        return {"skipped": True, "reason": "dr_in_progress", "removed": 0}
    cutoff = time.time() - retention_days * 86400
    active = {Path(handler.baseFilename).resolve() for handler in LOG.handlers if hasattr(handler, "baseFilename")}
    removed = 0
    patterns = [(Path(directory) / "history", "state-*.json"),
                (Path(directory) / "history", "discovery-*.json"),
                (Path(directory) / "history", "run-*.json"),
                (Path(directory) / "history", "evidence-*.json"),
                (Path(directory) / "results", "*.json"),
                (Path(log_dir), "fsdr-*.jsonl*")]
    for root, pattern in patterns:
        if root.is_symlink():
            raise Error("Retention does not traverse symbolic-link directories")
        for path in root.glob(pattern):
            if path.is_symlink() or not path.is_file() or path.resolve() in active:
                continue
            if path.stat().st_mtime < cutoff:
                path.unlink()
                LOG.debug("retention_removed file=%s", path)
                removed += 1
    return {"skipped": False, "removed": removed, "retention_days": retention_days}
