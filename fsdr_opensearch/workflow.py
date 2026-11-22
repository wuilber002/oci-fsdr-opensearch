"""Durable, API-facing gates for the planned FSDR switchover workflow.

The OpenSearch operations remain the source of truth for each action.  This
module records their API outcomes separately and turns the persisted state plus
that audit trail into explicit, explainable submission gates for the UI/API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Iterable

from .core import write_json


WORKFLOW_STEPS = (
    ("inventory", "Inventory"),
    ("validate-switchover", "Validate switchover"),
    ("switchover", "Switchover"),
    ("validate-post-switchover", "Validate post-switchover"),
    ("reprotect", "Reprotect"),
)
WORKFLOW_COMMANDS = {command for command, _ in WORKFLOW_STEPS}
STABLE_PHASES = {"READY", "PROTECTION_RESTORED"}
INVENTORY_MAX_AGE_SECONDS = 60 * 60
LEDGER_FILE = "workflow.json"


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _ledger_path(state_dir: Path) -> Path:
    return Path(state_dir) / LEDGER_FILE


def read_ledger(state_dir: Path) -> dict[str, Any]:
    try:
        import json
        data = json.loads(_ledger_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    events = data.get("events") if isinstance(data, dict) else None
    return {"schema": 1, "events": events if isinstance(events, list) else []}


def record_event(state_dir: Path, job: dict[str, Any], state: dict[str, Any] | None) -> None:
    """Append a terminal workflow result without changing controller state."""
    request = job.get("request") or {}
    command = request.get("command")
    if command not in WORKFLOW_COMMANDS or job.get("status") not in {"succeeded", "failed", "interrupted"}:
        return
    ledger = read_ledger(state_dir)
    event = {
        "job_id": job.get("id"), "command": command, "status": job.get("status"),
        "submitted_at": job.get("submitted_at"), "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"), "exit_code": job.get("exit_code"),
        "reason": job.get("reason"), "phase": (state or {}).get("phase"),
        "inventory_at": (state or {}).get("inventory_at"),
    }
    events = [item for item in ledger["events"] if item.get("job_id") != event["job_id"]]
    event["sequence"] = max((item.get("sequence", 0) for item in events), default=0) + 1
    events.append(event)
    ledger["events"] = events[-200:]
    write_json(_ledger_path(state_dir), ledger)


def _event_order(event: dict[str, Any]) -> tuple[float, int]:
    return (_epoch(event.get("finished_at")) or 0, int(event.get("sequence", 0) or 0))


def _latest(events: Iterable[dict[str, Any]], command: str) -> dict[str, Any] | None:
    matched = [event for event in events if event.get("command") == command]
    return max(matched, key=_event_order, default=None)


def _last_success(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    matched = [event for event in events if event.get("status") == "succeeded"]
    return max(matched, key=_event_order, default=None)


def _requirement(key: str, met: bool, detail: str, blocked: bool = False) -> dict[str, Any]:
    return {"key": key, "met": met, "detail": detail, "level": "blocked" if blocked and not met else "waiting"}


def _fresh_inventory(state: dict[str, Any], now: float) -> tuple[bool, str]:
    inventory_at = state.get("inventory_at")
    if not isinstance(inventory_at, (int, float)):
        return False, "No completed inventory is available."
    age = max(0, now - inventory_at)
    if age <= INVENTORY_MAX_AGE_SECONDS:
        return True, "Inventory is fresh ({:.0f} min of 60 min allowed).".format(age / 60)
    return False, "Inventory expired ({:.0f} min old; maximum is 60 min).".format(age / 60)


def _current_jobs(jobs: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for job in jobs:
        command = (job.get("request") or {}).get("command")
        if command in WORKFLOW_COMMANDS and job.get("status") in {"queued", "running"}:
            current[command] = job
    return current


def workflow_snapshot(state_dir: Path, state: dict[str, Any] | None, jobs: Iterable[dict[str, Any]] = (),
                      now: float | None = None) -> dict[str, Any]:
    """Return UI/API-ready workflow gates using only durable local records."""
    state = state or {}
    now = time.time() if now is None else now
    events = read_ledger(state_dir)["events"]
    jobs = list(jobs)
    active_jobs = [job for job in jobs if job.get("status") in {"queued", "running"}]
    current = _current_jobs(active_jobs)
    controller_busy = bool(active_jobs)
    phase = state.get("phase") or "READY"
    fresh, fresh_detail = _fresh_inventory(state, now)
    validate = _latest(events, "validate-switchover")
    switch = _latest(events, "switchover")
    post = _latest(events, "validate-post-switchover")
    last_success = _last_success(events)
    inventory_at = state.get("inventory_at")

    stable = phase in STABLE_PHASES
    stable_detail = ("FSDR state is {} and accepts this preparation step.".format(phase)
                     if stable else "FSDR state is {}; a stable protected state is required.".format(phase))
    validation_current = bool(validate and validate.get("status") == "succeeded"
                              and isinstance(inventory_at, (int, float))
                              and validate.get("inventory_at") == inventory_at)
    validation_detail = ("Validate switchover succeeded for the current inventory."
                         if validation_current else "Validate switchover must succeed for the current inventory.")
    switched_last = bool(switch and switch.get("status") == "succeeded"
                         and last_success and last_success.get("job_id") == switch.get("job_id"))
    switch_detail = ("The latest successful workflow step is switchover."
                     if switched_last else "Switchover must be the latest successful workflow step.")
    post_after_switch = bool(post and post.get("status") == "succeeded" and switch
                              and (_epoch(post.get("finished_at")) or 0) >= (_epoch(switch.get("finished_at")) or 0))
    post_detail = ("Post-switchover validation succeeded after switchover."
                   if post_after_switch else "Validate post-switchover must succeed after switchover.")
    service_restored = phase == "SERVICE_RESTORED"
    service_detail = ("FSDR state is SERVICE_RESTORED." if service_restored
                      else "FSDR state is {}; SERVICE_RESTORED is required.".format(phase))

    rules = {
        "inventory": [
            _requirement("stable-state", stable, stable_detail, blocked=not stable),
        ],
        "validate-switchover": [
            _requirement("fresh-inventory", fresh, fresh_detail),
            _requirement("stable-state", stable, stable_detail, blocked=not stable),
        ],
        "switchover": [
            _requirement("fresh-inventory", fresh, fresh_detail),
            _requirement("validate-switchover", validation_current, validation_detail),
            _requirement("stable-state", stable, stable_detail, blocked=not stable),
        ],
        "validate-post-switchover": [
            _requirement("service-restored", service_restored, service_detail, blocked=not service_restored),
            _requirement("switchover-last", switched_last, switch_detail),
        ],
        "reprotect": [
            _requirement("service-restored", service_restored, service_detail, blocked=not service_restored),
            _requirement("post-validation", post_after_switch, post_detail),
        ],
    }
    steps = []
    for command, label in WORKFLOW_STEPS:
        requirements = list(rules[command])
        active = current.get(command)
        latest = _latest(events, command)
        eligible = all(item["met"] for item in requirements) and not controller_busy
        if controller_busy and not active:
            requirements.insert(0, _requirement("controller-idle", False,
                "Another FSDR workflow step is queued or running."))
        elif active:
            requirements.insert(0, _requirement("controller-idle", True,
                "This workflow step is currently {}.".format(active.get("status"))))
        else:
            requirements.insert(0, _requirement("controller-idle", True, "No workflow step is running."))
        if active:
            status, detail = ("running" if active.get("status") == "running" else "queued",
                              "This step is currently {}.".format(active.get("status")))
        elif latest and latest.get("status") in {"failed", "interrupted"}:
            status = latest["status"]
            detail = latest.get("reason") or "The latest execution did not complete successfully."
        elif eligible:
            status, detail = "ready", "All prerequisites are met; this step can be submitted."
        elif any(item["level"] == "blocked" for item in requirements if not item["met"]):
            status, detail = "blocked", "A required controller state is incompatible with this step."
        else:
            status, detail = "waiting", "Waiting for prerequisites."
        steps.append({"command": command, "label": label, "status": status, "status_detail": detail,
                      "eligible": eligible, "requirements": requirements,
                      "last_event": latest})
    return {"phase": phase, "inventory_max_age_seconds": INVENTORY_MAX_AGE_SECONDS,
            "steps": steps, "events": events[-50:]}


def step_for(snapshot: dict[str, Any], command: str) -> dict[str, Any] | None:
    return next((step for step in snapshot.get("steps", []) if step.get("command") == command), None)
