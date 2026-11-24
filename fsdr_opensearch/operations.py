"""OpenSearch-only DR orchestration; no lab data or application routing."""

import time
import uuid

from .core import Error, LOG, feedback, index_name, other
from .reporting import freshness
from .resilience import rpo


IDLE = ("READY", "PROTECTION_RESTORED")
STOPPED = "REPLICATION NOT IN PROGRESS"
ANNOTATION_KEY = "fsdr"


def check_shards(result):
    if result.get("_shards", {}).get("failed", 0):
        raise Error("Resposta parcial: uma ou mais shards falharam")
    return result


def matching_status(status, cfg, follower, leader, leader_index):
    alias = status.get("leader_alias")
    remote_ids = cfg["clusters"][follower].get("remote_ocids")
    correct_alias = (remote_ids.get(alias) == cfg["clusters"][leader]["id"]
                     if remote_ids is not None else alias == cfg["clusters"][follower]["remote_aliases"].get(leader))
    if not correct_alias or status.get("leader_index") != leader_index:
        raise Error(f"CCR on {follower} does not match the expected leader/alias")


def no_autofollow(client):
    data = client.request("GET", "/_plugins/_replication/autofollow_stats")
    if "autofollow_stats" not in data:
        raise Error("Unrecognized auto-follow response; it is unsafe to continue")
    if data["autofollow_stats"]:
        raise Error(f"Auto-follow is active on {client.name}. This version uses explicit per-index CCR; "
                    "remove rules through an external procedure before the switch")


def select_roles(cfg, client, name):
    roles = client.request("GET", "/_plugins/_security/authinfo").get("roles", [])
    for kind in ("leader", "follower"):
        field = kind + "_role"
        requested = cfg["clusters"][name].get(field)
        choices = [requested] if requested else [f"cross_cluster_replication_{kind}_full_access", "all_access"]
        selected = next((role for role in choices if role in roles), None)
        if not selected:
            raise Error(f"User on {name} has no recognized {kind} role; map CCR roles "
                        f"or specify --{kind}-role for a custom role")
        cfg["clusters"][name][field] = selected


def precheck(cfg, cs, names=("a", "b"), require_reverse=True):
    infos = {}
    for name in names:
        client = cs[name]
        info = client.request("GET", "/")
        version = info["version"]["number"]
        if version != cfg["expected_version"]:
            raise Error(f"Version on {name}: {version}; expected: {cfg['expected_version']}")
        health = client.request("GET", "/_cluster/health")
        if health.get("status") != "green" or health.get("timed_out"):
            raise Error(f"Cluster {name} must be green before the operation")
        if cfg.get("scope") == "pair":
            select_roles(cfg, client, name)
        no_autofollow(client)
        aliases = client.request("GET", "/_remote/info")
        alias = cfg["clusters"][name]["remote_aliases"].get(other(name))
        if require_reverse and (not alias or alias not in aliases):
            peer = other(name)
            peer_cluster = cfg["clusters"][peer]
            current_cluster = cfg["clusters"][name]
            peer_label = peer_cluster.get("display_name", peer)
            current_label = current_cluster.get("display_name", name)
            peer_id = f" ({peer_cluster['id']})" if peer_cluster.get("id") else ""
            raise Error(
                f"CCR alias for {peer_label}{peer_id} is missing on {name} ({current_label}). "
                f"In OCI, first configure {peer} to accept {name} under Cross cluster connections > Inbound; "
                f"then create an Outbound connection on {name} with an alias pointing to {peer}. "
                "FSDR requires both directions to be prepared for reprotection after the switch."
            )
        # connected=false while idle is diagnostic, not proof of failure.
        LOG.debug("precheck cluster=%s version=%s health=green alias=%s connected=%s uuid=%s",
                 name, version, alias, aliases.get(alias, {}).get("connected"), info["cluster_uuid"])
        infos[name] = info
    if len(infos) == 2 and infos["a"]["cluster_uuid"] == infos["b"]["cluster_uuid"]:
        raise Error("Os dois endpoints apontam para o mesmo cluster")
    return infos


def scoped(cfg, value):
    # Membership in the CCR manifest defines DR scope; never a demo prefix.
    return index_name(value)


