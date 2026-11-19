"""Safe removal of a dataset created by the standalone demonstration setup."""

import time

from .core import Error, LOG, other
from .operations import STOPPED, matching_status, precheck, write_blocked


FSDR_IDLE = ("READY", "PROTECTION_RESTORED")
LAB_READY = "PROTECTION_RESTORED"


def _owned(cfg, client, index, demo_uuid):
    """Refuse deletion unless name and persistent lab markers both match."""
    if not index.startswith(cfg["prefix"]):
        raise Error("Índice fora do prefixo do laboratório: {}/{}".format(client.name, index))
    if not client.exists(index):
        return False
    mapping = client.request("GET", "/{}/_mapping".format(index))
    meta = mapping[index]["mappings"].get("_meta", {})
    fsdr = meta.get("fsdr", {})
    if fsdr.get("demo_uuid") != demo_uuid or fsdr.get("replicate") is not True:
        raise Error("Índice {}/{} não pertence ao dataset do laboratório; limpeza recusada".format(
            client.name, index))
    return True


def _preflight(cfg, cs, state):
    if cfg["fsdr_state"].get("phase") not in FSDR_IDLE:
        raise Error("FSDR está em {}; a limpeza do lab é bloqueada durante DR".format(
            cfg["fsdr_state"].get("phase")))
    if state.get("phase") != LAB_READY or not state.get("indices") or not state.get("demo_uuid"):
        raise Error("Limpeza requer um setup do laboratório concluído e protegido")
    precheck(cfg, cs)

    plan = []
    for entry in state["indices"]:
        statuses = {}
        for name in ("a", "b"):
            index = entry[name]
            if not _owned(cfg, cs[name], index, state["demo_uuid"]):
                raise Error("Índice {}/{} ausente antes da limpeza; intervenção externa detectada".format(
                    name, index))
            statuses[name] = cs[name].status(index)
        followers = [name for name in ("a", "b") if statuses[name].get("status") != STOPPED]
        if len(followers) != 1:
            raise Error("CCR do índice {} é ambíguo; limpeza recusada".format(entry["a"]))
        follower = followers[0]
        leader = other(follower)
        matching_status(statuses[follower], cfg, follower, leader, entry[leader])
        plan.append({"leader_cluster": leader, "leader_index": entry[leader],
                     "follower_cluster": follower, "follower_index": entry[follower]})
    return plan


def _delete(cfg, client, index, demo_uuid):
    if not _owned(cfg, client, index, demo_uuid):
        return False
    client.request("DELETE", "/{}".format(index))
    if client.exists(index):
        raise Error("Índice {}/{} continua existente após DELETE".format(client.name, index))
    return True


def cleanup(cfg, cs, store, execute=False, progress=None):
    """Remove only the exact indices recorded and owned by this lab state."""
    state = store.read()
    if state.get("demo_uuid") != store.demo_uuid():
        raise Error("Estado do dataset não corresponde à identidade persistente deste laboratório")
    if not execute:
        plan = _preflight(cfg, cs, state)
        return {"dry_run": True, "indices": plan,
                "note": "No cluster changes were made. Repeat with --cleanup --execute to delete this lab dataset."}

    if state.get("phase") == "CLEANUP":
        progress_state = state.get("cleanup") or {}
    else:
        plan = _preflight(cfg, cs, state)
        progress_state = {item["leader_index"]: dict(item, stage="pending") for item in plan}
        state.update(phase="CLEANUP", cleanup=progress_state)
        store.write(state)

    total = len(state["indices"])
    for position, entry in enumerate(state["indices"], start=1):
        # During a resume, the item stores the original roles. Ownership is
        # established by the persistent mapping markers, not UUIDs: reprotect
        # intentionally recreates followers with new UUIDs.
        item = progress_state.get(entry["a"]) or progress_state.get(entry["b"])
        if not item:
            raise Error("Progresso de limpeza ausente para o índice {}".format(entry["a"]))
        leader, follower = item["leader_cluster"], item["follower_cluster"]
        leader_index, follower_index = item["leader_index"], item["follower_index"]
        if progress:
            progress("Limpeza {}/{}: {}.".format(position, total, leader_index))

        if item["stage"] == "pending":
            if not _owned(cfg, cs[leader], leader_index, state["demo_uuid"]):
                raise Error("Leader ausente antes da limpeza: {}".format(leader_index))
            if not _owned(cfg, cs[follower], follower_index, state["demo_uuid"]):
                raise Error("Follower ausente antes da limpeza: {}".format(follower_index))
            status = cs[follower].status(follower_index)
            matching_status(status, cfg, follower, leader, leader_index)
            result = cs[leader].request("PUT", "/{}/_block/write".format(leader_index))
            if not result.get("acknowledged") or not write_blocked(cs[leader], leader_index):
                raise Error("Não foi possível bloquear escrita em {}; limpeza recusada".format(leader_index))
            item["stage"] = "stop_follower"
            store.write(state)

        if item["stage"] == "stop_follower":
            if _owned(cfg, cs[follower], follower_index, state["demo_uuid"]):
                status = cs[follower].status(follower_index)
                if status.get("status") != STOPPED:
                    matching_status(status, cfg, follower, leader, leader_index)
                    cs[follower].request("POST", "/_plugins/_replication/{}/_stop".format(follower_index), {})
                if cs[follower].status(follower_index).get("status") != STOPPED:
                    raise Error("Follower não confirmou parada do CCR: {}".format(follower_index))
            item["stage"] = "delete_follower"
            store.write(state)

        if item["stage"] == "delete_follower":
            _delete(cfg, cs[follower], follower_index, state["demo_uuid"])
            item["stage"] = "delete_leader"
            store.write(state)

        if item["stage"] == "delete_leader":
            _delete(cfg, cs[leader], leader_index, state["demo_uuid"])
            item["stage"] = "deleted"
            store.write(state)

    store.remove()
    LOG.info("lab_cleanup_completed indices=%d", total)
    if progress:
        progress("Limpeza concluída: %d pares de índices removidos. Execute o inventário FSDR antes de novo DR." % total)
    return {"deleted_indices": total * 2, "deleted_pairs": total,
            "next_step": "Run fsdr.py inventory before the next FSDR operation."}
