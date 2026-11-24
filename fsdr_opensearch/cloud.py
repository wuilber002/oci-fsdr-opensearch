"""Read-only OCI discovery and Secret retrieval using Instance Principal.

No password, hash, Secret payload, or full OCI model is written to disk.
"""

import base64
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .core import Error, LOG, other, redact_secrets, write_json


def ocid(value, resource_type):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", value)
            or len(value.split(".")) < 5 or value.split(".")[:2] != ["ocid1", resource_type]
            or not value.split(".")[3] or not value.split(".")[-1]):
        raise Error(f"Invalid OCID for {resource_type}")
    return value


def decode_credentials(content):
    try:
        document = json.loads(base64.b64decode(content, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, TypeError):
        raise Error("O Secret CURRENT deve conter JSON UTF-8 codificado em Base64 pelo Vault") from None
    # Register passwords before validation so later error paths cannot expose them.
    if isinstance(document, list):
        redact_secrets([entry.get("credencial", {}).get("pass") for entry in document
                        if isinstance(entry, dict) and isinstance(entry.get("credencial"), dict)])
    if not isinstance(document, list) or len(document) != 2:
        raise Error("Secret deve conter uma lista com exatamente dois clusters")
    result = {}
    for entry in document:
        if not isinstance(entry, dict):
            raise Error("Invalid Secret entry")
        cluster_id = ocid(entry.get("cluster_id"), "opensearchcluster")
        credentials = entry.get("credencial")
        if not isinstance(credentials, dict) or any(
                not isinstance(credentials.get(key), str) or not credentials[key] for key in ("user", "pass")):
            raise Error("Each entry requires non-empty credencial.user and credencial.pass")
        if ":" in credentials["user"]:
            raise Error("OpenSearch username cannot contain a colon")
        if cluster_id in result:
            raise Error("Secret contains duplicate cluster_id")
        result[cluster_id] = {key: credentials[key] for key in ("user", "pass")}
    return result


def pair_identity(ids):
    return hashlib.sha256(json.dumps(["dr-v2", *sorted(ids)]).encode()).hexdigest()


def default_state_dir(secret_id):
    key = hashlib.sha256(secret_id.encode()).hexdigest()[:24]
    return Path(__file__).resolve().parent.parent / "var" / "dr" / key


def endpoint(fqdn):
    if not isinstance(fqdn, str) or not fqdn:
        raise Error("OCI did not return opensearch_fqdn")
    url = urlsplit(fqdn if "://" in fqdn else "https://" + fqdn)
    try:
        port = url.port or 9200
    except ValueError:
        raise Error("Invalid OCI endpoint") from None
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment
            or not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname)):
        raise Error("Endpoint OCI deve ser um hostname HTTPS sem credenciais ou caminho")
    return f"https://{url.hostname}:{port}"


class CloudError(Error):
    def __init__(self, operation, status, transient, code=None, request_id=None, message=None):
        self.status = status
        self.transient = transient
        self.code = code
        self.request_id = request_id
        self.service_message = message
        detail = f"status={status}"
        if code:
            detail += f", code={code}"
        if request_id:
            detail += f", request_id={request_id}"
        if message:
            detail += f", message={message}"
        super().__init__(f"OCI {operation} failed ({detail}); check IAM/network/region")


