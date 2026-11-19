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

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from .client import clients
from .cloud import Cloud, read_cache, runtime_config
from .core import Error
from .web import DASHBOARD_HTML


PROJECT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("FSDR_CONFIG", "/etc/oci-opensearch-fsdr/config.json"))
JOB_DIRECTORY = Path(os.environ.get("FSDR_API_JOB_DIR", "/var/lib/oci-opensearch-fsdr/api-jobs"))
SECRET_DIRECTORY = Path(os.environ.get("FSDR_API_SECRET_DIR", str(JOB_DIRECTORY.parent / "api-secrets")))
TOKEN_FILE = Path(os.environ.get("FSDR_API_TOKEN_FILE", str(SECRET_DIRECTORY / "api-token")))
CERTIFICATE_FILE = Path(os.environ.get("FSDR_API_CERT_FILE", str(SECRET_DIRECTORY / "tls.crt")))
PRIVATE_KEY_FILE = Path(os.environ.get("FSDR_API_KEY_FILE", str(SECRET_DIRECTORY / "tls.key")))

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
    execute: bool = False
    dry_run: bool = False
    resume: bool = False
    source_fenced: bool = False
    accept_data_loss: bool = False
    allow_stale_inventory: bool = False
    use_follower_annotations: bool = False
    allow_recreate: bool = False
    reprotect_parallelism: Optional[int] = Field(default=None, ge=1)
    scheduled: bool = False

    @model_validator(mode="after")
    def validate_request(self):
        if self.command not in COMMANDS:
            raise ValueError("unsupported command")
        if self.execute and self.dry_run:
            raise ValueError("execute and dry_run are mutually exclusive")
        if (self.execute or self.dry_run) and self.command not in MUTATING:
            raise ValueError("execute and dry_run apply only to DR operations")
        if self.resume and self.command not in {"switchover", "failover"}:
            raise ValueError("resume applies only to switchover or failover")
        if self.command != "failover" and any((self.source_fenced, self.accept_data_loss,
                                                  self.allow_stale_inventory, self.use_follower_annotations)):
            raise ValueError("failover attestation fields apply only to failover")
        if self.allow_recreate and self.command != "reprotect":
            raise ValueError("allow_recreate applies only to reprotect")
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
    for field, flag in (("use_private_ip", "--use-private-ip"), ("execute", "--execute"),
                        ("dry_run", "--dry-run"), ("resume", "--resume"),
                        ("source_fenced", "--source-fenced"), ("accept_data_loss", "--accept-data-loss"),
                        ("allow_stale_inventory", "--allow-stale-inventory"),
                        ("use_follower_annotations", "--use-follower-annotations"),
                        ("allow_recreate", "--allow-recreate"), ("scheduled", "--scheduled")):
        if data.get(field):
            args.append(flag)
    return args


class JobManager:
    def __init__(self):
        self.jobs = queue.Queue()
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

    def submit(self, request):
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "submitted_at": utc_now(), "started_at": None, "finished_at": None,
               "status": "queued", "exit_code": None, "request": dump_model(request),
               "output": self.output_path(job_id).name}
        self.write(job)
        self.jobs.put(job_id)
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

    def _work(self):
        while True:
            job_id = self.jobs.get()
            try:
                job = self.read(job_id)
                job.update(status="running", started_at=utc_now())
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
    if (state_dir / "discovery.json").is_file() or (state_dir / "state.json").is_file():
        return
    manager.submit(OperationRequest(command="inventory"))


@app.get("/healthz")
def health():
    return {"status": "ok", "config_present": CONFIG.is_file(), "time": utc_now()}


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    """A local web console. Data remains protected by the API Bearer token."""
    return DASHBOARD_HTML


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(token_auth)])
def submit(request: OperationRequest):
    job = api_manager().submit(request)
    return {"id": job["id"], "status": job["status"], "submitted_at": job["submitted_at"]}


@app.get("/v1/jobs", dependencies=[Depends(token_auth)])
def jobs():
    return {"jobs": api_manager().recent()}


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


@app.get("/v1/commands", dependencies=[Depends(token_auth)])
def commands():
    return {"commands": sorted(COMMANDS), "mutating_commands": sorted(MUTATING),
            "execution": "serialized", "default_dr_mode": "dry-run"}


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
    credentials = cloud.credentials(secret_id)
    cfg = runtime_config(discovery, state_dir, configured.get("log_dir", "/var/log/oci-opensearch-fsdr"),
                         configured.get("ca_file"), configured.get("leader_role"),
                         configured.get("follower_role"))
    cfg.update(request_timeout=float(configured.get("request_timeout", 20)),
               wait_timeout=float(configured.get("wait_timeout", 1800)),
               poll_interval=float(configured.get("poll_interval", 2)),
               use_private_ip=configured.get("use_private_ip", False))
    observed = clients(cfg, credentials)
    app.state.observer = {"at": now, "secret_id": secret_id, "cfg": cfg, "clients": observed}
    return cfg, observed


def monitor_snapshot():
    """Read OpenSearch safely for the web observer without changing FSDR state.

    It deliberately uses the saved discovery record and Vault credentials only
    in memory.  Unlike an inventory job it never takes the controller lock or
    writes a manifest, so it can show counts and CCR status while a transition
    owns the controller.
    """
    configured = _read_json(CONFIG, {})
    state_dir = Path(configured.get("state_dir", "/var/lib/oci-opensearch-fsdr"))
    state = _read_json(state_dir / "state.json", {})
    secret_id = configured.get("secret_id")
    base = {"observed_at": utc_now(), "phase": state.get("phase"),
            "primary": state.get("active"), "inventory_at": state.get("inventory_at"),
            "current_step": (state.get("transition") or {}).get("step"),
            "current_index": (state.get("transition") or {}).get("current_index"),
            "indices": state.get("indices", []), "clusters": {}, "errors": []}
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
        base["errors"].append("Cannot prepare OpenSearch observer: {}".format(type(exc).__name__))
        return base
    for name in ("a", "b"):
        cluster = cfg["clusters"][name]
        view = {"name": cluster["display_name"], "id": cluster["id"],
                "role": "primary" if state.get("active") == name else "follower",
                "indices": [], "documents": 0, "error": None}
        for entry in state.get("indices", []):
            index = entry.get(name)
            if not index:
                continue
            item = {"index": index, "documents": None, "replication": None, "error": None}
            try:
                item["documents"] = cs[name].count(index)
                view["documents"] += item["documents"]
                if state.get("active") != name:
                    status = cs[name].status(index)
                    details = status.get("syncing_details", {})
                    item["replication"] = {"status": status.get("status"),
                                           "leader_checkpoint": details.get("leader_checkpoint"),
                                           "follower_checkpoint": details.get("follower_checkpoint")}
                else:
                    item["replication"] = {"status": "PRIMARY"}
            except Exception as exc:
                item["error"] = type(exc).__name__
            view["indices"].append(item)
        if any(item["error"] for item in view["indices"]):
            view["error"] = "One or more index observations failed."
        base["clusters"][name] = view
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
