"""Authenticated, serialized HTTP API for the FSDR command-line controller.

The API deliberately invokes the existing CLI in a child process.  It does
not duplicate DR orchestration logic, so CLI and API executions preserve the
same state lock, audit records, resume behaviour, and exit codes.
"""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .client import clients
from .cloud import Cloud, read_cache, runtime_config
from .core import Error
from .workflow import WORKFLOW_COMMANDS, record_event, step_for, workflow_snapshot
from .web import DASHBOARD_HTML


PROJECT = Path(__file__).resolve().parent.parent
STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
CONFIG = Path(os.environ.get("FSDR_CONFIG", "/etc/oci-opensearch-fsdr/config.json"))
JOB_DIRECTORY = Path(os.environ.get("FSDR_API_JOB_DIR", "/var/lib/oci-opensearch-fsdr/api-jobs"))
SECRET_DIRECTORY = Path(os.environ.get("FSDR_API_SECRET_DIR", str(JOB_DIRECTORY.parent / "api-secrets")))
TOKEN_FILE = Path(os.environ.get("FSDR_API_TOKEN_FILE", str(SECRET_DIRECTORY / "api-token")))
CERTIFICATE_FILE = Path(os.environ.get("FSDR_API_CERT_FILE", str(SECRET_DIRECTORY / "tls.crt")))
PRIVATE_KEY_FILE = Path(os.environ.get("FSDR_API_KEY_FILE", str(SECRET_DIRECTORY / "tls.key")))
MONITOR_CACHE_LOCK = threading.RLock()

COMMANDS = {
    "discover", "precheck", "inventory", "readiness", "validate-switchover",
    "validate-post-switchover", "status", "cleanup", "switchover", "failover", "reprotect",
}
MUTATING = {"switchover", "failover", "reprotect"}


class OperationRequest(BaseModel):
    command: str
    secret_id: Optional[str] = None
    state_dir: Optional[str] = None
    log_dir: Optional[str] = None
    ca_file: Optional[str] = None
    use_private_ip: Optional[bool] = None
    request_timeout: Optional[float] = Field(default=None, gt=0)
    wait_timeout: Optional[float] = Field(default=None, gt=0)
    poll_interval: Optional[float] = Field(default=None, gt=0)
    max_inventory_age_hours: Optional[float] = Field(default=None, gt=0)
    retention_days: Optional[int] = Field(default=None, ge=1)
    leader_role: Optional[str] = None
    follower_role: Optional[str] = None
    resume: bool = False
    source_fenced: bool = False
    accept_data_loss: bool = False
    allow_stale_inventory: bool = False
    use_follower_annotations: bool = False
    reprotect_parallelism: Optional[int] = Field(default=None, ge=1)
    scheduled: bool = False

    class Config:
        # Reject obsolete fields such as ``execute`` or ``dry_run`` rather than silently
        # accepting a request whose DR semantics changed.
        extra = "forbid"

    @model_validator(mode="after")
    def validate_request(self):
        if self.command not in COMMANDS:
            raise ValueError("unsupported command")
        if self.resume and self.command not in {"switchover", "failover"}:
            raise ValueError("resume applies only to switchover or failover")
        if self.command != "failover" and any((self.source_fenced, self.accept_data_loss,
                                                  self.allow_stale_inventory, self.use_follower_annotations)):
            raise ValueError("failover attestation fields apply only to failover")
        if self.reprotect_parallelism is not None and self.command != "reprotect":
            raise ValueError("reprotect_parallelism applies only to reprotect")
        if self.scheduled and self.command != "inventory":
            raise ValueError("scheduled applies only to inventory")
        return self


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def dump_model(request: OperationRequest):
    # Supports both Pydantic 1 and 2 used by supported FastAPI releases.
    return request.model_dump() if hasattr(request, "model_dump") else request.dict()


