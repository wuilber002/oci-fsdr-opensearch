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
FAILOVER_STEP = ("failover", "Failover")
ALL_WORKFLOW_STEPS = WORKFLOW_STEPS[:3] + (FAILOVER_STEP,) + WORKFLOW_STEPS[3:]
WORKFLOW_COMMANDS = {command for command, _ in ALL_WORKFLOW_STEPS}
RECOVERY_PATHS = (
    {"id": "switchover", "label": "Planned switchover",
     "steps": ("inventory", "validate-switchover", "switchover")},
    {"id": "failover", "label": "Emergency failover", "steps": ("failover",)},
    {"id": "shared", "label": "Shared recovery",
     "steps": ("validate-post-switchover", "reprotect")},
)
STABLE_PHASES = {"READY", "PROTECTION_RESTORED"}
INVENTORY_MAX_AGE_SECONDS = 60 * 60
RECOVERY_CYCLE_RESET_DELAY_SECONDS = 10
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
    evidence = job.get("workflow_evidence") or {}
    if isinstance(evidence.get("requirements"), list):
        # Preserve the gates that admitted this execution. A later phase
        # change is not allowed to repaint a completed historical step.
        event["requirements"] = evidence["requirements"]
        event["requirements_captured_at"] = evidence.get("captured_at")
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


