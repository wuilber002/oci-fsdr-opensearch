"""Minimal HTTPS client. Only reads are automatically retried."""

import base64
import contextlib
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .core import Error, LOG, index_name


DNS_OVERRIDE_LOCK = threading.RLock()


class APIError(Error):
    def __init__(self, status, method, path, error_type="unknown"):
        self.status = status
        super().__init__(f"HTTP {status}: {method} {path}; error_type={error_type}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Basic Auth to a redirected endpoint.


def transport_diagnosis(exc):
    """Classify connection failures without logging URLs or authentication data."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "dns", "endpoint name could not be resolved; check private DNS and the inter-region route"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "tls_certificate", "TLS certificate validation failed; check the system CA or ca_file"
    if isinstance(reason, ssl.SSLError):
        return "tls", "TLS negotiation failed; check certificate, CA, and HTTPS connectivity"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused", "connection was refused; check endpoint, port 9200, and network rules"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return "timeout", "connection timed out; check routes, NSGs/security lists, and port 9200 firewall"
    return type(reason).__name__, "check DNS, inter-region route, NSGs/security lists, and port 9200 firewall"


class Client:
    def __init__(self, cfg, name, credentials=None):
        self.name = name
        self.cfg = cfg
        self.cluster = cfg["clusters"][name]
        self.endpoint_host = urlsplit(self.cluster["url"]).hostname
        self.connect_ip = self.cluster.get("private_ip") if cfg.get("use_private_ip") else None
        if self.connect_ip:
            try:
                ipaddress.ip_address(self.connect_ip)
            except ValueError:
                raise Error(f"OCI did not return a valid private IP for cluster {name}") from None
        try:
            username = credentials["user"] if credentials is not None else os.environ[self.cluster["username_env"]]
            password = credentials["pass"] if credentials is not None else os.environ[self.cluster["password_env"]]
        except KeyError as exc:
            raise Error(f"Missing credential environment variable: {exc.args[0]}") from None
        if not username or not password or ":" in username:
            raise Error(f"Invalid credentials on cluster {name}")
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        context = ssl.create_default_context(cafile=self.cluster.get("ca_file"))
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    @contextlib.contextmanager
    def private_ip_resolution(self):
        """Route only this endpoint to its OCI private IP while retaining TLS SNI."""
        if not self.connect_ip:
            yield
            return
        with DNS_OVERRIDE_LOCK:
            original = socket.getaddrinfo

            def resolve(host, *args, **kwargs):
                value = host.decode() if isinstance(host, bytes) else host
                if isinstance(value, str) and value.rstrip(".").lower() == self.endpoint_host.lower():
                    return original(self.connect_ip, *args, **kwargs)
                return original(host, *args, **kwargs)

            socket.getaddrinfo = resolve
            try:
                yield
            finally:
                socket.getaddrinfo = original

    def request(self, method, path, body=None, allow=()):
        raw = body.encode() if isinstance(body, str) else (
            json.dumps(body).encode() if body is not None else None)
        headers = {"Authorization": self.authorization, "Accept": "application/json",
                   "Content-Type": "application/x-ndjson" if path == "/_bulk" else "application/json"}
        attempts = self.cfg["read_retries"] + 1 if method in ("GET", "HEAD") else 1
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                req = Request(self.cluster["url"] + path, data=raw, headers=headers, method=method)
                with self.private_ip_resolution(), self.opener.open(req, timeout=self.cfg["request_timeout"]) as response:
                    data = response.read()
                    result = json.loads(data) if data else {}
                LOG.info("http cluster=%s method=%s path=%s duration=%.3f outcome=ok",
                         self.name, method, path, time.monotonic() - started)
                if isinstance(result, dict) and (result.get("acknowledged") is False or result.get("errors") is True):
                    # Bulk item errors are inspected in bulk(), not discarded here.
                    if path != "/_bulk":
                        raise Error(f"Operation not acknowledged: {method} {path}")
                return result
            except HTTPError as exc:
                status = exc.code
                try:
                    error = json.loads(exc.read()).get("error", {})
                    kind = error.get("type", "unknown") if isinstance(error, dict) else "unknown"
                except (ValueError, AttributeError):
                    kind = "non_json_error"
                finally:
                    exc.close()
                LOG.warning("http cluster=%s method=%s path=%s status=%s type=%s attempt=%d",
                            self.name, method, path, status, kind, attempt + 1)
                if status in allow:
                    return {"_http_status": status}
                if status not in (429, 502, 503, 504) or attempt == attempts - 1:
                    raise APIError(status, method, path, kind) from None
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                kind, diagnosis = transport_diagnosis(exc)
                LOG.error("transport_error cluster=%s endpoint=%s connect_ip=%s method=%s path=%s type=%s diagnosis=%s attempt=%d",
                          self.name, self.cluster["url"], self.connect_ip or "dns", method, path, kind, diagnosis, attempt + 1)
                if attempt == attempts - 1:
                    if method in ("GET", "HEAD"):
                        raise Error(f"Read transport failure on cluster {self.name} "
                                    f"(endpoint {self.cluster['url']}): {method} {path}; "
                                    f"no change was sent. {diagnosis}") from None
                    raise Error(f"Transport failure on cluster {self.name} "
                                f"(endpoint {self.cluster['url']}): {method} {path}; "
                                f"a mutation may have been applied; check state before retrying. {diagnosis}") from None
            time.sleep(min(2 ** attempt, 8))

    def exists(self, index):
        return self.request("HEAD", "/" + index_name(index), allow=(404,)).get("_http_status") != 404

    def uuid(self, index):
        data = self.request("GET", f"/{index_name(index)}/_settings?flat_settings=true")
        return data[index]["settings"]["index.uuid"]

    def status(self, index):
        # A missing index is handled separately; a 404 here is never assumed to mean stopped.
        return self.request("GET", f"/_plugins/_replication/{index_name(index)}/_status")

    def wait_index(self, index):
        deadline = time.monotonic() + self.cfg["wait_timeout"]
        while not self.exists(index):
            if time.monotonic() >= deadline:
                raise Error(f"Timeout waiting for follower {index} creation")
            time.sleep(self.cfg["poll_interval"])
        return self.uuid(index)

    def start(self, index, leader, leader_index):
        body = {"leader_alias": self.cluster["remote_aliases"][leader],
                "leader_index": index_name(leader_index),
                "use_roles": {"leader_cluster_role": self.cfg["clusters"][leader]["leader_role"],
                              "follower_cluster_role": self.cluster["follower_role"]}}
        return self.request("PUT", f"/_plugins/_replication/{index_name(index)}/_start", body)

    def bulk(self, operations):
        lines = []
        for index, doc_id, document in operations:
            lines.append(json.dumps({"index": {"_index": index_name(index), "_id": doc_id}}))
            lines.append(json.dumps(document))
        result = self.request("POST", "/_bulk", "\n".join(lines) + "\n")
        failures = []
        items = result.get("items", [])
        if len(items) != len(operations):
            raise Error("Bulk retornou quantidade inesperada de resultados")
        for item in items:
            op = item["index"]
            status = op["status"]
            LOG.log(20 if 200 <= status < 300 else 40,
                    "bulk_item cluster=%s index=%s id=%s status=%s error_type=%s",
                    self.name, op.get("_index"), op.get("_id"), status,
                    op.get("error", {}).get("type", "none"))
            if not 200 <= status < 300:
                failures.append(status)
        if failures or result.get("errors"):
            raise Error(f"Bulk failed for {len(failures)} documents; see the log")

    def count(self, index):
        data = self.request("GET", f"/{index_name(index)}/_count")
        if data.get("_shards", {}).get("failed", 0):
            raise Error(f"Partial count on index {index}")
        return data["count"]

    def document_counts(self, scoped=False):
        """Read document totals for all visible indices in one request."""
        pattern = quote(self.cfg["prefix"], safe="-._") + "*" if scoped else "*"
        rows = self.request(
            "GET", f"/_cat/indices/{pattern}?format=json&h=index,docs.count&expand_wildcards=open,closed")
        counts = {}
        for row in rows:
            index = row.get("index")
            if not index or index.startswith("."):
                continue
            try:
                counts[index] = int(row.get("docs.count", 0))
            except (TypeError, ValueError):
                raise Error(f"Invalid document count returned for index {index}") from None
        return counts

    def indices(self, scoped=True):
        pattern = quote(self.cfg["prefix"], safe="-._") + "*" if scoped else "*"
        rows = self.request("GET", f"/_cat/indices/{pattern}?format=json&h=index,status&expand_wildcards=open,closed")
        return sorted(row["index"] for row in rows if not row["index"].startswith("."))


def clients(cfg, credentials=None):
    return {name: Client(cfg, name, credentials[name] if credentials is not None else None)
            for name in ("a", "b")}
