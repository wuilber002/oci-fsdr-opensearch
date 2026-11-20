"""Observe the live demo topology without reading or editing FSDR state."""

import copy

from .core import Error, other
from .operations import STOPPED, assert_writable, matching_status, write_blocked


def fsdr_marker(mapping, index, cluster):
    """Read a mapping marker with an actionable error for malformed metadata."""
    try:
        mappings = mapping[index]["mappings"]
    except (KeyError, TypeError):
        raise Error("Resposta de mapping inválida em {}/{}".format(cluster, index)) from None
    if not isinstance(mappings, dict):
        raise Error("Mapping inválido em {}/{}".format(cluster, index))
    meta = mappings.get("_meta", {})
    if not isinstance(meta, dict):
        raise Error("_meta inválido em {}/{}; esperado objeto JSON".format(cluster, index))
    marker = meta.get("fsdr", {})
    if not isinstance(marker, dict):
        raise Error("_meta.fsdr inválido em {}/{}; esperado objeto JSON".format(cluster, index))
    return marker


def discover_primary(cfg, cs):
    """Infer the current writer from live marked CCR followers.

    The first demo has no own indices yet.  Existing indices that declare the
    mandatory FSDR replication marker establish the current direction.  If no
    such follower exists, ``a`` is a deterministic initial writer.
    """
    candidates = set()
    for follower in ("a", "b"):
        leader = other(follower)
        for index in cs[follower].indices(scoped=False):
            mapping = cs[follower].request("GET", "/{}/_mapping".format(index))
            marker = fsdr_marker(mapping, index, follower)
            if marker.get("replicate") is not True:
                continue
            status = cs[follower].status(index)
            if status.get("status") == STOPPED:
                continue
            if status.get("leader_alias") != cfg["clusters"][follower]["remote_aliases"][leader]:
                continue
            candidates.add(leader)
    if len(candidates) > 1:
        raise Error("CCR marcado indica mais de um primary; setup do laboratório recusado")
    return candidates.pop() if candidates else "a"


def observe_writer(cfg, cs, state):
    if not state.get("demo_uuid") or not state["indices"] or state["phase"] == "SETUP":
        raise Error("Dataset do laboratório ainda não está pronto")
    result = copy.deepcopy(state)
    selected = set()
    for entry in result["indices"]:
        statuses = {name: cs[name].status(entry[name]) if cs[name].exists(entry[name])
                    else {"status": "MISSING"} for name in ("a", "b")}
        directions = set()
        for name, status in statuses.items():
            if status.get("status") not in (STOPPED, "MISSING"):
                peer = other(name)
                matching_status(status, cfg, name, peer, entry[peer])
                directions.add(peer)
        if not directions:
            # After promotion but before reverse CCR: exactly one side must be writable.
            for name, status in statuses.items():
                if status.get("status") == STOPPED:
                    if not write_blocked(cs[name], entry[name]):
                        directions.add(name)
        if len(directions) != 1:
            raise Error("Direção do laboratório ambígua durante a transição; carga pausada")
        active = directions.pop()
        assert_writable(cs[active], entry[active])
        mapping = cs[active].request("GET", f"/{entry[active]}/_mapping")
        marker = fsdr_marker(mapping, entry[active], active)
        if (marker.get("demo_uuid") != state["demo_uuid"]
                or marker.get("replicate") is not True):
            raise Error("Índice ativo não pertence ao dataset do laboratório")
        entry["uuids"][active] = cs[active].uuid(entry[active])
        selected.add(active)
    if len(selected) != 1:
        raise Error("Troca parcial: os índices do laboratório não têm um writer único")
    result["active"] = selected.pop()
    return result