class Cloud:
    def __init__(self, timeout=20):
        try:
            import oci
        except ImportError:
            raise Error("Instale o SDK OCI: python3 -m pip install -r requirements.txt") from None
        self.sdk, self.timeout = oci, timeout
        try:
            self.signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        except Exception as exc:
            LOG.error("instance_principal_failed type=%s", type(exc).__name__)
            raise Error("Could not obtain Instance Principal; check OCI VM, metadata, and IAM") from None

    def region(self, resource_id):
        value = resource_id.split(".")[3]
        aliases = self.sdk.regions.REGIONS_SHORT_NAMES
        if value in aliases:
            return aliases[value]
        if "-" in value:
            return value
        raise Error("OCID abbreviated region is not recognized by the SDK; update the oci package")

    def _read(self, operation, callback):
        started = time.monotonic()
        try:
            response = callback()
        except Exception as exc:
            status = getattr(exc, "status", None)
            code = getattr(exc, "code", None)
            request_id = getattr(exc, "opc_request_id", None)
            message = getattr(exc, "message", None)
            if isinstance(message, str):
                message = " ".join(message.split())[:500]
            else:
                message = None
            transient = status in (429, 500, 502, 503, 504) or type(exc).__name__ in (
                "ConnectTimeout", "ReadTimeout", "ConnectionError", "RequestException")
            LOG.error("oci_read operation=%s status=%s code=%s request_id=%s message=%s type=%s duration=%.3f", operation,
                      status, code, request_id, message, type(exc).__name__, time.monotonic() - started)
            raise CloudError(operation, status, transient, code, request_id, message) from None
        LOG.debug("oci_read operation=%s duration=%.3f request_id=%s", operation,
                 time.monotonic() - started, response.headers.get("opc-request-id"))
        return response.data

    def credentials(self, secret_id):
        secret_id = ocid(secret_id, "vaultsecret")
        client = self.sdk.secrets.SecretsClient(
            {"region": self.region(secret_id)}, signer=self.signer, timeout=self.timeout)
        data = self._read("GetSecretBundle", lambda: client.get_secret_bundle(
            secret_id, stage="CURRENT", retry_strategy=self.sdk.retry.NoneRetryStrategy()))
        bundle = data.secret_bundle_content
        if bundle.content_type != "BASE64":
            raise Error("Unsupported Secret format; expected BASE64")
        return decode_credentials(bundle.content)

    def cluster(self, cluster_id):
        region = self.region(cluster_id)
        client = self.sdk.opensearch.OpensearchClusterClient(
            {"region": region}, signer=self.signer, timeout=self.timeout)
        data = self._read("GetOpensearchCluster", lambda: client.get_opensearch_cluster(
            cluster_id, retry_strategy=self.sdk.retry.NoneRetryStrategy()))
        if data.id != cluster_id or data.lifecycle_state != "ACTIVE":
            raise Error("OCI cluster is not ACTIVE or returned an unexpected OCID")
        connections = getattr(data, "outbound_cluster_config", None)
        aliases = {}
        if connections and connections.is_enabled:
            for connection in connections.outbound_clusters:
                if connection.display_name in aliases:
                    raise Error("OCI retornou alias remoto duplicado")
                aliases[connection.display_name] = connection.seed_cluster_id
        return {"id": cluster_id, "url": endpoint(data.opensearch_fqdn),
                "display_name": data.display_name, "region": region,
                "private_ip": data.opensearch_private_ip, "version": data.software_version,
                "remote_ocids": aliases, "metadata_observed_at": time.time()}

    def object_storage(self, region):
        """Return an Object Storage client and the tenancy namespace.

        The caller supplies a region derived from the configured Secret OCID,
        so the ephemeral runner does not need a second region setting.
        """
        client = self.sdk.object_storage.ObjectStorageClient(
            {"region": region}, signer=self.signer, timeout=self.timeout)
        namespace = self._read("GetNamespace", lambda: client.get_namespace(
            retry_strategy=self.sdk.retry.NoneRetryStrategy()))
        if not isinstance(namespace, str) or not namespace:
            raise Error("OCI Object Storage returned an invalid namespace")
        return client, namespace


def read_cache(directory, secret_id):
    path = Path(directory) / "discovery.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or data.get("secret_id") != secret_id:
        raise Error("Discovery cache is incompatible with the Secret")
    clusters = data.get("clusters")
    if not isinstance(clusters, dict) or set(clusters) != {"a", "b"}:
        raise Error("Discovery cache is incomplete; run inventory again")
    return data


def discover(cloud, secret_id, credentials, directory, cached_allowed=()):
    """Fall back only for a known failed source and only on transient errors."""
    previous = read_cache(directory, secret_id)
    ids = sorted(credentials)
    if previous and sorted(c["id"] for c in previous["clusters"].values()) != ids:
        raise Error("Cluster pair in Secret changed; use a new state directory")
    previous_by_id = {value["id"]: value for value in previous["clusters"].values()} if previous else {}
    selected = {}
    for name, cluster_id in zip(("a", "b"), ids):
        try:
            selected[name] = cloud.cluster(cluster_id)
        except CloudError as exc:
            if cluster_id not in cached_allowed or not exc.transient or cluster_id not in previous_by_id:
                raise
            selected[name] = dict(previous_by_id[cluster_id])
            LOG.warning("oci_source_metadata_from_cache cluster_id=%s cache_time=%s",
                        cluster_id, selected[name].get("metadata_observed_at", previous["discovered_at"]))
    result = {"schema": 1, "secret_id": secret_id, "clusters": selected, "discovered_at": time.time()}
    return result


def runtime_config(discovery, directory, log_dir, ca_file=None, leader_role=None, follower_role=None):
    clusters = {}
    for name, source in discovery["clusters"].items():
        peer = discovery["clusters"][other(name)]["id"]
        aliases = sorted(alias for alias, remote in source["remote_ocids"].items() if remote == peer)
        clusters[name] = dict(source, ca_file=ca_file,
                              remote_aliases={other(name): aliases[0]} if aliases else {},
                              leader_role=leader_role, follower_role=follower_role)
    versions = {value["version"] for value in clusters.values()}
    if len(versions) != 1:
        raise Error("CCR reversal requires the same version on both clusters")
    if len({value["url"] for value in clusters.values()}) != 2:
        raise Error("OCI endpoints for both clusters are identical")
    return {"scope": "pair", "clusters": clusters, "state_schema": 2,
            "identity": pair_identity(c["id"] for c in clusters.values()),
            "expected_version": next(iter(versions)), "state_dir": str(directory),
            "log_dir": str(log_dir), "request_timeout": 20, "read_retries": 2,
            "wait_timeout": 1800, "poll_interval": 2}


def save_cache(directory, data):
    write_json(Path(directory) / "discovery.json", data)
