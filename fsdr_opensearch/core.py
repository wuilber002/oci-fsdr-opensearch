"""Configuration, durable local/shared-filesystem state, locks and audit logging."""

import contextlib
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid


LOG = logging.getLogger("fsdr")


class Error(RuntimeError):
    pass


class BusyError(Error):
    """A concurrent administrative operation currently owns the lock."""


def feedback(message):
    """Emit a short, human-facing progress update without mixing it with JSON output.

    The complete audit trail remains in the JSONL file.  stderr is deliberate:
    stdout can still be consumed as JSON by automation.
    """
    stamp = time.strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, message), file=sys.stderr, flush=True)


def index_name(value):
    if (not isinstance(value, str) or len(value.encode()) > 255
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value)
            or value in (".", "..")):
        raise Error(f"Invalid index name: {value!r}")
    return value


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    if set(cfg["clusters"]) != {"a", "b"}:
        raise Error("Configure exactly clusters a and b")
    index_name(cfg["prefix"])
    if cfg["initial_primary"] not in ("a", "b"):
        raise Error("initial_primary deve ser a ou b")
    for name, cluster in cfg["clusters"].items():
        url = urlsplit(cluster["url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise Error(f"Cluster {name}: provide an HTTPS endpoint without credentials or path")
        cluster["url"] = cluster["url"].rstrip("/")
        other = "b" if name == "a" else "a"
        if not cluster["remote_aliases"].get(other):
            raise Error(f"Missing remote alias on {name}")
        for field in ("username_env", "password_env", "leader_role", "follower_role"):
            if not cluster.get(field):
                raise Error(f"Required field on {name}: {field}")
        if cluster.get("ca_file"):
            cluster["ca_file"] = str((path.parent / cluster["ca_file"]).resolve())
    if cfg["clusters"]["a"]["url"] == cfg["clusters"]["b"]["url"]:
        raise Error("Endpoints must differ")
    for field in ("state_dir", "log_dir"):
        cfg[field] = str((path.parent / cfg[field]).resolve())
    for field, default in (("request_timeout", 20), ("wait_timeout", 1800),
                           ("poll_interval", 2), ("dashboard_interval", 2),
                           ("operations_per_second", 2)):
        cfg.setdefault(field, default)
        if not isinstance(cfg[field], (int, float)) or not 0 < cfg[field] < float("inf"):
            raise Error(f"{field} must be positive and finite")
    cfg.setdefault("read_retries", 2)
    if not isinstance(cfg["read_retries"], int) or not 0 <= cfg["read_retries"] <= 10:
        raise Error("read_retries must be between 0 and 10")
    cfg.setdefault("create_probability", 0.6)
    if not 0 <= cfg["create_probability"] <= 1:
        raise Error("create_probability must be between 0 and 1")
    identity = {"clusters": {k: v["url"] for k, v in cfg["clusters"].items()},
                "prefix": cfg["prefix"]}
    cfg["identity"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return cfg


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id, secrets):
        super().__init__()
        self.run_id = run_id
        self.secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def format(self, record):
        item = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "level": record.levelname, "run_id": self.run_id,
                "event": record.getMessage()}
        if record.exc_info:
            item["exception"] = self.formatException(record.exc_info)
        output = json.dumps(item, ensure_ascii=False)
        for secret in self.secrets:
            output = output.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        return output


def configure_logging(cfg, console=False):
    directory = Path(cfg["log_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = uuid.uuid4().hex
    secrets = [os.environ.get(c.get("password_env", ""), "")
               for c in cfg.get("clusters", {}).values()]
    formatter = JsonFormatter(run_id, secrets)
    LOG.setLevel(logging.INFO)
    for old_handler in LOG.handlers:
        old_handler.close()
    LOG.handlers.clear()
    handler = RotatingFileHandler(directory / f"fsdr-{run_id}.jsonl", maxBytes=20_000_000,
                                  backupCount=10, encoding="utf-8")
    handler.setFormatter(formatter)
    LOG.addHandler(handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        LOG.addHandler(stream)
    LOG.info("execution_started")
    return run_id


def redact_secrets(values):
    """Register newly retrieved secrets before any further processing or logging."""
    for handler in LOG.handlers:
        if isinstance(handler.formatter, JsonFormatter):
            handler.formatter.secrets = sorted(set(handler.formatter.secrets) | {
                value for value in values if isinstance(value, str) and value}, key=len, reverse=True)


def write_json(path, data):
    """Atomically write a private, durable JSON file without logging its contents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        self.directory = Path(cfg["state_dir"])
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.json"

    @contextlib.contextmanager
    def lock(self, blocking=True, name="operation"):
        # Dashboard writer and every administrative command share this same lock.
        with (self.directory / f"{name}.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise BusyError("Another operation holds the lock; generator/DR cannot run together") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self, required=True):
        if not self.path.exists():
            if required:
                raise Error("State is missing: run setup or inventory first")
            return None
        data = json.loads(self.path.read_text())
        if data.get("schema") != self.cfg.get("state_schema", 1) or data.get("identity") != self.cfg["identity"]:
            raise Error("State is incompatible with configuration identity or format version")
        return data

    def write(self, data):
        data["updated_at"] = time.time()
        write_json(self.path, data)
        if self.cfg.get("history_enabled"):
            archive(self.directory, "state", data)

    def new(self):
        return {"schema": self.cfg.get("state_schema", 1), "identity": self.cfg["identity"],
                "active": self.cfg.get("initial_primary"), "phase": "READY", "indices": []}


def other(name):
    return "b" if name == "a" else "a"


def archive(directory, kind, data):
    """Historical copies are secondary to the committed current state."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = Path(directory) / "history" / f"{kind}-{stamp}-{uuid.uuid4().hex}.json"
    try:
        write_json(path, data)
    except OSError:
        LOG.exception("history_write_failed kind=%s current_state_preserved=true", kind)