def command_arguments(request: OperationRequest):
    data = dump_model(request)
    # The CLI is an internal package entry point.  There is no host-side
    # fsdr.py wrapper in the container-first distribution.
    args = [sys.executable, "-m", "fsdr_opensearch.cli", request.command, "--config", str(CONFIG)]
    if request.secret_id:
        args.insert(4, request.secret_id)
    options = {
        "state_dir": "--state-dir", "log_dir": "--log-dir", "ca_file": "--ca-file",
        "request_timeout": "--request-timeout", "wait_timeout": "--wait-timeout",
        "poll_interval": "--poll-interval", "max_inventory_age_hours": "--max-inventory-age-hours",
        "retention_days": "--retention-days", "leader_role": "--leader-role",
        "follower_role": "--follower-role", "reprotect_parallelism": "--reprotect-parallelism",
    }
    for field, flag in options.items():
        if data.get(field) is not None:
            args.extend((flag, str(data[field])))
    for field, flag in (("use_private_ip", "--use-private-ip"), ("resume", "--resume"),
                        ("source_fenced", "--source-fenced"), ("accept_data_loss", "--accept-data-loss"),
                        ("allow_stale_inventory", "--allow-stale-inventory"),
                        ("use_follower_annotations", "--use-follower-annotations"),
                        ("scheduled", "--scheduled")):
        if data.get(field):
            args.append(flag)
    return args


class JobManager:
    def __init__(self):
        self.jobs = queue.Queue()
        self._job_change_condition = threading.Condition()
        self._job_revision = 0
        JOB_DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._mark_interrupted()
        self.worker = threading.Thread(target=self._work, daemon=True, name="fsdr-api-worker")
        self.worker.start()

    def path(self, job_id):
        return JOB_DIRECTORY / (job_id + ".json")

    def output_path(self, job_id):
        return JOB_DIRECTORY / (job_id + ".output")

    def read(self, job_id):
        try:
            return json.loads(self.path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(job_id) from None

    def write(self, job):
        target = self.path(job["id"])
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        with self._job_change_condition:
            self._job_revision += 1
            self._job_change_condition.notify_all()

    def job_revision(self):
        with self._job_change_condition:
            return self._job_revision

    def wait_for_job_change(self, revision, timeout=15):
        """Wait for an in-process job lifecycle update without polling disk."""
        with self._job_change_condition:
            if self._job_revision == revision:
                self._job_change_condition.wait(timeout)
            return self._job_revision

    def submit(self, request):
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "submitted_at": utc_now(), "started_at": None, "finished_at": None,
               "status": "queued", "exit_code": None, "request": dump_model(request),
               "output": self.output_path(job_id).name}
        self.write(job)
        self.jobs.put(job_id)
        return job

    def reject(self, request, rejection):
        """Persist a refused submission without starting a controller process.

        A workflow gate is an auditable decision: callers need the HTTP 409,
        while operators need to see the same decision and its unmet gates in
        the dashboard history.  It deliberately does not record a workflow
        outcome because no FSDR operation was executed.
        """
        job_id = uuid.uuid4().hex
        finished_at = utc_now()
        job = {
            "id": job_id,
            "submitted_at": finished_at,
            "started_at": None,
            "finished_at": finished_at,
            "status": "rejected",
            "exit_code": None,
            "reason": rejection.get("code", "request_rejected"),
            "rejection": rejection,
            "request": dump_model(request),
            "output": self.output_path(job_id).name,
        }
        lines = [
            "FSDR job rejected before execution.",
            "Command: {}".format(request.command),
            "Reason: {}".format(job["reason"]),
            "",
            "The controller did not start because required workflow gates are not satisfied.",
        ]
        inventory = rejection.get("inventory") or {}
        if inventory:
            age = inventory.get("age_seconds")
            maximum = inventory.get("max_age_seconds")
            if isinstance(age, (int, float)) and isinstance(maximum, (int, float)):
                lines.extend(("", "Inventory age: {} min ({} min limit; {}).".format(
                    int(age // 60), int(maximum // 60),
                    "valid" if inventory.get("fresh") else "expired")))
            elif inventory.get("detail"):
                lines.extend(("", "Inventory: {}".format(inventory["detail"])))
        requirements = rejection.get("requirements") or []
        unmet = [item for item in requirements if not item.get("met")]
        if unmet:
            lines.extend(("", "Unmet requirements:"))
            for item in unmet:
                detail = item.get("detail") or item.get("key") or "Requirement not satisfied"
                explanation = item.get("explanation")
                lines.append("- {}".format(detail))
                if explanation:
                    lines.append("  {}".format(explanation))
        self.output_path(job_id).write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(self.output_path(job_id), 0o600)
        self.write(job)
        return job

    def idle(self):
        """Avoid accumulating periodic inventory jobs behind a DR operation."""
        return self.jobs.empty() and not any(
            item.get("status") == "running" for item in self._recent_jobs())

    def _recent_jobs(self):
        for path in JOB_DIRECTORY.glob("*.json"):
            try:
                yield json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    def recent(self, limit=50):
        """Return public job metadata only, newest first."""
        rows = list(self._recent_jobs())
        rows.sort(key=lambda item: item.get("submitted_at", ""), reverse=True)
        return rows[:limit]

    def _mark_interrupted(self):
        for path in JOB_DIRECTORY.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") in {"queued", "running"}:
                job.update(status="interrupted", finished_at=utc_now(), exit_code=None,
                           reason="api_restarted; FSDR state was preserved for explicit resume")
                self.write(job)

    def _record_workflow_outcome(self, job):
        """Persist terminal workflow evidence separately from the controller state."""
        try:
            request = OperationRequest(**job["request"])
            configured = _read_json(CONFIG, {})
            state_dir = Path(request.state_dir or configured.get(
                "state_dir", "/var/lib/oci-opensearch-fsdr"))
            record_event(state_dir, job, _read_json(state_dir / "state.json", {}))
        except Exception:
            # The operation outcome remains authoritative even if its secondary
            # dashboard/audit projection cannot be written.
            pass

    def _capture_workflow_evidence(self, job):
        """Freeze the gates that admitted a workflow step before it runs.

        A successful switchover intentionally changes the durable FSDR phase.
        That later state must not repaint its already-satisfied inventory and
        validation gates as failures in the recovery-path history.
        """
        try:
            request = OperationRequest(**job["request"])
            if request.command not in WORKFLOW_COMMANDS:
                return None
            configured = _read_json(CONFIG, {})
            state_dir = Path(request.state_dir or configured.get(
                "state_dir", "/var/lib/oci-opensearch-fsdr"))
            snapshot = workflow_snapshot(state_dir, _read_json(state_dir / "state.json", {}),
                                         self.recent(), candidate=dump_model(request))
            step = step_for(snapshot, request.command)
            if not step:
                return None
            return {"captured_at": utc_now(), "requirements": step["requirements"]}
        except Exception:
            # The command remains authoritative even when a dashboard-only
            # evidence snapshot cannot be built.
            return None

    def _work(self):
        while True:
            job_id = self.jobs.get()
            try:
                job = self.read(job_id)
                job.update(status="running", started_at=utc_now())
                evidence = self._capture_workflow_evidence(job)
                if evidence:
                    job["workflow_evidence"] = evidence
                self.write(job)
                request = OperationRequest(**job["request"])
                output = self.output_path(job_id)
                with output.open("wb") as stream:
                    result = subprocess.run(command_arguments(request), cwd=str(PROJECT), stdout=stream,
                                            stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED="1"),
                                            check=False)
                os.chmod(output, 0o600)
                job.update(status="succeeded" if result.returncode == 0 else "failed",
                           exit_code=result.returncode, finished_at=utc_now())
            except Exception as exc:  # The exception is intentionally not returned to API clients.
                job.update(status="failed", exit_code=None, finished_at=utc_now(), reason=type(exc).__name__)
            self._record_workflow_outcome(job)
            self.write(job)
            self.jobs.task_done()