def annotated_indices(client):
    """Return marked indices, rejecting malformed FSDR metadata."""
    marked = set()
    for index in client.indices(scoped=False):
        mappings = client.request("GET", "/{}/_mapping".format(index))
        meta = mappings.get(index, {}).get("mappings", {}).get("_meta", {})
        annotation = meta.get(ANNOTATION_KEY) if isinstance(meta, dict) else None
        if annotation is None:
            continue
        if not isinstance(annotation, dict) or type(annotation.get("replicate")) is not bool:
            raise Error("Invalid _meta.fsdr annotation on {}; expected fsdr.replicate boolean".format(index))
        if annotation["replicate"]:
            marked.add(index)
    return marked


def validate_annotation_scope(cfg, cs, state, source, target):
    """Require the FSDR marker to cover exactly the discovered CCR scope.

    The marker is a mandatory declaration of the DR scope.  The inventory
    remains authoritative for UUIDs, direction and checkpoints, but it must
    never accept a replicated index that was not explicitly marked.
    """
    leaders = {entry[source] for entry in state["indices"]}
    followers = {entry[target] for entry in state["indices"]}
    marked_leaders, marked_followers = annotated_indices(cs[source]), annotated_indices(cs[target])
    if marked_leaders != leaders or marked_followers != followers:
        raise Error("FSDR replication marker is required for every CCR index: "
                    "leaders missing={} extra={}; followers missing={} extra={}. "
                    "Set _meta.fsdr.replicate=true on each replicated leader index and confirm the mapping on its follower".format(
                        sorted(leaders - marked_leaders), sorted(marked_leaders - leaders),
                        sorted(followers - marked_followers), sorted(marked_followers - followers)))
    return {"mode": "required",
            "leader_marked": sorted(marked_leaders), "follower_marked": sorted(marked_followers)}


def required_annotation_manifest(state, source, target):
    """Return the annotation evidence persisted by a compliant inventory."""
    expected_leaders = {entry[source] for entry in state["indices"]}
    expected_followers = {entry[target] for entry in state["indices"]}
    annotation = state.get("annotation") or {}
    actual_leaders = set(annotation.get("leader_marked", ()))
    actual_followers = set(annotation.get("follower_marked", ()))
    if (annotation.get("mode") != "required" or actual_leaders != expected_leaders
            or actual_followers != expected_followers):
        raise Error("Saved inventory has no complete required FSDR replication-marker evidence; "
                    "mark every CCR index and run inventory before DR operations")
    return {"leader": expected_leaders, "follower": expected_followers}


def validate_follower_annotation_scope(cs, state, source, target):
    """Confirm that the surviving follower still carries the required marker."""
    expected = required_annotation_manifest(state, source, target)["follower"]
    marked = annotated_indices(cs[target])
    if marked != expected:
        raise Error("Follower FSDR replication-marker scope differs from saved manifest: missing={} extra={}".format(
            sorted(expected - marked), sorted(marked - expected)))
    return {"cluster": target, "indices": sorted(marked), "source": source}


def validate_saved_annotation_scope(cs, state, source, target):
    """Validate saved evidence and the live marker on both reachable clusters."""
    annotation = state.get("annotation") or {}
    expected_all = ({entry[source] for entry in state["indices"]}
                    | {entry[target] for entry in state["indices"]})
    saved_all = set(annotation.get("leader_marked", ())) | set(annotation.get("follower_marked", ()))
    if annotation.get("mode") != "required" or saved_all != expected_all:
        raise Error("Saved inventory has no complete required FSDR replication-marker evidence; "
                    "mark every CCR index and run inventory before DR operations")
    expected = {"leader": {entry[source] for entry in state["indices"]},
                "follower": {entry[target] for entry in state["indices"]}}
    marked_leaders, marked_followers = annotated_indices(cs[source]), annotated_indices(cs[target])
    if marked_leaders != expected["leader"] or marked_followers != expected["follower"]:
        raise Error("Live FSDR replication-marker scope differs from saved manifest: leaders missing={} extra={}; "
                    "followers missing={} extra={}".format(
                        sorted(expected["leader"] - marked_leaders), sorted(marked_leaders - expected["leader"]),
                        sorted(expected["follower"] - marked_followers), sorted(marked_followers - expected["follower"])))
    return {"mode": "required", "leader_marked": sorted(marked_leaders),
            "follower_marked": sorted(marked_followers)}


