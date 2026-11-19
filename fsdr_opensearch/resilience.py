"""Read-only DR readiness checks: CCR RPO and OpenSearch snapshots."""

from urllib.parse import quote
import time

from .core import Error
from .reporting import freshness
from .core import other


def checkpoint_lag(status):
    details = status.get("syncing_details", status) if isinstance(status, dict) else {}
    leader, follower = details.get("leader_checkpoint"), details.get("follower_checkpoint")
    if not isinstance(leader, int) or not isinstance(follower, int):
        return None
    return max(leader - follower, 0)


def rpo(cfg, state, clients=None):
    """Return replication lag without treating equal document counts as proof."""
    state = state or {}
    source, target = state.get("active"), other(state.get("active")) if state.get("active") in ("a", "b") else None
    rows = []
    for entry in state.get("indices", []):
        saved = entry.get("replication", {})
        status = saved
        if clients and target:
            status = clients[target].status(entry[target])
        lag = checkpoint_lag(status)
        rows.append({"leader": entry.get(source) if source else None,
                     "follower": entry.get(target) if target else None,
                     "status": status.get("status") if isinstance(status, dict) else None,
                     "lag_operations": lag})
    known = [row["lag_operations"] for row in rows if row["lag_operations"] is not None]
    max_lag = max(known) if known else None
    limit = cfg.get("max_checkpoint_lag", 0)
    status = "unknown" if len(known) != len(rows) else "exceeded" if max_lag > limit else "within_limit"
    return {"status": status, "max_lag_operations": max_lag, "max_allowed_operations": limit,
            "enforced": cfg.get("enforce_rpo", False), "indices": rows}


def snapshots(cfg, state, clients):
    repositories = cfg.get("snapshot_repositories")
    if not repositories:
        return {"status": "not_configured", "required": cfg.get("snapshot_required", False), "clusters": {}}
    now = time.time()
    clusters = {}
    active = state.get("active")
    for name, repository in repositories.items():
        repo = quote(repository, safe="-_.")
        # Check repository registration separately: it produces a clearer failure than _all.
        registration = clients[name].request("GET", f"/_snapshot/{repo}")
        if repository not in registration:
            raise Error(f"Snapshot repository not confirmed on {name}: {repository}")
        data = clients[name].request("GET", f"/_snapshot/{repo}/_all?verbose=false")
        successful = [item for item in data.get("snapshots", []) if item.get("state") == "SUCCESS"
                      and isinstance(item.get("end_time_in_millis"), int)]
        latest = max(successful, key=lambda item: item["end_time_in_millis"], default=None)
        required_indices = {entry[name] for entry in state.get("indices", []) if name == active}
        covered = set(latest.get("indices", [])) if latest else set()
        age = (now - latest["end_time_in_millis"] / 1000) if latest else None
        clusters[name] = {"repository": repository, "latest": latest.get("snapshot") if latest else None,
                          "age_seconds": round(max(age, 0), 1) if age is not None else None,
                          "max_age_seconds": cfg.get("max_snapshot_age_hours", 24) * 3600,
                          "covered_indices": sorted(required_indices & covered),
                          "missing_indices": sorted(required_indices - covered),
                          "status": "fresh" if latest and age <= cfg.get("max_snapshot_age_hours", 24) * 3600
                          and not (required_indices - covered) else "stale_or_incomplete"}
    all_fresh = all(item["status"] == "fresh" for item in clusters.values())
    return {"status": "ready" if all_fresh else "not_ready", "required": cfg.get("snapshot_required", False),
            "clusters": clusters}


def readiness(cfg, state, clients):
    rpo_result = rpo(cfg, state, clients)
    snapshot_result = snapshots(cfg, state, clients)
    inventory_result = freshness(state, cfg.get("max_inventory_age_seconds", 14400))
    gates = [inventory_result["status"] == "fresh"]
    if cfg.get("enforce_rpo", False):
        gates.append(rpo_result["status"] == "within_limit")
    if cfg.get("snapshot_required", False):
        gates.append(snapshot_result["status"] == "ready")
    return {"ready": all(gates), "checked_at": time.time(), "inventory": inventory_result,
            "rpo": rpo_result, "snapshots": snapshot_result,
            "gates": {"inventory_fresh": inventory_result["status"] == "fresh",
                      "rpo": not cfg.get("enforce_rpo", False) or rpo_result["status"] == "within_limit",
                      "snapshots": not cfg.get("snapshot_required", False) or snapshot_result["status"] == "ready"}}