app = FastAPI(title="OCI OpenSearch FSDR API", version="1.0")


def api_manager():
    manager = getattr(app.state, "manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="API worker is starting")
    return manager


def read_token():
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(status_code=503, detail="API token is unavailable") from None
    if not value:
        raise HTTPException(status_code=503, detail="API token is invalid")
    return value


def token_auth(authorization: Optional[str] = Header(default=None)):
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied, read_token()):
        raise HTTPException(status_code=401, detail="authentication required",
                            headers={"WWW-Authenticate": "Bearer"})


def inventory_schedule(manager):
    """Run only the safe scheduled-inventory mode configured by the operator."""
    while True:
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            interval = float(data.get("inventory_interval_hours", 2)) * 3600
        except (OSError, ValueError, TypeError):
            interval = 3600
        # Re-read configuration after each interval so changes do not require a restart.
        time.sleep(max(interval, 60))
        try:
            enabled = json.loads(CONFIG.read_text(encoding="utf-8")).get("inventory_enabled") is True
        except (OSError, ValueError):
            enabled = False
        if enabled and manager.idle():
            manager.submit(OperationRequest(command="inventory", scheduled=True))


@app.on_event("startup")
def start_inventory_schedule():
    app.state.manager = JobManager()
    _queue_initial_inventory(app.state.manager)
    threading.Thread(target=inventory_schedule, args=(app.state.manager,), daemon=True,
                     name="fsdr-inventory-scheduler").start()
    # Prime the read-only dashboard data after a container restart.  It never
    # changes OpenSearch or FSDR state, and avoids a blank first page for an
    # already configured controller.
    threading.Thread(target=monitor_snapshot, daemon=True,
                     name="fsdr-dashboard-observer").start()


