"""Seed laboratory indices and verify example document replication."""

import re
import time

from .core import Error, LOG, other
from .operations import STOPPED, check_shards, matching_status, precheck, wait_sync
from .topology import discover_primary, observe_writer


def scoped(cfg, value):
    from .core import index_name
    index_name(value)
    if not value.startswith(cfg["prefix"]):
        raise Error("Índice fora do prefixo do laboratório")
    return value


def next_index_number(cfg, entries):
    """Choose the suffix after the highest index already owned by the lab."""
    matcher = re.compile(r"^{}(\d+)$".format(re.escape(cfg["prefix"])))
    numbers = []
    for entry in entries:
        values = {entry.get(name) for name in ("a", "b")}
        if len(values) != 1:
            raise Error("Índices do laboratório possuem nomes diferentes entre os clusters")
        match = matcher.match(values.pop() or "")
        if not match:
            raise Error("Índice do laboratório não segue o padrão numérico: {}".format(entry))
        numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def compatible_expansion(previous, requested):
    """Existing documents/settings must not be silently redefined by expansion."""
    return all(previous.get(field) == requested.get(field)
               for field in ("docs", "shards", "replicas"))


def setup(cfg, cs, store, count, docs, batch, shards, replicas, progress=None):
    """Create a new dataset or expand a completed one to ``count`` total indices."""
    state = store.read(required=False)
    params = {"count": count, "docs": docs, "batch": batch, "shards": shards, "replicas": replicas}
    precheck(cfg, cs)
    if not state:
        source = discover_primary(cfg, cs)
        state = store.new(source)
        target = other(source)
        entries = [{source: scoped(cfg, f"{cfg['prefix']}{number:04d}"),
                    target: scoped(cfg, f"{cfg['prefix']}{number:04d}"),
                    "uuids": {}, "loaded": 0} for number in range(1, count + 1)]
        for entry in entries:
            if any(cs[n].exists(entry[n]) for n in (source, target)):
                raise Error(f"Índice já existe: {entry[source]}; setup não adota dados existentes")
        state.update(phase="SETUP", indices=entries, demo_uuid=store.demo_uuid(), setup_params=params,
                     setup_pending=[entry[source] for entry in entries])
        store.write(state)
    elif state["phase"] == "SETUP":
        if state.get("setup_params") != params:
            raise Error("Setup incompleto: repita os mesmos parâmetros para retomar com segurança")
        source, target = state["active"], other(state["active"])
    elif state["phase"] in ("READY", "PROTECTION_RESTORED"):
        current = len(state["indices"])
        if count <= current:
            raise Error("O laboratório já possui {} índices; informe um total maior para adicionar índices".format(current))
        if not compatible_expansion(state.get("setup_params", {}), params):
            raise Error("Para adicionar índices, docs-per-index, shards e replicas devem manter os valores do dataset existente")
        # Do not trust the persisted active side after a completed DR operation.
        state = observe_writer(cfg, cs, state)
        source, target = state["active"], other(state["active"])
        first = next_index_number(cfg, state["indices"])
        additions = []
        for number in range(first, first + count - current):
            index = scoped(cfg, "{}{:04d}".format(cfg["prefix"], number))
            additions.append({source: index, target: index, "uuids": {}, "loaded": 0})
        for entry in additions:
            if any(cs[name].exists(entry[name]) for name in (source, target)):
                raise Error("Índice já existe: {}; expansão não adota dados existentes".format(entry[source]))
        state["indices"].extend(additions)
        state.update(phase="SETUP", active=source, setup_params=params,
                     setup_pending=[entry[source] for entry in additions])
        store.write(state)
    else:
        raise Error("Estado do lab não permite setup nesta fase: {}".format(state["phase"]))

    source, target = state["active"], other(state["active"])
    pending = set(state.get("setup_pending", [entry[source] for entry in state["indices"]]))
    entries = [entry for entry in state["indices"] if entry[source] in pending]
    if not entries:
        raise Error("Setup sem índices pendentes; confira o estado do laboratório")
    if progress:
        progress("Setup: %d índice(s) pendente(s), %d documentos por índice." % (len(entries), docs))
    for position, entry in enumerate(entries, start=1):
        index = entry[source]
        if progress:
            progress("Setup %d/%d: %s." % (position, len(entries), index))
        if not cs[source].exists(index):
            if source in entry["uuids"]:
                raise Error("Índice do setup foi removido externamente")
            cs[source].request("PUT", f"/{index}", {
                "settings": {"number_of_shards": shards, "number_of_replicas": replicas},
                "mappings": {"_meta": {"fsdr": {"replicate": True,
                                                    "demo_uuid": state["demo_uuid"]}}, "properties": {
                    "created_at": {"type": "date"}, "kind": {"type": "keyword"},
                    "value": {"type": "long"}}}})
        mapping = cs[source].request("GET", f"/{index}/_mapping")
        meta = mapping[index]["mappings"].get("_meta", {})
        if meta.get("fsdr", {}).get("demo_uuid") != state["demo_uuid"]:
            raise Error("Índice não pertence a esta execução do setup")
        if meta.get("fsdr", {}).get("replicate") is not True:
            raise Error("Índice do laboratório sem a anotação obrigatória fsdr.replicate=true")
        current_uuid = cs[source].uuid(index)
        if entry["uuids"].get(source, current_uuid) != current_uuid:
            raise Error("UUID do índice do setup mudou")
        entry["uuids"][source] = current_uuid
        store.write(state)
        while entry["loaded"] < docs:
            end = min(entry["loaded"] + batch, docs)
            operations = [(index, f"seed-{n:012d}", {
                "created_at": int(time.time() * 1000), "kind": "seed", "value": n})
                for n in range(entry["loaded"], end)]
            cs[source].bulk(operations)
            entry["loaded"] = end
            store.write(state)
        check_shards(cs[source].request("POST", f"/{index}/_refresh"))
        if cs[source].count(index) != docs:
            raise Error(f"Contagem inesperada após setup: {index}")
        if cs[target].exists(entry[target]):
            status = cs[target].status(entry[target])
            matching_status(status, cfg, target, source, index)
            if status.get("status") == STOPPED:
                raise Error("Índice de destino existente não é follower")
        else:
            cs[target].start(entry[target], source, index)
        entry["uuids"][target] = cs[target].wait_index(entry[target])
        store.write(state)
    if progress:
        progress("Aguardando a sincronização inicial do CCR.")
    wait_sync(cfg, cs, entries, source, target)
    for entry in entries:
        check_shards(cs[target].request("POST", f"/{entry[target]}/_refresh"))
        if cs[target].count(entry[target]) != docs:
            raise Error("Contagem incorreta no follower após setup")
    state.pop("setup_pending", None)
    state.update(phase="PROTECTION_RESTORED", inventory_at=time.time())
    store.write(state)
    LOG.info("setup_completed total_indices=%d added_indices=%d docs_per_index=%d", len(state["indices"]),
             len(entries), docs)
    if progress:
        progress("Setup concluído: %d índices protegidos." % len(state["indices"]))


def verify_replication(cfg, cs, entry, source, target, probe_id):
    """Check create/delete visibility using real-time GET, independent of refresh."""
    source_path = f"/{entry[source]}/_doc/{probe_id}"
    target_path = f"/{entry[target]}/_doc/{probe_id}"
    cs[source].request("PUT", source_path, {"kind": "replication-probe",
                                           "created_at": int(time.time() * 1000)})

    def wait_present(present):
        deadline = time.monotonic() + cfg["wait_timeout"]
        while True:
            response = cs[target].request("GET", target_path, allow=(404,))
            found = response.get("found") is True
            absent = response.get("_http_status") == 404 or response.get("found") is False
            if (found if present else absent):
                return
            if time.monotonic() >= deadline:
                raise Error("Timeout verificando criação/exclusão replicada; probe pode permanecer no primary")
            time.sleep(cfg["poll_interval"])

    wait_present(True)
    cs[source].request("DELETE", source_path, allow=(404,))
    wait_present(False)
    LOG.info("replication_probe_completed leader=%s follower=%s index=%s id=%s",
             source, target, entry[source], probe_id)


