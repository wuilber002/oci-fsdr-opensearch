"""Durable, credential-free FSDR execution evidence in OCI Object Storage.

The controller keeps a small local outbox first, then publishes immutable
bundles.  A failed upload never changes a completed OpenSearch operation into
a failed operation; it remains queued locally and is retried by the next job.
"""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import time
import uuid

from .cloud import Cloud
from .core import Error, LOG, write_json


SCHEMA = 1
OUTBOX = "evidence-outbox"
CURRENT_FILES = ("state.json", "dr-cycle.json", "workflow.json", "discovery.json", "inventory-health.json", "last-run.json")


def _identity(directory, secret_id):
    """Use a non-secret, stable path component even before state exists."""
    state_path = Path(directory) / "state.json"
    try:
        identity = json.loads(state_path.read_text(encoding="utf-8")).get("identity")
    except (OSError, ValueError, TypeError):
        identity = None
    if isinstance(identity, str) and identity:
        return identity
    return hashlib.sha256(secret_id.encode("utf-8")).hexdigest()


def _artifact_files(directory, report=None):
    directory = Path(directory)
    selected = []
    for name in CURRENT_FILES:
        path = directory / name
        if path.is_file() and not path.is_symlink():
            selected.append((name, path))
    # Preserve the current structured result separately from the readable log.
    # Only a controller-generated path below ``results`` is admissible.
    result_name = (report or {}).get("result_file") if isinstance(report, dict) else None
    if isinstance(result_name, str) and result_name.startswith("results/"):
        path = directory / result_name
        if path.is_file() and not path.is_symlink() and path.parent == directory / "results":
            selected.append((result_name, path))
    # Evidence records hold integrity hashes for each completed transition.
    history = directory / "history"
    if history.is_dir() and not history.is_symlink():
        for path in sorted(history.glob("evidence-*.json")):
            if path.is_file() and not path.is_symlink():
                selected.append(("history/" + path.name, path))
    return selected


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_bundle(directory, secret_id, prefix, report):
    """Stage one immutable bundle locally before any network call."""
    directory = Path(directory)
    now = time.time()
    execution_id = uuid.uuid4().hex
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    identity = _identity(directory, secret_id)
    try:
        cycle = json.loads((directory / "dr-cycle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        cycle = {}
    cycle_id = report.get("cycle_id") or cycle.get("id")
    if isinstance(cycle_id, str) and cycle_id:
        remote_base = "{}/cycles/{}/executions/{}-{}".format(prefix, cycle_id, stamp, execution_id)
    else:
        # Scheduled inventory remains durable operational evidence, but never
        # starts or impersonates a recovery cycle.
        remote_base = "{}/maintenance/{}/executions/{}-{}".format(prefix, identity, stamp, execution_id)
    outbox = directory / OUTBOX
    outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = outbox / (stamp + "-" + execution_id)
    bundle = base.with_suffix(".tar.gz")
    manifest_path = base.with_suffix(".manifest.json")
    files = _artifact_files(directory, report)
    entries = [{"name": name, "sha256": _digest(path), "bytes": path.stat().st_size}
               for name, path in files]
    manifest = {
        "schema": SCHEMA,
        "created_at": now,
        "identity": identity,
        "cycle_id": cycle_id if isinstance(cycle_id, str) else None,
        "cycle_status": cycle.get("status") if isinstance(cycle, dict) else None,
        "command": report.get("command"),
        "outcome": report.get("outcome"),
        "transition_id": ((json.loads((directory / "state.json").read_text(encoding="utf-8"))
                           if (directory / "state.json").is_file() else {}).get("transition") or {}).get("id"),
        "remote_base": remote_base,
        "files": entries,
        "excluded": [
            "Vault Secret payload and OpenSearch credentials",
            "API bearer token and TLS private key",
            "raw controller logs and job output",
        ],
    }
    report_bytes = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    with tarfile.open(bundle, "w:gz") as archive:
        info = tarfile.TarInfo("report.json")
        info.size = len(report_bytes)
        info.mtime = int(now)
        archive.addfile(info, io.BytesIO(report_bytes))
        for name, path in files:
            archive.add(path, arcname=name, recursive=False)
    try:
        bundle.chmod(0o600)
    except OSError:
        pass
    write_json(manifest_path, manifest)
    try:
        manifest_path.chmod(0o600)
    except OSError:
        pass
    return manifest_path


def _load_manifest(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Error("Invalid queued remote-evidence manifest: {}".format(path.name)) from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("remote_base"), str):
        raise Error("Invalid queued remote-evidence manifest: {}".format(path.name))
    return value


def _publish_one(cloud, region, bucket, manifest_path):
    manifest = _load_manifest(manifest_path)
    bundle_path = manifest_path.with_suffix("").with_suffix(".tar.gz")
    if not bundle_path.is_file() or bundle_path.is_symlink():
        raise Error("Queued remote-evidence bundle is missing: {}".format(bundle_path.name))
    client, namespace = cloud.object_storage(region)
    base = manifest["remote_base"]
    payloads = (
        (base + "/bundle.tar.gz", bundle_path.read_bytes(), "application/gzip"),
        (base + "/manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
         "application/json"),
    )
    for name, payload, content_type in payloads:
        cloud._read("PutObject", lambda name=name, payload=payload, content_type=content_type: client.put_object(
            namespace, bucket, name, io.BytesIO(payload), content_length=len(payload), content_type=content_type,
            retry_strategy=cloud.sdk.retry.NoneRetryStrategy()))
    bundle_path.unlink()
    manifest_path.unlink()
    return base


def publish_pending(directory, secret_id, bucket, prefix, timeout):
    """Publish all staged bundles; return the remote paths and leave failures queued."""
    if not bucket:
        return {"status": "not_configured", "published": [], "pending": 0}
    outbox = Path(directory) / OUTBOX
    manifests = sorted(outbox.glob("*.manifest.json")) if outbox.is_dir() else []
    if not manifests:
        return {"status": "published", "published": [], "pending": 0}
    cloud = Cloud(timeout=timeout)
    region = cloud.region(secret_id)
    published, pending = [], 0
    for manifest_path in manifests:
        try:
            published.append(_publish_one(cloud, region, bucket, manifest_path))
        except Exception as exc:
            pending += 1
            LOG.warning("remote_evidence_publish_pending bundle=%s error=%s", manifest_path.name, type(exc).__name__)
    return {"status": "published" if not pending else "pending", "published": published, "pending": pending}


def stage_and_publish(directory, secret_id, bucket, prefix, timeout, report):
    """Queue the current evidence, then try the complete outbox.

    Evidence is deliberately not staged without a configured bucket: local
    persistence alone is not suitable for an ephemeral controller VM.
    """
    if not bucket:
        return {"status": "not_configured", "published": [], "pending": 0}
    _create_bundle(directory, secret_id, prefix, report)
    return publish_pending(directory, secret_id, bucket, prefix, timeout)