def inventory(cfg, cs, store, persist=True):
    state = store.read(required=False) or store.new()
    if state["phase"] not in IDLE:
        raise Error("Inventory cannot replace state while setup/DR is in progress")
    precheck(cfg, cs, require_reverse=False)
    entries, directions, seen = [], set(), set()
    for target in ("a", "b"):
        source = other(target)
        for follower_index in cs[target].indices(scoped=False):
            status = cs[target].status(follower_index)
            if status.get("status") == STOPPED:
                continue
            alias = status.get("leader_alias")
            remotes = cfg["clusters"][target].get("remote_ocids")
            if remotes is not None:
                if alias not in remotes:
                    raise Error(f"CCR alias {alias} on {target} is absent from OCI discovery")
                if remotes[alias] != cfg["clusters"][source]["id"]:
                    LOG.debug("inventory_skip_external_peer cluster=%s index=%s alias=%s", target, follower_index, alias)
                    continue
            leader_index = scoped(cfg, status.get("leader_index", ""))
            matching_status(status, cfg, target, source, leader_index)
            if (source, leader_index) in seen:
                raise Error("Multiple followers of the same leader make reversal ambiguous")
            seen.add((source, leader_index))
            if cs[source].status(leader_index).get("status") != STOPPED:
                raise Error("Chained CCR topology is not supported")
            directions.add((source, target))
            details = status.get("syncing_details", {})
            entries.append({source: leader_index, target: scoped(cfg, follower_index),
                            "uuids": {source: cs[source].uuid(leader_index),
                                      target: cs[target].uuid(follower_index)},
                            "replication": {"status": status.get("status"),
                                            "leader_checkpoint": details.get("leader_checkpoint"),
                                            "follower_checkpoint": details.get("follower_checkpoint"),
                                            "observed_at": time.time()}})
    if not entries:
        raise Error("No CCR exists between the clusters; primary/standby cannot be inferred")
    if len(directions) != 1:
        raise Error("CCR exists in both directions for different sets; global primary is ambiguous")
    source, target = directions.pop()
    state["active"] = source
    state["indices"] = entries
    state["inventory_at"] = time.time()
    state["rpo"] = rpo(cfg, state)
    state["annotation"] = validate_annotation_scope(cfg, cs, state, source, target)
    if persist:
        store.write(state)
    LOG.debug("inventory source=%s target=%s indices=%d persisted=%s", source, target, len(entries), persist)
    return state


def validate_switchover(cfg, cs, store):
    """Persist and validate the read-only gates required before a planned swap."""
    state = inventory(cfg, cs, store)
    # Inventory establishes the direction.  This second check requires the
    # pre-created reverse aliases, which are necessary for reprotect.
    precheck(cfg, cs, require_reverse=True)
    from .resilience import readiness
    result = readiness(cfg, state, cs)
    if not result["ready"]:
        failed = [name for name, passed in result["gates"].items() if not passed]
        raise Error("Switchover pre-validation failed: {}".format(", ".join(failed)))
    LOG.debug("switchover_prevalidation_passed active=%s indices=%d", state["active"], len(state["indices"]))
    return {"validation": "switchover", "ready": True, "active": state["active"],
            "index_count": len(state["indices"]), "readiness": result}


def validate_post_switchover(cfg, cs, store):
    """Validate the promoted service before clients resume writes or reprotect starts.

    Document counts are deliberately compared while application writers remain
    paused.  Once the new primary accepts writes, count divergence from the old
    blocked copy is expected until reverse CCR is established.
    """
    state = store.read()
    transition = state.get("transition") or {}
    if state.get("phase") != "SERVICE_RESTORED":
        raise Error("Post-validation requires SERVICE_RESTORED; run switchover before reprotect")
    source, target = transition.get("source"), transition.get("target")
    if source not in ("a", "b") or target != other(source) or state.get("active") != target:
        raise Error("Transition state is incompatible with the promoted primary")
    precheck(cfg, cs, require_reverse=True)
    validate_saved_annotation_scope(cs, state, target, source)
    checked = []
    for entry in state["indices"]:
        old_index, new_index = scoped(cfg, entry[source]), scoped(cfg, entry[target])
        assert_uuid(cs[source], old_index, entry["uuids"][source])
        assert_uuid(cs[target], new_index, entry["uuids"][target])
        if cs[source].status(old_index).get("status") != STOPPED:
            raise Error("Index {} on the former primary still has active CCR".format(old_index))
        if cs[target].status(new_index).get("status") != STOPPED:
            raise Error("Index {} on the promoted primary still has active CCR".format(new_index))
        if not write_blocked(cs[source], old_index):
            raise Error("Index {} on the former primary is not write-blocked".format(old_index))
        assert_writable(cs[target], new_index)
        old_count, new_count = cs[source].count(old_index), cs[target].count(new_index)
        if old_count != new_count:
            raise Error("Document count mismatch after switchover on {}: former primary={} promoted primary={}".format(
                new_index, old_count, new_count))
        checked.append({"old_primary_index": old_index, "new_primary_index": new_index,
                        "documents": new_count})
    LOG.debug("post_switchover_validation_passed active=%s indices=%d", target, len(checked))
    return {"validation": "post_switchover", "ready_for_reprotect": True,
            "active": target, "indices": checked}


