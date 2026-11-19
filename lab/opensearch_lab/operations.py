"""CCR checks used only by setup_demo and monitor."""

import time

from .core import Error, LOG, index_name, other


IDLE = ("READY", "PROTECTION_RESTORED")
STOPPED = "REPLICATION NOT IN PROGRESS"


def check_shards(result):
    if result.get("_shards", {}).get("failed", 0):
        raise Error("Resposta parcial: uma ou mais shards falharam")
    return result


def matching_status(status, cfg, follower, leader, leader_index):
    aliases = cfg["clusters"][follower]["remote_aliases"]
    if status.get("leader_alias") != aliases.get(leader) or status.get("leader_index") != leader_index:
        raise Error("CCR de {} não corresponde ao leader esperado".format(follower))


def select_roles(cfg, client, name):
    roles = client.request("GET", "/_plugins/_security/authinfo").get("roles", [])
    for kind in ("leader", "follower"):
        field = kind + "_role"
        requested = cfg["clusters"][name].get(field)
        choices = [requested] if requested else [
            "cross_cluster_replication_{}_full_access".format(kind), "all_access"]
        selected = next((role for role in choices if role in roles), None)
        if not selected:
            raise Error("Usuário OpenSearch em {} não possui role CCR {} reconhecida".format(name, kind))
        cfg["clusters"][name][field] = selected


def precheck(cfg, cs, names=("a", "b")):
    infos = {}
    for name in names:
        info = cs[name].request("GET", "/")
        if info["version"]["number"] != cfg["expected_version"]:
            raise Error("Versão inesperada em {}".format(name))
        health = cs[name].request("GET", "/_cluster/health")
        if health.get("status") != "green" or health.get("timed_out"):
            raise Error("Cluster {} deve estar green".format(name))
        select_roles(cfg, cs[name], name)
        aliases = cs[name].request("GET", "/_remote/info")
        alias = cfg["clusters"][name]["remote_aliases"].get(other(name))
        if alias not in aliases:
            raise Error("Alias CCR {} ausente em {}".format(alias, name))
        infos[name] = info
    if infos["a"]["cluster_uuid"] == infos["b"]["cluster_uuid"]:
        raise Error("Os dois endpoints do lab apontam para o mesmo cluster")
    return infos


def final_checkpoint(client, index):
    data = check_shards(client.request("GET", "/{}/_stats?level=shards".format(index)))
    total = 0
    for copies in data["indices"][index]["shards"].values():
        primaries = [item for item in copies if item["routing"]["primary"]]
        if len(primaries) != 1:
            raise Error("Shards primárias ausentes ou ambíguas")
        seq = primaries[0]["seq_no"]
        if seq["global_checkpoint"] != seq["max_seq_no"]:
            return None
        total += seq["max_seq_no"]
    return total


def wait_sync(cfg, cs, entries, source, target):
    deadline = time.monotonic() + cfg["wait_timeout"]
    while True:
        complete = True
        for entry in entries:
            status = cs[target].status(entry[target])
            matching_status(status, cfg, target, source, entry[source])
            details = status.get("syncing_details", {})
            leader, follower = details.get("leader_checkpoint"), details.get("follower_checkpoint")
            synced = status.get("status") == "SYNCING" and isinstance(leader, int) and leader == follower
            synced = synced and final_checkpoint(cs[source], entry[source]) == follower
            complete = complete and synced
        if complete:
            return
        if time.monotonic() >= deadline:
            raise Error("Timeout esperando sincronização CCR")
        time.sleep(cfg["poll_interval"])


def assert_uuid(client, index, expected):
    if not expected or client.uuid(index) != expected:
        raise Error("UUID inesperado em {}/{}".format(client.name, index))


def write_blocked(client, index):
    settings = client.request("GET", "/{}/_settings?flat_settings=true".format(index))[index]["settings"]
    return any(str(settings.get("index.blocks." + block, "false")).lower() == "true"
               for block in ("write", "read_only", "read_only_allow_delete", "metadata"))


def assert_writable(client, index):
    if write_blocked(client, index):
        raise Error("Índice {} está bloqueado para escrita".format(index))
