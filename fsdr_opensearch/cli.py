"""FSDR CLI: one Secret OCID identifies the pair and its credentials."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

from .client import clients
from .cloud import (Cloud, default_state_dir, discover, ocid, pair_identity,
                    read_cache, runtime_config, save_cache)
from .core import BusyError, Error, LOG, Store, archive, configure_logging, feedback, write_json
from .operations import (inventory, precheck, reprotect, transition,
                         validate_post_switchover, validate_switchover)
from .reporting import cleanup, raw_state, record_evidence, record_inventory_attempt, summary
from .resilience import readiness
from .settings import configuration_reference, merge_args


SYSTEM_CONFIG = Path("/etc/oci-opensearch-fsdr/config.json")


STEP_LABELS = {
    "block_source": "write block on former primary",
    "final_sync": "final CCR synchronization",
    "stop_followers": "follower stop",
    "enable_target": "write enablement on new primary",
    "reprotect": "reprotection",
}


def human_step(value):
    """Present durable state fields as operator language, never as ``None``."""
    if not value:
        return "none"
    return STEP_LABELS.get(value, str(value))


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("provide a positive finite number")
    return number


def positive_integer(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("provide a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("provide a positive integer")
    return number


def parser():
    root = argparse.ArgumentParser(description="OCI FSDR / OpenSearch: discovery and DR using a Secret OCID")
    root.add_argument("--config-help", action="store_true",
                      help="show configuration-file field reference and exit")
    commands = root.add_subparsers(dest="command")
    reference = commands.add_parser("config-help", help="show configuration-file field reference")
    reference.add_argument("--json", action="store_true", help="emit the reference as JSON")
    for name in ("discover", "precheck", "inventory", "readiness", "validate-switchover",
                 "validate-post-switchover", "status", "cleanup", "switchover", "failover", "reprotect"):
        cmd = commands.add_parser(name)
        cmd.add_argument("secret_id", nargs="?", help="Secret OCID; may be obtained from --config")
        cmd.add_argument("--config", help="FSDR runtime configuration; default: /etc/oci-opensearch-fsdr/config.json when present")
        cmd.add_argument("--state-dir", help="persistent/shared filesystem; do not use lab state")
        cmd.add_argument("--log-dir")
        cmd.add_argument("--ca-file", help="CA adicional dos endpoints OpenSearch")
        cmd.add_argument("--use-private-ip", action="store_true", default=None,
                         help="connect to OCI private IP while preserving FQDN TLS validation")
        cmd.add_argument("--request-timeout", type=positive_seconds)
        cmd.add_argument("--wait-timeout", type=positive_seconds)
        cmd.add_argument("--poll-interval", type=positive_seconds)
        cmd.add_argument("--max-inventory-age-hours", type=positive_seconds)
        cmd.add_argument("--retention-days", type=int)
        cmd.add_argument("--human", action="store_true", help="show human-readable summary instead of full JSON")
        cmd.add_argument("--leader-role", help="override for a custom CCR role mapped on both clusters")
        cmd.add_argument("--follower-role", help="override for a custom CCR role mapped on both clusters")
        if name in ("switchover", "failover", "reprotect"):
            mode = cmd.add_mutually_exclusive_group()
            mode.add_argument("--execute", action="store_true", help="apply changes; default is simulation")
            mode.add_argument("--dry-run", action="store_true", help="inspect and plan without changing clusters")
        if name in ("switchover", "failover"):
            cmd.add_argument("--resume", action="store_true")
        if name == "failover":
            cmd.add_argument("--source-fenced", action="store_true", help="attest external isolation of the former primary")
            cmd.add_argument("--accept-data-loss", action="store_true")
            cmd.add_argument("--allow-stale-inventory", action="store_true", help="explicitly accept stale/invalid inventory")
            cmd.add_argument("--use-follower-annotations", action="store_true",
                             help="for stale inventory, require matching _meta.fsdr annotations on the follower")
        if name == "inventory":
            cmd.add_argument("--scheduled", action="store_true", help="timer mode: skips disabled collection or DR in progress")
        if name == "reprotect":
            cmd.add_argument("--allow-recreate", action="store_true", help="authorize deletion of inventoried former-primary indices")
            cmd.add_argument("--reprotect-parallelism", type=positive_integer, default=1,
                             help="maximum simultaneous CCR bootstraps; default: 1")
    return root


def print_configuration_reference(as_json=False):
    """Print configuration help without loading a file, OCI SDK, or credentials."""
    reference = configuration_reference()
    if as_json:
        print(json.dumps({"fields": reference}, indent=2, ensure_ascii=False))
        return
    print("FSDR configuration-file reference")
    print("Use fsdr-config.example.json as the model. Omitted fields use their default values.")
    for item in reference:
        default = json.dumps(item["default"], ensure_ascii=False)
        print("\n%s (default: %s)" % (item["field"], default))
        print("  %s" % item["description"])
    print("\nFull documentation: docs/CONTAINER.rst (runtime configuration file).")


def apply_system_config_default(args):
    """Use the installed configuration when the caller did not select one."""
    if getattr(args, "config", None) is None and SYSTEM_CONFIG.is_file():
        args.config = str(SYSTEM_CONFIG)


def bootstrap(args, directory, log_dir):
    cloud = Cloud(timeout=args.request_timeout)
    by_id = cloud.credentials(args.secret_id)
    names = dict(zip(("a", "b"), sorted(by_id)))
    state_cfg = {"state_dir": str(directory), "identity": pair_identity(by_id), "state_schema": 2}
    existing = Store(state_cfg).read(required=False)
    allowed = set()
    if args.command == "failover" and existing:
        tr = existing.get("transition", {})
        source = tr["source"] if getattr(args, "resume", False) and tr.get("mode") == "failover" else existing["active"]
        if source in names:
            allowed.add(names[source])
    discovered = discover(cloud, args.secret_id, by_id, directory, cached_allowed=allowed)
    cfg = runtime_config(discovered, directory, log_dir, args.ca_file, args.leader_role, args.follower_role)
    cfg.update(request_timeout=args.request_timeout, wait_timeout=args.wait_timeout, poll_interval=args.poll_interval)
    cfg.update(use_private_ip=args.use_private_ip)
    cfg.update(history_enabled=True, max_inventory_age_seconds=args.max_inventory_age_hours * 3600)
    cfg.update(max_checkpoint_lag=args.max_checkpoint_lag, enforce_rpo=args.enforce_rpo,
               snapshot_repositories=args.snapshot_repositories, max_snapshot_age_hours=args.max_snapshot_age_hours,
               snapshot_required=args.snapshot_required)
    save_cache(directory, discovered)
    archive(directory, "discovery", discovered)
    credentials = {name: by_id[cluster_id] for name, cluster_id in names.items()}
    return cfg, credentials, discovered


def finish(args, directory, result, cfg, started, outcome="success", reason=None):
    state = raw_state(directory)
    report = summary(result, state, args.max_inventory_age_hours * 3600, time.monotonic() - started, outcome)
    report.update(command=args.command, reason=reason, finished_at=time.time())
    if args.command == "inventory":
        record_inventory_attempt(directory, outcome, reason, (state or {}).get("inventory_at"))
    with Store({"state_dir": str(directory)}).lock(name="report"):
        write_json(Path(directory) / "last-run.json", report)
    archive(directory, "run", report)
    if (outcome == "success" and args.command in ("switchover", "failover", "reprotect")
            and getattr(args, "execute", False)):
        record_evidence(directory, args.command, report)
    if outcome in ("failed", "skipped"):
        try:
            with Store({"state_dir": str(directory)}).lock(blocking=False, name="controller"):
                logs = Path(args.log_dir).resolve() if args.log_dir else Path(directory) / "logs"
                LOG.info("retention_result result=%s", cleanup(directory, logs, args.retention_days))
        except BusyError:
            LOG.info("retention_skipped reason=controller_busy")
    clusters = {name: {"id": cluster["id"], "display_name": cluster["display_name"]}
                for name, cluster in (cfg or {}).get("clusters", {}).items()}
    if args.human:
        print(f"Command: {args.command} | outcome: {outcome} | duration: {report['duration_seconds']}s")
        print(f"Phase: {report['phase']} | primary: {report['active']} | indices: {report['index_count']}")
        for name, cluster in clusters.items():
            print(f"  {name}: {cluster['display_name']} ({cluster['id']})")
        print(f"Inventory: {report['inventory']['status']} | age: {report['inventory']['age_seconds']}s")
        if report["phase"] == "REPROTECTING":
            protection = "Reprotect progress: {} index/indices".format(report["reprotected_indices"])
        elif report["phase"] == "PROTECTION_RESTORED":
            protection = "Protection: restored"
        elif report["phase"] == "SERVICE_RESTORED":
            protection = "Protection: reverse replication pending"
        else:
            protection = "Protection: not established"
        print("Current step: {} | Current index: {} | {}".format(
            human_step(report["current_step"]), report["current_index"] or "none", protection))
        if reason:
            print(f"Reason: {reason}")
    else:
        print(json.dumps({"result": result, "clusters": clusters, "summary": report}, indent=2, ensure_ascii=False))
    LOG.info("execution_finished command=%s outcome=%s reason=%s duration=%.3f", args.command, outcome,
             reason, report["duration_seconds"])


def main(argv=None):
    args = parser().parse_args(argv)
    if args.config_help:
        print_configuration_reference()
        return 0
    if args.command == "config-help":
        print_configuration_reference(args.json)
        return 0
    if args.command is None:
        parser().error("provide a command or use --config-help")
    apply_system_config_default(args)
    os.umask(0o077)
    configured = False
    started = time.monotonic()
    directory, cfg = None, None
    try:
        settings = merge_args(args)
        if args.retention_days < 1:
            raise Error("retention-days must be positive")
        directory = Path(args.state_dir).resolve() if args.state_dir else (
            default_state_dir(args.secret_id) if args.secret_id else Path(__file__).resolve().parent.parent / "var/dr-pending")
        log_dir = Path(args.log_dir).resolve() if args.log_dir else directory / "logs"
        # Detailed HTTP/OCI events belong in the JSONL audit log.  The console
        # is reserved for the final human/JSON result and explicit failures.
        configure_logging({"log_dir": str(log_dir)}, console=False)
        configured = True
        LOG.info("command_started command=%s", args.command)
        if not getattr(args, "scheduled", False):
            feedback("Starting %s. Concise progress is shown in the terminal; details are in %s." %
                     (args.command, log_dir))
        if getattr(args, "scheduled", False) and (not settings["inventory_enabled"] or not args.secret_id):
            finish(args, directory, None, None, started, "skipped", "inventory_disabled_or_secret_pending")
            return 0
        if args.command == "cleanup":
            with Store({"state_dir": str(directory)}).lock(blocking=False, name="controller"):
                result = cleanup(directory, log_dir, args.retention_days)
                finish(args, directory, result, None, started,
                       "skipped" if result["skipped"] else "success", result.get("reason"))
            return 0
        ocid(args.secret_id, "vaultsecret")
        if args.command == "status":
            cached = read_cache(directory, args.secret_id)
            if not cached:
                raise Error("Sem cache; execute discover ou inventory primeiro")
            cfg = runtime_config(cached, directory, log_dir)
            result = Store(cfg).read()
            health_file = directory / "inventory-health.json"
            result["inventory_health"] = json.loads(health_file.read_text()) if health_file.exists() else None
            finish(args, directory, result, cfg, started)
        else:
            # Covers discovery cache and state as one administrative operation.
            with Store({"state_dir": str(directory)}).lock(blocking=False, name="controller"):
                if getattr(args, "scheduled", False):
                    read_cache(directory, args.secret_id)
                    state = raw_state(directory)
                    if state and state["phase"] not in ("READY", "PROTECTION_RESTORED"):
                        finish(args, directory, None, None, started, "skipped", "dr_in_progress")
                        return 0
                if not getattr(args, "scheduled", False):
                    feedback("Reading the Secret and OCI metadata for both clusters.")
                cfg, credentials, discovered = bootstrap(args, directory, log_dir)
                store = Store(cfg)
                if args.command == "discover":
                    feedback("Discovery completed for both clusters.")
                    result = discovered
                else:
                    cs = clients(cfg, credentials)
                    if args.command == "precheck":
                        feedback("Validating version, health, aliases, and CCR permissions.")
                        result = precheck(cfg, cs)
                    elif args.command == "inventory":
                        feedback("Identifying indices and current replication direction.")
                        result = inventory(cfg, cs, store)
                    elif args.command == "readiness":
                        feedback("Refreshing inventory and evaluating DR readiness.")
                        state = inventory(cfg, cs, store)
                        result = readiness(cfg, state, cs)
                    elif args.command == "validate-switchover":
                        feedback("Validating health, CCR, inventory, and switchover readiness.")
                        result = validate_switchover(cfg, cs, store)
                    elif args.command == "validate-post-switchover":
                        feedback("Validating promoted primary, write blocks, and document counts.")
                        result = validate_post_switchover(cfg, cs, store)
                    elif args.command in ("switchover", "failover"):
                        result = transition(cfg, cs, store, args.command, execute=args.execute, resume=args.resume,
                                            source_fenced=getattr(args, "source_fenced", False),
                                            accept_data_loss=getattr(args, "accept_data_loss", False),
                                            allow_stale_inventory=getattr(args, "allow_stale_inventory", False),
                                            use_follower_annotations=getattr(args, "use_follower_annotations", False))
                    else:
                        result = reprotect(cfg, cs, store, execute=args.execute,
                                           allow_recreate=args.allow_recreate,
                                           parallelism=args.reprotect_parallelism)
                retention = cleanup(directory, log_dir, args.retention_days)
                LOG.info("retention_result result=%s", retention)
                finish(args, directory, result, cfg, started)
        return 0
    except BusyError:
        if getattr(args, "scheduled", False):
            finish(args, directory, None, None, started, "skipped", "controller_busy")
            return 0
        LOG.warning("execution_busy command=%s", args.command)
        print("Another operation is in progress; try again later.", file=sys.stderr)
        return 75
    except KeyboardInterrupt:
        if configured:
            LOG.warning("execution_interrupted command=%s state_preserved=true", args.command)
        return 130
    except Exception as exc:
        if configured:
            LOG.exception("execution_failed command=%s", args.command)
            try:
                finish(args, directory, None, cfg, started, "failed", "execution_failed")
            except Exception:
                LOG.exception("failure_report_failed")
            print(f"Failure: {exc}. State preserved for analysis/resume; see {log_dir}.", file=sys.stderr)
        else:
            print(f"Failure before execution started: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
