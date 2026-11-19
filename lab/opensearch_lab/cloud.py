"""OCI discovery for the standalone lab, authenticated only with Instance Principal."""

import base64
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .core import Error, LOG, other, redact_secrets, write_json


def ocid(value, kind):
    parts = value.split(".") if isinstance(value, str) else []
    if (not re.fullmatch(r"[A-Za-z0-9.-]+", value or "") or len(parts) < 5 or
            parts[:2] != ["ocid1", kind] or not parts[3] or not parts[-1]):
        raise Error("OCID inválido para {}".format(kind))
    return value


def endpoint(fqdn):
    url = urlsplit(fqdn if isinstance(fqdn, str) and "://" in fqdn else "https://" + (fqdn or ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.path not in ("", "/"):
        raise Error("OCI retornou endpoint OpenSearch inválido")
    return "https://{}:{}".format(url.hostname, url.port or 9200)


def decode_secret(encoded):
    try:
        document = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, TypeError):
        raise Error("Secret CURRENT deve conter JSON UTF-8 BASE64") from None
    if not isinstance(document, list) or len(document) != 2:
        raise Error("Secret deve conter uma lista com exatamente dois clusters")
    result = {}
    for entry in document:
        if not isinstance(entry, dict):
            raise Error("Entrada inválida no Secret")
        cluster_id = ocid(entry.get("cluster_id"), "opensearchcluster")
        credential = entry.get("credencial")
        if (not isinstance(credential, dict) or not isinstance(credential.get("user"), str) or
                not credential["user"] or not isinstance(credential.get("pass"), str) or not credential["pass"]):
            raise Error("Cada entrada do Secret exige credencial.user e credencial.pass")
        if cluster_id in result or ":" in credential["user"]:
            raise Error("Secret contém cluster duplicado ou usuário inválido")
        result[cluster_id] = {"user": credential["user"], "pass": credential["pass"]}
    redact_secrets([item["pass"] for item in result.values()])
    return result


class Cloud:
    def __init__(self, timeout):
        try:
            import oci
            self.sdk, self.timeout = oci, timeout
            self.signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        except ImportError:
            raise Error("SDK OCI ausente; execute .venv/bin/python -m pip install -r requirements.txt") from None
        except Exception:
            raise Error("Instance Principal indisponível; confira a VM OCI, metadata e IAM") from None

    def region(self, resource_id):
        value = resource_id.split(".")[3]
        return self.sdk.regions.REGIONS_SHORT_NAMES.get(value, value)

    def secret(self, secret_id):
        secret_id = ocid(secret_id, "vaultsecret")
        client = self.sdk.secrets.SecretsClient({"region": self.region(secret_id)}, signer=self.signer, timeout=self.timeout)
        try:
            bundle = client.get_secret_bundle(secret_id, stage="CURRENT",
                retry_strategy=self.sdk.retry.NoneRetryStrategy()).data.secret_bundle_content
        except Exception as exc:
            raise Error("Não foi possível ler Secret CURRENT ({}); confira IAM/Vault/rede".format(type(exc).__name__)) from None
        if bundle.content_type != "BASE64":
            raise Error("Formato de Secret não suportado; esperado BASE64")
        return decode_secret(bundle.content)

    def cluster(self, cluster_id):
        client = self.sdk.opensearch.OpensearchClusterClient({"region": self.region(cluster_id)}, signer=self.signer,
                                                             timeout=self.timeout)
        try:
            data = client.get_opensearch_cluster(cluster_id,
                retry_strategy=self.sdk.retry.NoneRetryStrategy()).data
        except Exception as exc:
            raise Error("Não foi possível consultar cluster OCI {} ({})".format(cluster_id, type(exc).__name__)) from None
        if data.id != cluster_id or data.lifecycle_state != "ACTIVE":
            raise Error("Cluster OCI não está ACTIVE: {}".format(cluster_id))
        outbound = getattr(data, "outbound_cluster_config", None)
        aliases = {entry.display_name: entry.seed_cluster_id for entry in getattr(outbound, "outbound_clusters", [])} if outbound and outbound.is_enabled else {}
        if len(aliases) != len(getattr(outbound, "outbound_clusters", []) if outbound and outbound.is_enabled else []):
            raise Error("OCI retornou alias CCR duplicado")
        return {"id": cluster_id, "url": endpoint(data.opensearch_fqdn), "private_ip": data.opensearch_private_ip,
                "display_name": data.display_name, "version": data.software_version, "remote_ocids": aliases}


def discover_runtime(cfg, fsdr):
    """Refresh OCI metadata while preserving the FSDR pair and active direction."""
    cloud = Cloud(cfg["request_timeout"])
    credentials = cloud.secret(cfg["secret_id"])
    discovered = {cluster_id: cloud.cluster(cluster_id) for cluster_id in sorted(credentials)}
    ids = [fsdr["discovery"]["clusters"][name].get("id") for name in ("a", "b")]
    if set(ids) != set(discovered):
        raise Error("Os clusters do Secret diferem do inventário FSDR; execute inventory no FSDR")
    if len({discovered[item]["version"] for item in ids}) != 1:
        raise Error("Os clusters do lab precisam ter a mesma versão OpenSearch")
    clusters = {}
    assigned = {}
    for name, cluster_id in zip(("a", "b"), ids):
        current, peer = discovered[cluster_id], ids[1] if cluster_id == ids[0] else ids[0]
        aliases = sorted(alias for alias, target in current["remote_ocids"].items() if target == peer)
        if not aliases:
            raise Error("Conexão CCR outbound ausente em {} para o outro cluster".format(current["display_name"]))
        clusters[name] = dict(current, remote_aliases={other(name): aliases[0]},
                              leader_role=cfg.get("leader_role"),
                              follower_role=cfg.get("follower_role"),
                              ca_file=cfg.get("ca_file"))
        assigned[name] = credentials[cluster_id]
    cfg = dict(cfg, clusters=clusters, initial_primary=fsdr["state"]["active"],
               expected_version=clusters["a"]["version"])
    cache = {"schema": 1, "secret_id": cfg["secret_id"], "observed_at": time.time(),
             "fsdr_state_dir": fsdr["fsdr_state_dir"], "fsdr_active": fsdr["state"]["active"],
             "fsdr_inventory_at": fsdr["state"].get("inventory_at"),
             "fsdr_protected_index_count": len(fsdr["state"].get("indices", [])),
             "clusters": {name: {key: value for key, value in cluster.items() if key not in ("ca_file",)}
                          for name, cluster in clusters.items()}}
    write_json(Path(cfg["state_dir"]) / "discovery.json", cache)
    LOG.info("lab_discovery_completed clusters=%s", ",".join(cluster["display_name"] for cluster in clusters.values()))
    return cfg, assigned
