"""Private state, configuration and JSON logging for the standalone lab."""

import contextlib
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
import uuid


LOG = logging.getLogger("opensearch_lab")


class Error(RuntimeError):
    pass


class BusyError(Error):
    pass


def feedback(message):
    """Short operator progress while detailed diagnostics remain in JSONL."""
    print("[{}] {}".format(time.strftime("%H:%M:%S"), message), file=sys.stderr, flush=True)


def other(name):
    return "b" if name == "a" else "a"


def index_name(value):
    if (not isinstance(value, str) or len(value.encode()) > 255 or
            not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value) or value in (".", "..")):
        raise Error("Nome de índice inválido: {!r}".format(value))
    return value


def lab_settings(args):
    """Return configuration owned entirely by the lab package.

    OCI discovery and the live CCR APIs supply topology.  The lab never reads
    FSDR configuration, inventory, state or logs.
    """
    state_dir, log_dir = Path(args.state_dir).resolve(), Path(args.log_dir).resolve()
    prefix = args.prefix
    index_name(prefix)
    secret_id = args.secret_id or os.environ.get("OPENSEARCH_LAB_SECRET_ID")
    if not isinstance(secret_id, str) or not secret_id:
        raise Error("Informe --secret-id ou defina OPENSEARCH_LAB_SECRET_ID")
    return {"secret_id": secret_id,
            "prefix": prefix, "state_dir": str(state_dir), "log_dir": str(log_dir),
            "ca_file": args.ca_file, "use_private_ip": args.use_private_ip,
            "request_timeout": args.request_timeout, "read_retries": 2,
            "wait_timeout": args.wait_timeout,
            "poll_interval": args.poll_interval,
            "dashboard_interval": args.dashboard_interval,
            "operations_per_second": args.operations_per_second,
            "create_probability": args.create_probability,
            "leader_role": args.leader_role, "follower_role": args.follower_role,
            "identity": hashlib.sha256(json.dumps({"secret_id": secret_id, "prefix": prefix},
                                                    sort_keys=True).encode()).hexdigest()}


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id):
        super().__init__()
        self.run_id, self.secrets = run_id, []

    def format(self, record):
        event = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                 "level": record.levelname, "run_id": self.run_id,
                 "event": record.getMessage()}
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        text = json.dumps(event, ensure_ascii=False)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text


def configure_logging(cfg, console=True):
    directory = Path(cfg["log_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    formatter = JsonFormatter(uuid.uuid4().hex)
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    file_handler = RotatingFileHandler(directory / "lab.jsonl", maxBytes=20_000_000,
                                       backupCount=10, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)
    if console:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        LOG.addHandler(handler)
    LOG.info("lab_execution_started")


def redact_secrets(values):
    for handler in LOG.handlers:
        if isinstance(handler.formatter, JsonFormatter):
            handler.formatter.secrets = sorted(set(handler.formatter.secrets) | set(filter(None, values)), key=len, reverse=True)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".lab-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, cfg):
        self.cfg, self.directory = cfg, Path(cfg["state_dir"])
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.json"
        self.demo_identity_path = self.directory / "demo-identity.json"

    @contextlib.contextmanager
    def lock(self, blocking=True, name="operation"):
        with (self.directory / (name + ".lock")).open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise BusyError("Outra operação do lab detém o lock") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self, required=True):
        if not self.path.exists():
            if required:
                raise Error("Estado do lab ausente: execute setup_demo.py primeiro")
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("identity") != self.cfg["identity"]:
            raise Error("Estado do lab incompatível com esta configuração")
        return data

    def write(self, data):
        data["updated_at"] = time.time()
        write_json(self.path, data)

    def remove(self):
        """Remove only this lab's state file after a completed cleanup."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def demo_uuid(self):
        """Return the stable marker for this extracted lab installation."""
        if self.demo_identity_path.exists():
            try:
                data = json.loads(self.demo_identity_path.read_text(encoding="utf-8"))
                value = data.get("demo_uuid")
            except (OSError, json.JSONDecodeError, AttributeError):
                raise Error("Identidade persistente do laboratório inválida: {}".format(
                    self.demo_identity_path)) from None
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise Error("demo_uuid persistente inválido: {}".format(self.demo_identity_path))
            return value
        value = secrets.token_hex(32)
        write_json(self.demo_identity_path, {"schema": 1, "demo_uuid": value})
        return value

    def new(self, active):
        return {"schema": 1, "identity": self.cfg["identity"], "active": active,
                "phase": "READY", "indices": []}