def final_checkpoint(client, index):
    data = check_shards(client.request("GET", f"/{index}/_stats?level=shards"))
    shards = data["indices"][index]["shards"]
    if not shards:
        raise Error(f"No primary shards on {index}")
    total = 0
    for copies in shards.values():
        primaries = [copy for copy in copies if copy["routing"]["primary"]]
        if len(primaries) != 1:
            raise Error(f"Missing/ambiguous primary shard on {index}")
        seq = primaries[0]["seq_no"]
        if seq["global_checkpoint"] != seq["max_seq_no"]:
            return None
        total += seq["max_seq_no"]
    return total


def wait_sync(cfg, cs, entries, source, target, message=None):
    deadline = time.monotonic() + cfg["wait_timeout"]
    last_feedback = None
    while True:
        ready = True
        ready_count = 0
        for entry in entries:
            status = cs[target].status(entry[target])
            matching_status(status, cfg, target, source, entry[source])
            details = status.get("syncing_details", {})
            leader = details.get("leader_checkpoint")
            follower = details.get("follower_checkpoint")
            synced = (status.get("status") == "SYNCING" and isinstance(leader, int)
                      and leader == follower)
            # Equal CCR checkpoints alone can both be stale. Compare them to the
            # current source primaries, even during a live reverse bootstrap.
            final = final_checkpoint(cs[source], entry[source])
            synced = synced and final is not None and follower == final
            LOG.debug("sync cluster=%s index=%s status=%s leader_checkpoint=%s follower_checkpoint=%s ready=%s",
                     target, entry[target], status.get("status"), leader, follower, synced)
            ready = ready and synced
            ready_count += int(synced)
        if ready:
            if message:
                feedback("%s: completed (%d/%d indices)." % (message, ready_count, len(entries)))
            return
        now = time.monotonic()
        # CCR polling is normally every two seconds.  Show liveness without
        # turning a long bootstrap into a stream of diagnostic messages.
        if message and (last_feedback is None or now - last_feedback >= 10):
            feedback("%s: %d/%d indices synchronized; waiting." %
                     (message, ready_count, len(entries)))
            last_feedback = now
        if time.monotonic() >= deadline:
            raise Error("Timeout waiting for CCR; state preserved for resume")
        time.sleep(cfg["poll_interval"])








def assert_uuid(client, index, expected):
    if not expected or client.uuid(index) != expected:
        raise Error(f"Unexpected UUID on {client.name}/{index}; external intervention detected")


def write_blocked(client, index):
    settings = client.request("GET", f"/{index}/_settings?flat_settings=true")[index]["settings"]
    return any(str(settings.get("index.blocks." + block, "false")).lower() == "true"
               for block in ("write", "read_only", "read_only_allow_delete", "metadata"))


def assert_writable(client, index):
    if write_blocked(client, index):
        raise Error(f"Index {index} still has a write block")


def read_transport_failure(exc):
    """Whether a read could not reach a cluster at all.

    Failover may proceed under the operator's fencing attestation only when the
    old primary is genuinely unreachable.  Authentication, authorization and
    HTTP failures remain actionable failures and must never be treated as an
    outage.
    """
    return str(exc).startswith("Read transport failure on cluster ")