def _requirement(key: str, met: bool, detail: str, explanation: str,
                 blocked: bool = False) -> dict[str, Any]:
    """Build a UI/API gate with a short condition and operational rationale."""
    return {
        "key": key, "met": met, "detail": detail, "explanation": explanation,
        "level": "blocked" if blocked and not met else "waiting",
    }


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
                      now: float | None = None, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI/API-ready workflow gates using only durable local records."""
    state = state or {}
    candidate = candidate or {}
    now = time.time() if now is None else now
    events = read_ledger(state_dir)["events"]
    jobs = list(jobs)
    active_jobs = [job for job in jobs if job.get("status") in {"queued", "running"}]
    current = _current_jobs(active_jobs)
    controller_busy = bool(active_jobs)
    phase = state.get("phase") or "READY"
    completed_cycle = (_latest(events, "reprotect") if phase == "PROTECTION_RESTORED" else None)
    completed_cycle = (completed_cycle if completed_cycle and completed_cycle.get("status") == "succeeded"
                       else None)
    completed_at = _epoch((completed_cycle or {}).get("finished_at"))
    cycle_reset_pending_until = (completed_at + RECOVERY_CYCLE_RESET_DELAY_SECONDS
                                 if completed_at is not None else None)
    reset_pending = bool(cycle_reset_pending_until and now < cycle_reset_pending_until)
    cycle_boundary = completed_cycle if completed_cycle and not reset_pending else None
    cycle_events = ([event for event in events if _event_order(event) > _event_order(cycle_boundary)]
                    if cycle_boundary else events)
    fresh, fresh_detail = _fresh_inventory(state, now)
    cycle_inventory = _latest(cycle_events, "inventory")
    if cycle_boundary and not (cycle_inventory and cycle_inventory.get("status") == "succeeded"):
        fresh = False
        fresh_detail = "Start the new recovery cycle with inventory."
    validate = _latest(cycle_events, "validate-switchover")
    switch = _latest(cycle_events, "switchover")
    failover = _latest(cycle_events, "failover")
    post = _latest(cycle_events, "validate-post-switchover")
    last_success = _last_success(cycle_events)
    inventory_at = state.get("inventory_at")
    transition_event = max((event for event in (switch, failover) if event and event.get("status") == "succeeded"),
                           key=_event_order, default=None)
    transition_attempt = max((event for event in (switch, failover) if event),
                             key=_event_order, default=None)
    active_transition = next((command for command in ("switchover", "failover") if command in current), None)
    # Inventory freshness and pre-validation are admission gates until the
    # switchover actually begins.  Once it has begun, their captured evidence
    # belongs to that recovery cycle and must not be invalidated by elapsed
    # time while post-transition validation and reprotect are running.
    switchover_started = (bool(switch)
                           or (current.get("switchover") or {}).get("status") == "running"
                           or phase not in STABLE_PHASES)
    # Selecting a route is durable for the non-stable part of a recovery.  Once
    # protection is restored (or the controller returns to READY), either route
    # can be evaluated again for a new recovery cycle.
    selected_path = active_transition or (None if phase in STABLE_PHASES and not reset_pending else
                                          (transition_attempt or {}).get("command"))

    stable = phase in STABLE_PHASES
    stable_detail = ("FSDR state is {} and accepts this preparation step.".format(phase)
                     if stable else "FSDR state is {}; a stable protected state is required.".format(phase))
    validation_current = bool(validate and validate.get("status") == "succeeded"
                              and isinstance(inventory_at, (int, float))
                              and validate.get("inventory_at") == inventory_at)
    validation_detail = ("Validate switchover succeeded for the current inventory."
                         if validation_current else "Validate switchover must succeed for the current inventory.")
    transition_last = bool(transition_event and last_success
                           and last_success.get("job_id") == transition_event.get("job_id"))
    transition_name = transition_event.get("command") if transition_event else "a transition"
    transition_detail = ("The latest successful workflow step is {}.".format(transition_name)
                         if transition_last else "Switchover or failover must be the latest successful workflow step.")
    post_after_transition = bool(post and post.get("status") == "succeeded" and transition_event
                                 and (_event_order(post) >= _event_order(transition_event)))
    post_detail = ("Post-transition validation succeeded after {}.".format(transition_name)
                   if post_after_transition else "Validate post-switchover must succeed after switchover or failover.")
    saved_inventory = bool(state.get("indices"))
    saved_inventory_detail = ("A persisted inventory defines the protected index scope."
                              if saved_inventory else "Failover requires a previously persisted inventory.")
    source_fenced = candidate.get("source_fenced") is True
    source_fenced_detail = ("Source fencing was explicitly attested." if source_fenced
                            else "Failover requires source_fenced=true in the API request.")
    data_loss_accepted = candidate.get("accept_data_loss") is True
    data_loss_detail = ("Potential data loss was explicitly accepted." if data_loss_accepted
                        else "Failover requires accept_data_loss=true in the API request.")
    stale_annotation_ok = fresh or candidate.get("use_follower_annotations") is True
    stale_annotation_detail = ("Inventory is fresh; no stale-inventory follower attestation is needed."
                               if fresh else ("Follower annotations will be verified for stale inventory."
                                              if stale_annotation_ok else "Stale inventory requires use_follower_annotations=true."))
    service_restored = phase == "SERVICE_RESTORED"
    service_detail = ("FSDR state is SERVICE_RESTORED." if service_restored
                      else "FSDR state is {}; SERVICE_RESTORED is required.".format(phase))

    rules = {
        "inventory": [
            _requirement("stable-state", stable, stable_detail,
                "Preparation is allowed only from a stable FSDR state, preventing a second operation from overlapping an unfinished transition.",
                blocked=not stable),
        ],
        "validate-switchover": [
            _requirement("fresh-inventory", fresh, fresh_detail,
                "A fresh inventory fixes the current protected-index scope, cluster roles, UUIDs and CCR direction used by the next operation."),
            _requirement("stable-state", stable, stable_detail,
                "Preparation is allowed only from a stable FSDR state, preventing a second operation from overlapping an unfinished transition.",
                blocked=not stable),
        ],
        "switchover": [
            _requirement("fresh-inventory", fresh, fresh_detail,
                "A fresh inventory fixes the current protected-index scope, cluster roles, UUIDs and CCR direction used by the next operation."),
            _requirement("validate-switchover", validation_current, validation_detail,
                "The pre-validation checks health, roles, aliases, CCR and readiness against this exact inventory before writes are moved."),
            _requirement("stable-state", stable, stable_detail,
                "Preparation is allowed only from a stable FSDR state, preventing a second operation from overlapping an unfinished transition.",
                blocked=not stable),
        ],
        "failover": [
            _requirement("stable-state", stable, stable_detail,
                "Preparation is allowed only from a stable FSDR state, preventing a second operation from overlapping an unfinished transition.",
                blocked=not stable),
            _requirement("persisted-inventory", saved_inventory, saved_inventory_detail,
                "When the original primary is unavailable, FSDR needs the saved inventory to identify the protected indices and their follower mappings."),
            _requirement("source-fenced", source_fenced, source_fenced_detail,
                "This operator attestation confirms the former primary cannot accept writes, preventing split-brain during an emergency promotion."),
            _requirement("data-loss-accepted", data_loss_accepted, data_loss_detail,
                "Failover can promote only operations already replicated to the follower; this attestation explicitly accepts any unreplicated writes."),
            _requirement("stale-follower-annotations", stale_annotation_ok, stale_annotation_detail,
                "With stale inventory, FSDR verifies follower annotations before promotion so the saved scope is not used blindly."),
        ],
        "validate-post-switchover": [
            _requirement("service-restored", service_restored, service_detail,
                "Post-transition work starts only after the promoted cluster is writable and the reverse CCR remains intentionally stopped.",
                blocked=not service_restored),
            _requirement("transition-last", transition_last, transition_detail,
                "Validation must run immediately after the switchover or failover, before a later workflow action changes the recovery evidence."),
        ],
        "reprotect": [
            _requirement("service-restored", service_restored, service_detail,
                "Post-transition work starts only after the promoted cluster is writable and the reverse CCR remains intentionally stopped.",
                blocked=not service_restored),
            _requirement("post-validation", post_after_transition, post_detail,
                "Reverse protection recreates the former primary as a follower, so it is allowed only after post-transition checks completed successfully."),
        ],
    }
    steps = []
    for command, label in ALL_WORKFLOW_STEPS:
        requirements = list(rules[command])
        active = current.get(command)
        latest = _latest(cycle_events, command)
        frozen_requirements = (latest or {}).get("requirements")
        eligible = all(item["met"] for item in requirements) and not controller_busy
        if controller_busy and not active:
            requirements.insert(0, _requirement("controller-idle", False,
                "Another FSDR workflow step is queued or running.",
                "FSDR serializes disruptive actions so two jobs cannot modify roles, write blocks or CCR configuration at the same time."))
        elif active:
            requirements.insert(0, _requirement("controller-idle", True,
                "This workflow step is currently {}.".format(active.get("status")),
                "The controller owns this operation and will publish its terminal outcome before another workflow action is accepted."))
        else:
            requirements.insert(0, _requirement("controller-idle", True, "No workflow step is running.",
                "The controller is free to start the next eligible workflow action."))
        if active:
            status, detail = ("running" if active.get("status") == "running" else "queued",
                              "This step is currently {}.".format(active.get("status")))
        elif latest and latest.get("status") in {"failed", "interrupted"}:
            status = latest["status"]
            detail = latest.get("reason") or "The latest execution did not complete successfully."
        elif latest and latest.get("status") == "succeeded":
            if command == "inventory" and not switchover_started and not fresh:
                status, detail = "ready", "The latest inventory expired; run inventory again before switchover."
            elif command == "validate-switchover" and not switchover_started and not fresh:
                status, detail = "waiting", "The validated inventory expired; run inventory and validate again."
            else:
                status, detail = "succeeded", "The latest execution completed successfully."
        elif eligible:
            status, detail = "ready", "All prerequisites are met; this step can be submitted."
        elif any(item["level"] == "blocked" for item in requirements if not item["met"]):
            status, detail = "blocked", "A required controller state is incompatible with this step."
        else:
            status, detail = "waiting", "Waiting for prerequisites."
        attempt = active or latest or {}
        last_attempt_at = (attempt.get("finished_at") or attempt.get("started_at")
                           or attempt.get("submitted_at"))
        displayed_requirements = requirements
        active_requirements = (active or {}).get("workflow_evidence", {}).get("requirements")
        if isinstance(active_requirements, list):
            # A running step may intentionally change the FSDR phase (for
            # example SERVICE_RESTORED to REPROTECTING). Keep showing the
            # gates that admitted this in-flight execution.
            displayed_requirements = list(active_requirements)
        elif active:
            # Compatibility for a job that started before evidence snapshots
            # were introduced. An active job already passed API admission.
            displayed_requirements = [{**item, "met": True,
                                     "detail": "Satisfied when this active execution began.",
                                     "level": "waiting"}
                                    for item in requirements]
        elif latest and latest.get("status") == "succeeded" and (
                switchover_started or command not in {"inventory", "validate-switchover"}):
            if isinstance(frozen_requirements, list):
                displayed_requirements = list(frozen_requirements)
            else:
                # Jobs recorded before gate snapshots existed still represent
                # successful admission. Preserve that fact for the dashboard
                # rather than evaluating their old gates against a newer DR
                # phase (for example SERVICE_RESTORED after switchover).
                displayed_requirements = [{**item, "met": True,
                                         "detail": "Satisfied when this successful execution began.",
                                         "level": "waiting"}
                                        for item in requirements]
        steps.append({"command": command, "label": label, "status": status, "status_detail": detail,
                      "eligible": eligible,
                      "requirements": displayed_requirements,
                      "last_event": latest, "last_attempt_at": last_attempt_at})
    by_command = {step["command"]: step for step in steps}
    paths = [{**path, "steps": [by_command[command] for command in path["steps"]],
              "active": path["id"] == selected_path,
              "unavailable": bool(selected_path and path["id"] in {"switchover", "failover"}
                                  and path["id"] != selected_path)}
             for path in RECOVERY_PATHS]
    return {"phase": phase, "inventory_max_age_seconds": INVENTORY_MAX_AGE_SECONDS,
            "selected_path": selected_path,
            "paths_resettable": phase in STABLE_PHASES and not reset_pending,
            "cycle_reset_at": completed_cycle.get("finished_at") if completed_cycle else None,
            "cycle_reset_pending_until": cycle_reset_pending_until if reset_pending else None,
            "steps": steps, "detailed_steps": [by_command[command] for command, _ in WORKFLOW_STEPS],
            "paths": paths, "events": events[-50:]}


def step_for(snapshot: dict[str, Any], command: str) -> dict[str, Any] | None:
    return next((step for step in snapshot.get("steps", []) if step.get("command") == command), None)