def _queue_initial_inventory(manager):
    """Seed discovery and CCR inventory on a new configured deployment.

    ``inventory`` already performs OCI discovery before inspecting CCR, so one
    serialized read-only job is enough.  It is queued only when neither the
    discovery cache nor durable state exists; container restarts never repeat
    an already attempted initialization.
    """
    configured = _read_json(CONFIG, {})
    secret_id = configured.get("secret_id")
    state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    if not secret_id:
        return
    if (state_dir / "state.json").is_file():
        return
    manager.submit(OperationRequest(command="inventory"))


@app.get("/healthz")
def health():
    return {"status": "ok", "config_present": CONFIG.is_file(), "time": utc_now()}


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    """A local web console. Data remains protected by the API Bearer token."""
    return DASHBOARD_HTML


@app.get("/assets/fsdr-logo.png", include_in_schema=False)
def dashboard_logo():
    """Brand asset used by the unauthenticated local dashboard."""
    return FileResponse(STATIC_DIRECTORY / "fsdr-logo.png", media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Small embedded icon so normal browser navigation does not produce 404s."""
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
           "<rect width='64' height='64' rx='14' fill='#45d5e8'/>"
           "<path d='M18 22h28v20H18z' fill='#0b1120'/><path d='M25 15v34M39 15v34' "
           "stroke='#0b1120' stroke-width='5'/></svg>")
    return Response(content=svg, media_type="image/svg+xml")


def workflow_gate(request, manager):
    """Reject out-of-order API workflow submissions before a child starts."""
    if request.command not in WORKFLOW_COMMANDS or request.resume:
        # Resume follows the controller's durable transition record rather than
        # a new planned-workflow path. The CLI validates compatibility.
        return
    configured = _read_json(CONFIG, {})
    state_dir = Path(request.state_dir or configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    snapshot = workflow_snapshot(state_dir, _read_json(state_dir / "state.json", {}), manager.recent(),
                                 candidate=dump_model(request))
    step = step_for(snapshot, request.command)
    if not step or not step["eligible"]:
        raise HTTPException(status_code=409, detail={
            "code": "workflow_prerequisites_unmet", "command": request.command,
            "status": step.get("status") if step else "blocked",
            "inventory": snapshot.get("inventory", {}),
            "requirements": step.get("requirements", []) if step else [],
        })


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(token_auth)])
def submit(request: OperationRequest):
    manager = api_manager()
    try:
        workflow_gate(request, manager)
    except HTTPException as exc:
        if exc.status_code != 409 or not isinstance(exc.detail, dict):
            raise
        rejected = manager.reject(request, exc.detail)
        detail = dict(exc.detail)
        detail.update(job_id=rejected["id"], rejected_at=rejected["finished_at"])
        raise HTTPException(status_code=409, detail=detail) from None
    job = manager.submit(request)
    return {"id": job["id"], "status": job["status"], "submitted_at": job["submitted_at"]}


@app.get("/v1/jobs", dependencies=[Depends(token_auth)])
def jobs():
    return {"jobs": api_manager().recent()}


def current_workflow(manager=None, jobs=None):
    configured = _read_json(CONFIG, {})
    state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    manager = manager or api_manager()
    # Reuse the job collection carried by an SSE payload. History and Recovery
    # paths must be derived from the same controller revision.
    return workflow_snapshot(state_dir, _read_json(state_dir / "state.json", {}),
                             manager.recent() if jobs is None else jobs)


@app.get("/v1/workflow", dependencies=[Depends(token_auth)])
def workflow():
    """Return durable workflow gates and audit evidence for automation."""
    return current_workflow()


@app.get("/dashboard/v1/workflow")
def dashboard_workflow():
    return current_workflow()


@app.get("/dashboard/v1/jobs")
def dashboard_jobs():
    """Read-only local dashboard feed; command submission remains authenticated."""
    return {"jobs": api_manager().recent()}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(token_auth)])
def job(job_id: str):
    try:
        return api_manager().read(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None


@app.get("/v1/jobs/{job_id}/output", dependencies=[Depends(token_auth)])
def output(job_id: str):
    try:
        manager = api_manager()
        manager.read(job_id)
        data = manager.output_path(job_id).read_bytes()
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None
    except FileNotFoundError:
        data = b""
    return Response(content=data, media_type="text/plain; charset=utf-8")


@app.get("/dashboard/v1/jobs/{job_id}/output")
def dashboard_output(job_id: str):
    try:
        manager = api_manager()
        manager.read(job_id)
        data = manager.output_path(job_id).read_bytes()
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None
    except FileNotFoundError:
        data = b""
    return Response(content=data, media_type="text/plain; charset=utf-8")


async def dashboard_output_stream(manager, job_id, offset):
    """Tail one job's output as SSE without retaining output in memory."""
    terminal = {"succeeded", "failed", "interrupted", "rejected"}
    last_keepalive = time.monotonic()
    while True:
        try:
            job = manager.read(job_id)
        except KeyError:
            yield "event: error\ndata: job not found\n\n"
            return
        output_path = manager.output_path(job_id)
        try:
            size = output_path.stat().st_size
            if size < offset:
                offset = 0
            with output_path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(64 * 1024)
        except FileNotFoundError:
            data = b""
        if data:
            offset += len(data)
            payload = data.decode("utf-8", errors="replace")
            yield "id: {}\nevent: output\ndata: {}\n\n".format(offset, json.dumps(payload))
            continue
        if job.get("status") in terminal:
            yield "id: {}\nevent: end\ndata: {}\n\n".format(offset, json.dumps(job["status"]))
            return
        if time.monotonic() - last_keepalive >= 15:
            yield ": keepalive\n\n"
            last_keepalive = time.monotonic()
        await asyncio.sleep(0.4)


def dashboard_snapshot_payload(manager=None):
    """Return one internally consistent dashboard projection for an SSE event."""
    manager = manager or api_manager()
    jobs = manager.recent()
    # Remote monitor data is cached briefly, but workflow/job lifecycle data is
    # local and changes immediately. Refresh only that projection so a delayed
    # monitor snapshot cannot overwrite a newer jobs-stream workflow update.
    monitor = dict(monitor_snapshot())
    monitor["workflow"] = current_workflow(manager, jobs)
    return {"monitor": monitor, "jobs": {"jobs": jobs}}


async def dashboard_events_stream(manager, refresh_seconds=5.0):
    """Push complete dashboard snapshots without browser polling.

    FSDR state changes are detected from durable local records. A bounded
    periodic observation refreshes remote OpenSearch counters and CCR status;
    job lifecycle updates use their dedicated local SSE feed so they never
    wait for an OpenSearch observation.
    """
    revision = 0
    signature = object()
    next_observation = 0.0
    last_keepalive = time.monotonic()
    while True:
        configured = _read_json(CONFIG, {})
        state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
        current = (str(CONFIG), _file_version(CONFIG), _file_version(state_dir / "state.json"),
                   _file_version(state_dir / "workflow.json"))
        now = time.monotonic()
        if current != signature or now >= next_observation:
            payload = await asyncio.to_thread(dashboard_snapshot_payload, manager)
            signature = current
            revision += 1
            next_observation = now + max(1.0, refresh_seconds)
            yield "id: {}\nevent: snapshot\ndata: {}\n\n".format(revision, json.dumps(payload))
            last_keepalive = time.monotonic()
        elif time.monotonic() - last_keepalive >= 15:
            yield ": keepalive\n\n"
            last_keepalive = time.monotonic()
        await asyncio.sleep(0.25)


async def dashboard_jobs_stream(manager, revision):
    """Publish job lifecycle updates to dashboard clients through SSE."""
    sent_initial = False
    workflow_refresh_at = None
    while True:
        current = manager.job_revision()
        now = time.time()
        if not sent_initial or current != revision or (workflow_refresh_at is not None and now >= workflow_refresh_at):
            revision = current
            sent_initial = True
            # Do not call monitor_snapshot here. This feed must be able to
            # render a newly submitted or rejected job even if either remote
            # OpenSearch endpoint is slow or temporarily unavailable.
            jobs = manager.recent()
            workflow = current_workflow(manager, jobs)
            pending_reset = workflow.get("cycle_reset_pending_until")
            workflow_refresh_at = (float(pending_reset) if isinstance(pending_reset, (int, float))
                                   and pending_reset > now else None)
            yield "id: {}\nevent: jobs\ndata: {}\n\n".format(
                revision, json.dumps({"revision": revision, "jobs": jobs,
                                      "workflow": workflow}))
        # ``wait_for_job_change`` returns the revision that woke the waiter.
        # Do not assign it here: the next iteration must compare that new
        # value with the last revision actually sent to the browser.  Assigning
        # it first silently swallowed the event until another dashboard
        # refresh, leaving History ahead of Recovery paths for several seconds.
        timeout = 15
        if workflow_refresh_at is not None:
            timeout = max(0.05, min(timeout, workflow_refresh_at - time.time()))
        await asyncio.to_thread(manager.wait_for_job_change, revision, timeout)


async def dashboard_state_stream(state_file):
    """Notify dashboard clients when the child controller persists state."""
    last_version = object()
    last_keepalive = time.monotonic()
    while True:
        version = _file_version(state_file)
        if version != last_version:
            last_version = version
            yield "event: state\ndata: {}\n\n".format(json.dumps({"version": version}))
        if time.monotonic() - last_keepalive >= 15:
            yield ": keepalive\n\n"
            last_keepalive = time.monotonic()
        await asyncio.sleep(0.4)


@app.get("/dashboard/v1/events")
async def dashboard_events():
    """Unified public dashboard feed: monitor, workflow and job history."""
    return StreamingResponse(dashboard_events_stream(api_manager()),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/dashboard/v1/jobs/{job_id}/events")
async def dashboard_output_events(job_id: str, request: Request):
    """Stream a selected job log to the read-only local dashboard via SSE."""
    manager = api_manager()
    try:
        manager.read(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None
    try:
        offset = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        offset = 0
    return StreamingResponse(dashboard_output_stream(manager, job_id, offset),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/dashboard/v1/jobs/events")
async def dashboard_jobs_events(request: Request):
    """Notify the public local dashboard when a job changes lifecycle state."""
    try:
        revision = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        revision = 0
    return StreamingResponse(dashboard_jobs_stream(api_manager(), revision),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/dashboard/v1/state/events")
async def dashboard_state_events():
    """Push durable FSDR state changes to the public local dashboard."""
    configured = _read_json(CONFIG, {})
    state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    return StreamingResponse(dashboard_state_stream(state_dir / "state.json"),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/v1/commands", dependencies=[Depends(token_auth)])
def commands():
    return {"commands": sorted(COMMANDS), "mutating_commands": sorted(MUTATING),
            "execution": "serialized", "default_dr_mode": "execute"}


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _observer_clients(configured, state_dir, secret_id):
    """Return short-lived in-memory observer clients without Vault polling.

    No credential is written to disk. The cache is deliberately brief so a
    rotated Secret is picked up without a container restart.
    """
    lifetime = max(1, int(os.environ.get("FSDR_MONITOR_CACHE_SECONDS", "30")))
    cached = getattr(app.state, "observer", None)
    now = time.monotonic()
    if cached and cached["secret_id"] == secret_id and now - cached["at"] < lifetime:
        return cached["cfg"], cached["clients"]
    discovery = read_cache(state_dir, secret_id)
    if not discovery:
        raise Error("Discovery is unavailable")
    cloud = Cloud(timeout=float(configured.get("request_timeout", 20)))
    credentials_by_id = cloud.credentials(secret_id)
    try:
        credentials = {
            name: credentials_by_id[cluster["id"]]
            for name, cluster in discovery["clusters"].items()
        }
    except (KeyError, TypeError):
        raise Error("Secret credentials do not match the discovered cluster pair") from None
    cfg = runtime_config(discovery, state_dir, configured.get("log_dir", "/var/log/oci-opensearch-fsdr"),
                         configured.get("ca_file"), configured.get("leader_role"),
                         configured.get("follower_role"))
    cfg.update(request_timeout=float(configured.get("request_timeout", 20)),
               wait_timeout=float(configured.get("wait_timeout", 1800)),
               poll_interval=float(configured.get("poll_interval", 2)),
               use_private_ip=configured.get("use_private_ip", True))
    observed = clients(cfg, credentials)
    app.state.observer = {"at": now, "secret_id": secret_id, "cfg": cfg, "clients": observed}
    return cfg, observed


def _file_version(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def monitor_snapshot():
    """Return a short-lived shared read-only snapshot for dashboard clients."""
    configured = _read_json(CONFIG, {})
    state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    state_file = state_dir / "state.json"
    manager = getattr(app.state, "manager", None)
    signature = (str(CONFIG), _file_version(CONFIG), _file_version(state_file),
                 _file_version(state_dir / "workflow.json"))
    try:
        lifetime = max(1, float(os.environ.get("FSDR_MONITOR_SNAPSHOT_CACHE_SECONDS", "10")))
    except ValueError:
        lifetime = 10
    with MONITOR_CACHE_LOCK:
        cached = getattr(app.state, "monitor_snapshot", None)
        if cached and cached["signature"] == signature and time.monotonic() - cached["at"] < lifetime:
            return cached["data"]
        data = _monitor_snapshot(configured, state_dir, _read_json(state_file, {}))
        app.state.monitor_snapshot = {"signature": signature, "at": time.monotonic(), "data": data}
        return data


def _index_write_mode(settings):
    """Translate OpenSearch index block settings into a dashboard-safe mode."""
    if not isinstance(settings, dict):
        return "unknown"
    blocked = any(str(settings.get("index.blocks." + block, "false")).lower() == "true"
                  for block in ("write", "read_only", "read_only_allow_delete", "metadata"))
    return "read_only" if blocked else "read_write"


def _monitor_snapshot(configured, state_dir, state):
    """Read OpenSearch safely for the web observer without changing FSDR state.

    It deliberately uses the saved discovery record and Vault credentials only
    in memory.  Unlike an inventory job it never takes the controller lock or
    writes a manifest, so it can show counts and CCR status while a transition
    owns the controller.
    """
    secret_id = configured.get("secret_id")
    base = {"observed_at": utc_now(), "phase": state.get("phase"),
            "primary": state.get("active"), "inventory_at": state.get("inventory_at"),
            "state_updated_at": state.get("updated_at"),
            "current_step": (state.get("transition") or {}).get("step"),
            "current_index": (state.get("transition") or {}).get("current_index"),
            "indices": state.get("indices", []), "clusters": {}, "errors": [],
            "topology": "unavailable", "topology_directions": [],
            "workflow": workflow_snapshot(state_dir, state,
                getattr(app.state, "manager", None).recent() if getattr(app.state, "manager", None) else [])}
    if not secret_id:
        base["errors"].append("Discovery is unavailable. Run discover or inventory first.")
        return base
    try:
        cfg, cs = _observer_clients(configured, state_dir, secret_id)
    except Error as exc:
        # These controller errors are already sanitized and identify the
        # failing dependency (discovery, Instance Principal, Vault or OCI).
        # Showing them in the local read-only dashboard avoids an opaque
        # "Error" while never returning the Secret payload or credentials.
        base["errors"].append("Cannot prepare OpenSearch observer: {}".format(exc))
        return base
    except Exception as exc:
        # An invalid or interrupted cache must not make the dashboard opaque.
        # Keep the message free of request data and credentials, but identify
        # the local recovery action for the operator.
        base["errors"].append(
            "Cannot prepare OpenSearch observer: unexpected {}. Run inventory again; "
            "see the controller logs if it persists.".format(type(exc).__name__))
        return base
    observed_directions = set()
    for name in ("a", "b"):
        cluster = cfg["clusters"][name]
        view = {"name": cluster["display_name"], "id": cluster["id"],
                "role": "primary" if state.get("active") == name else "follower",
                "indices": [], "documents": 0, "error": None}
        try:
            mappings = cs[name].request("GET", "/_mapping")
        except Exception as exc:
            mappings = {}
            view["error"] = "Could not read one or more index mappings ({})".format(
                type(exc).__name__)
        try:
            counts = cs[name].document_counts(scoped=False)
        except Exception as exc:
            counts = {}
            view["error"] = "Could not read one or more document counts ({})".format(
                type(exc).__name__)
        try:
            index_settings = cs[name].request("GET", "/_settings?flat_settings=true")
        except Exception:
            # The observer must stay read-only and useful when a permission or
            # transient OpenSearch error prevents reading block settings.
            index_settings = {}
        for entry in state.get("indices", []):
            index = entry.get(name)
            if not index:
                continue
            meta = mappings.get(index, {}).get("mappings", {}).get("_meta", {})
            marker = meta.get("fsdr", {}) if isinstance(meta, dict) else {}
            write_mode = _index_write_mode(index_settings.get(index, {}).get("settings"))
            item = {"index": index, "documents": None, "replication": None, "error": None,
                    "fsdr_replicate": isinstance(marker, dict) and marker.get("replicate") is True,
                    "write_mode": write_mode,
                    "write_detail": ("OpenSearch has an index write block enabled."
                                     if write_mode == "read_only" else
                                     "OpenSearch permits writes to this index."
                                     if write_mode == "read_write" else
                                     "The dashboard could not determine this index's write-block setting.")}
            try:
                if index not in counts:
                    raise Error("Index was not returned by the document count request")
                item["documents"] = counts[index]
                view["documents"] += item["documents"]
                status = cs[name].status(index)
                if status.get("status") == "SYNCING":
                    observed_directions.add(("b" if name == "a" else "a", name))
                if state.get("active") != name:
                    details = status.get("syncing_details", {})
                    item["replication"] = {"status": status.get("status"),
                                           "leader_checkpoint": details.get("leader_checkpoint"),
                                           "follower_checkpoint": details.get("follower_checkpoint")}
                    if status.get("status") == "SYNCING":
                        # CCR followers are effectively read-only even when
                        # index.blocks.write is not explicitly present.
                        item["write_mode"] = "read_only"
                        item["write_detail"] = ("This index is an active CCR follower; "
                                                "OpenSearch rejects application writes while replication runs.")
                else:
                    item["replication"] = {"status": "PRIMARY"}
            except Exception as exc:
                item["error"] = type(exc).__name__
            view["indices"].append(item)
        if any(item["error"] for item in view["indices"]):
            view["error"] = "One or more index observations failed."
        base["clusters"][name] = view
    base["topology_directions"] = ["{}->{}".format(source, target)
                                   for source, target in sorted(observed_directions)]
    if state.get("active") in {"a", "b"}:
        directions = {("a", "b"), ("b", "a")}
        base["topology"] = ("bidirectional" if directions.issubset(observed_directions)
                            else "unidirectional")
    return base


@app.get("/v1/monitor", dependencies=[Depends(token_auth)])
def monitor():
    return monitor_snapshot()


@app.get("/dashboard/v1/monitor")
def dashboard_monitor():
    return monitor_snapshot()


def secure_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def bootstrap_api():
    """Create a persistent local API token and localhost TLS certificate."""
    if not TOKEN_FILE.exists():
        import secrets
        secure_write(TOKEN_FILE, secrets.token_urlsafe(48) + "\n")
    if not CERTIFICATE_FILE.exists() or not PRIVATE_KEY_FILE.exists():
        command = ["openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes", "-days", "365",
                   "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                   "-keyout", str(PRIVATE_KEY_FILE), "-out", str(CERTIFICATE_FILE)]
        CERTIFICATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        # The certificate is public material and must be readable by local curl clients.
        os.chmod(CERTIFICATE_FILE, 0o644)
        os.chmod(PRIVATE_KEY_FILE, 0o600)
    fingerprint = hashlib.sha256(CERTIFICATE_FILE.read_bytes()).hexdigest()
    print("API bootstrap completed. Retrieve the token locally with: python -m fsdr_opensearch.api token")
    print("Certificate SHA-256: {}".format(fingerprint))


def show_token():
    if not sys.stdout.isatty():
        raise RuntimeError("the token is displayed only on an interactive local terminal")
    print("Bearer token: {}".format(read_token()))
    print("CA certificate: {}".format(CERTIFICATE_FILE))


def main():
    import uvicorn
    if len(sys.argv) == 2 and sys.argv[1] == "bootstrap":
        bootstrap_api()
        return
    if len(sys.argv) == 2 and sys.argv[1] == "token":
        show_token()
        return
    if not TOKEN_FILE.is_file() or not CERTIFICATE_FILE.is_file() or not PRIVATE_KEY_FILE.is_file():
        raise RuntimeError("API is not bootstrapped; run: python -m fsdr_opensearch.api bootstrap")
    uvicorn.run(app, host=os.environ.get("FSDR_API_HOST", "127.0.0.1"),
                port=int(os.environ.get("FSDR_API_PORT", "1604")), log_level="info",
                ssl_certfile=str(CERTIFICATE_FILE), ssl_keyfile=str(PRIVATE_KEY_FILE))


if __name__ == "__main__":
    main()