def transition(cfg, cs, store, mode, resume=False,
               source_fenced=False, accept_data_loss=False, allow_stale_inventory=False,
               use_follower_annotations=False):
    state = store.read(required=False)
    annotation_fallback = None
    if resume:
        tr = state.get("transition") if state else None
        if not tr or tr["mode"] != mode:
            raise Error("No compatible transition is available to resume")
        if state["phase"] == "PROTECTION_RESTORED":
            return state
        if state["phase"] not in ("TRANSITION", "SERVICE_RESTORED"):
            raise Error("Another phase is in progress")
        if state["phase"] == "TRANSITION":
            required_annotation_manifest(state, tr["source"], tr["target"])
        if state["phase"] == "SERVICE_RESTORED" and mode != "failover":
            return state
    else:
        if state and state["phase"] not in IDLE:
            raise Error("A transition is pending: use --resume or complete reprotect")
        if mode == "failover" and not (source_fenced and accept_data_loss):
            raise Error("Failover requires --source-fenced and --accept-data-loss")
        if mode == "switchover":
            state = inventory(cfg, cs, store)
            precheck(cfg, cs)
        else:
            if not state or not state["indices"]:
                raise Error("Failover requires previously persisted inventory")
            age = freshness(state, cfg.get("max_inventory_age_seconds", 14400))
            target = other(state["active"])
            # The primary might be unavailable during failover. The saved
            # inventory proves its marker; the reachable follower is checked
            # live before it can be promoted.
            follower_annotation = validate_follower_annotation_scope(
                cs, state, state["active"], target)
            if age["status"] != "fresh" and use_follower_annotations:
                annotation_fallback = dict(follower_annotation, used=True)
                LOG.warning("failover_annotation_fallback cluster=%s indices=%d inventory_status=%s",
                            target, len(annotation_fallback["indices"]), age["status"])
                feedback("Stale inventory accepted after follower annotation scope confirmation.")
            if age["status"] != "fresh" and not (allow_stale_inventory or annotation_fallback):
                raise Error("Inventory is missing, stale, or future-dated; check status and use "
                            "--allow-stale-inventory or --use-follower-annotations to explicitly accept this risk")
            precheck(cfg, cs, names=(other(state["active"]),))
            rpo_result = rpo(cfg, state, {other(state["active"]): cs[other(state["active"])]})
            if cfg.get("enforce_rpo") and rpo_result["status"] != "within_limit":
                raise Error("RPO does not meet max_checkpoint_lag; use inventory/readiness and fix replication before failover")
        if not state["indices"]:
            raise Error("Inventory is empty")
        tr = {"id": uuid.uuid4().hex, "source": state["active"], "target": other(state["active"]),
              "mode": mode, "steps": [], "started_at": time.time(),
              "inventory_at": state.get("inventory_at"), "source_fenced": source_fenced,
              "accept_data_loss": accept_data_loss, "allow_stale_inventory": allow_stale_inventory,
              "annotation_fallback": annotation_fallback}
        state.update(phase="TRANSITION", transition=tr)
        store.write(state)
    source, target = tr["source"], tr["target"]
    entries = state["indices"]
    source_name = cfg["clusters"][source].get("display_name", source)
    target_name = cfg["clusters"][target].get("display_name", target)
    feedback("%s: %s to %s (%d indices)." %
             ("Resuming " + mode if resume else mode.capitalize(), source_name, target_name, len(entries)))

    labels = {
        "block_source": "Blocking writes on current primary",
        "fence_source": "Fencing writes on former primary when reachable",
        "final_sync": "Waiting for final CCR synchronization",
        "stop_followers": "Stopping replication on destination",
        "enable_target": "Enabling writes on new primary",
    }

    def step(name, action):
        if name not in tr["steps"]:
            feedback("%s..." % labels.get(name, name))
            tr["current_step"] = name
            store.write(state)
            LOG.debug("step_started transition=%s step=%s", tr["id"], name)
            completed = action()
            if completed is False:
                tr["current_step"] = None
                store.write(state)
                LOG.debug("step_deferred transition=%s step=%s", tr["id"], name)
                return False
            tr["steps"].append(name)
            store.write(state)
            LOG.debug("step_completed transition=%s step=%s", tr["id"], name)
            if name != "final_sync":
                feedback("%s: completed." % labels.get(name, name))
            return True

    def identity_check():
        for entry in entries:
            assert_uuid(cs[target], scoped(cfg, entry[target]), entry["uuids"][target])
            if mode == "switchover":
                assert_uuid(cs[source], scoped(cfg, entry[source]), entry["uuids"][source])

    identity_check()  # Repeat on every resume, not just the first execution.
    no_autofollow(cs[target])
    if mode == "failover":
        def fence_source_if_reachable():
            # A failover can proceed only with the explicit source_fenced
            # attestation when the old primary is unreachable.  When it is
            # reachable, FSDR itself must fence every protected index before
            # enabling writes on the promoted cluster.
            try:
                cs[source].request("GET", "/_cluster/health")
            except Error as exc:
                if not read_transport_failure(exc):
                    raise
                tr["source_fence"] = {"status": "externally_attested_unreachable",
                                      "observed_at": time.time(), "detail": "source transport is unavailable"}
                feedback("Former primary is unreachable; retaining the operator fencing attestation.")
                return False
            for entry in entries:
                assert_uuid(cs[source], scoped(cfg, entry[source]), entry["uuids"][source])
                result = cs[source].request("PUT", f"/{entry[source]}/_block/write")
                if not result.get("acknowledged"):
                    raise Error("Write block was not acknowledged by the former primary")
                if not write_blocked(cs[source], entry[source]):
                    raise Error("Write block did not become active on the former primary")
            tr["source_fence"] = {"status": "blocked_by_fsdr", "observed_at": time.time()}

        step("fence_source", fence_source_if_reachable)
    if mode == "switchover":
        no_autofollow(cs[source])

        def block():
            for entry in entries:
                result = cs[source].request("PUT", f"/{entry[source]}/_block/write")
                if not result.get("acknowledged"):
                    raise Error("Write block was not acknowledged by the former primary")
                if not write_blocked(cs[source], entry[source]):
                    raise Error("Write block did not become active on the former primary")
        step("block_source", block)
        for entry in entries:
            settings = cs[source].request("GET", f"/{entry[source]}/_settings?flat_settings=true")
            if str(settings[entry[source]]["settings"].get("index.blocks.write")).lower() != "true":
                raise Error("Former-primary write block was externally removed; resume refused")
        if "stop_followers" not in tr["steps"]:
            step("final_sync", lambda: wait_sync(
                cfg, cs, entries, source, target, "Final CCR synchronization"))

    def stop():
        for entry in entries:
            status = cs[target].status(entry[target])
            if status.get("status") != STOPPED:
                matching_status(status, cfg, target, source, entry[source])
                cs[target].request("POST", f"/_plugins/_replication/{entry[target]}/_stop", {})
            if cs[target].status(entry[target]).get("status") != STOPPED:
                raise Error("Follower did not confirm CCR stop")
    step("stop_followers", stop)

    def enable():
        for entry in entries:
            if cs[target].status(entry[target]).get("status") != STOPPED:
                raise Error("Destination is still replicating; it is unsafe to enable writes")
            cs[target].request("PUT", f"/{entry[target]}/_settings", {"index.blocks.write": False})
    step("enable_target", enable)

    for entry in entries:
        assert_writable(cs[target], entry[target])
    state.update(active=target, phase="SERVICE_RESTORED")
    tr["service_restored_at"] = time.time()
    tr["current_step"] = None
    store.write(state)
    LOG.debug("SERVICE_RESTORED active=%s mode=%s component=OpenSearch protection=pending", target, mode)
    feedback("Service restored on %s. Reverse protection remains pending." % target_name)
    return state


def reprotect(cfg, cs, store, parallelism=1):
    if isinstance(parallelism, bool) or not isinstance(parallelism, int) or parallelism < 1:
        raise Error("reprotect_parallelism must be a positive integer")
    state = store.read()
    tr = state.get("transition")
    if not tr or state["phase"] not in ("SERVICE_RESTORED", "REPROTECTING", "PROTECTION_RESTORED"):
        raise Error("Reprotect requires a transition with restored service")
    if state["phase"] == "PROTECTION_RESTORED":
        return state
    old, new = tr["source"], tr["target"]
    if state["active"] != new:
        raise Error("Current primary is incompatible with the transition")
    precheck(cfg, cs)
    # Both copies still exist. Verify the marker before the former primary can
    # be deleted and recreated as a follower.
    if state["phase"] == "SERVICE_RESTORED":
        validate_saved_annotation_scope(cs, state, new, old)
    else:
        required_annotation_manifest(state, old, new)
        expected_leaders = {entry[new] for entry in state["indices"]}
        marked_leaders = annotated_indices(cs[new])
        if marked_leaders != expected_leaders:
            raise Error("Current primary FSDR replication-marker scope differs from saved manifest: "
                        "missing={} extra={}".format(
                            sorted(expected_leaders - marked_leaders),
                            sorted(marked_leaders - expected_leaders)))
    state["phase"] = "REPROTECTING"
    progress = tr.setdefault("reprotect", {})
    recorded_parallelism = tr.setdefault("reprotect_parallelism", parallelism)
    if recorded_parallelism != parallelism:
        raise Error("Reprotect in progress uses parallelism {}; resume with the same value".format(
            recorded_parallelism))
    store.write(state)
    leader_name = cfg["clusters"][new].get("display_name", new)
    follower_name = cfg["clusters"][old].get("display_name", old)
    total = len(state["indices"])
    feedback("Reprotect: %s remains primary; rebuilding %s as follower (%d indices, parallelism %d)." %
             (leader_name, follower_name, total, parallelism))

    def prepare(position, entry):
        leader, follower = scoped(cfg, entry[new]), scoped(cfg, entry[old])
        feedback("Reprotect %d/%d: preparing %s." % (position, total, follower))
        tr.update(current_step="reprotect", current_index=follower)
        store.write(state)
        assert_uuid(cs[new], leader, entry["uuids"][new])
        if cs[new].status(leader).get("status") != STOPPED:
            raise Error("New primary follows another index; rebuild refused")
        item = progress.setdefault(follower, {"stage": "pending"})
        if item["stage"] == "pending":
            assert_uuid(cs[old], follower, entry["uuids"][old])
            result = cs[old].request("PUT", f"/{follower}/_block/write")
            if not result.get("acknowledged"):
                raise Error("Write block was not acknowledged on the former primary")
            # ``shards_acknowledged`` can be false while the setting itself was
            # applied.  The durable safety condition is reading that setting
            # back before authorizing deletion of the old copy.
            if not write_blocked(cs[old], follower):
                raise Error("Write block did not become active on the former primary")
            # Persist intent BEFORE DELETE; on retry absence is only accepted for this phase.
            item.update(stage="delete_intent", old_uuid=entry["uuids"][old])
            store.write(state)
        if item["stage"] == "delete_intent":
            if cs[old].exists(follower):
                assert_uuid(cs[old], follower, item["old_uuid"])
                LOG.warning("index_delete cluster=%s index=%s uuid=%s", old, follower, item["old_uuid"])
                cs[old].request("DELETE", f"/{follower}")
            item["stage"] = "start_intent"
            store.write(state)
        if item["stage"] == "start_intent":
            if cs[old].exists(follower):
                status = cs[old].status(follower)
                matching_status(status, cfg, old, new, leader)
                if status.get("status") == STOPPED:
                    raise Error("Index was externally recreated; it will not be removed")
            else:
                cs[old].start(follower, new, leader)
            item.update(stage="following", new_uuid=cs[old].wait_index(follower))
            entry["uuids"][old] = item["new_uuid"]
            store.write(state)
        assert_uuid(cs[old], follower, item["new_uuid"])
        return follower, item

    # Starting a follower initiates its remote recovery.  A batch is fully
    # synchronized before the next one starts, so this is a hard upper bound
    # on concurrent CCR bootstraps and their network/I/O/shard pressure.
    for first in range(0, total, parallelism):
        batch = []
        for position, entry in enumerate(state["indices"][first:first + parallelism], start=first + 1):
            follower, item = prepare(position, entry)
            if item["stage"] != "synced":
                batch.append((position, entry, follower, item))
        if not batch:
            continue
        positions = "{}/{}".format(batch[0][0], total)
        if len(batch) > 1:
            positions = "{}-{}/{}".format(batch[0][0], batch[-1][0], total)
        feedback("Reprotect batch %s: synchronizing %d index/indices." % (positions, len(batch)))
        wait_sync(cfg, cs, [item[1] for item in batch], new, old,
                  "Reprotect batch {}".format(positions))
        for _position, _entry, _follower, item in batch:
            item.update(stage="synced", synced_at=time.time())
        store.write(state)
    wait_sync(cfg, cs, state["indices"], new, old, "Validating restored protection")
    state["annotation"] = validate_saved_annotation_scope(cs, state, new, old)
    # Reprotect verifies and rebuilds CCR, but it is not an inventory
    # collection. Keep inventory_at as the timestamp of the last explicit
    # inventory so fresh-inventory gates and the dashboard age remain honest.
    state.update(phase="PROTECTION_RESTORED")
    tr["protection_restored_at"] = time.time()
    tr.update(current_step=None, current_index=None)
    store.write(state)
    LOG.debug("PROTECTION_RESTORED leader=%s follower=%s", new, old)
    feedback("Reverse protection restored: %s replicates %d indices from %s." %
             (follower_name, total, leader_name))
    return state
