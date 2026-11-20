"""Small HTTPS client used exclusively by the standalone lab package."""

import base64
import contextlib
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .core import Error, LOG, index_name


DNS_LOCK = threading.RLock()


class APIError(Error):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def diagnosis(exc):
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "não foi possível resolver o FQDN; habilite use_private_ip ou corrija DNS privado"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "validação TLS falhou; confira a CA"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return "conexão expirou; confira rota e regras de rede"
    return "confira DNS, rota, porta 9200, NSG/security lists e TLS"


class Client:
    def __init__(self, cfg, name, credential):
        self.cfg, self.name, self.cluster = cfg, name, cfg["clusters"][name]
        self.endpoint_host = urlsplit(self.cluster["url"]).hostname
        self.connect_ip = self.cluster.get("private_ip") if cfg.get("use_private_ip") else None
        if self.connect_ip:
            try:
                ipaddress.ip_address(self.connect_ip)
            except ValueError:
                raise Error("OCI não retornou IP privado válido para {}".format(name)) from None
        user, password = credential["user"], credential["pass"]
        self.authorization = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(
            cafile=self.cluster.get("ca_file"))), NoRedirect())

    @contextlib.contextmanager
    def resolution(self):
        if not self.connect_ip:
            yield
            return
        with DNS_LOCK:
            original = socket.getaddrinfo
            def resolver(host, *args, **kwargs):
                host = host.decode() if isinstance(host, bytes) else host
                if isinstance(host, str) and host.rstrip(".").lower() == self.endpoint_host.lower():
                    return original(self.connect_ip, *args, **kwargs)
                return original(host, *args, **kwargs)
            socket.getaddrinfo = resolver
            try:
                yield
            finally:
                socket.getaddrinfo = original

    def request(self, method, path, body=None, allow=()):
        raw = body.encode() if isinstance(body, str) else (json.dumps(body).encode() if body is not None else None)
        headers = {"Authorization": self.authorization, "Accept": "application/json",
                   "Content-Type": "application/x-ndjson" if path == "/_bulk" else "application/json"}
        attempts = self.cfg["read_retries"] + 1 if method in ("GET", "HEAD") else 1
        for attempt in range(attempts):
            try:
                request = Request(self.cluster["url"] + path, data=raw, headers=headers, method=method)
                with self.resolution(), self.opener.open(request, timeout=self.cfg["request_timeout"]) as response:
                    payload = response.read()
                result = json.loads(payload) if payload else {}
                if isinstance(result, dict) and result.get("acknowledged") is False:
                    raise Error("Operação não confirmada: {} {}".format(method, path))
                return result
            except HTTPError as exc:
                status = exc.code
                try:
                    error = json.loads(exc.read()).get("error", {})
                    error_type = error.get("type", "unknown") if isinstance(error, dict) else "unknown"
                    reason = error.get("reason", "") if isinstance(error, dict) else ""
                except (ValueError, AttributeError):
                    error_type, reason = "non_json_error", ""
                finally:
                    exc.close()
                if status in allow:
                    return {"_http_status": status}
                if status not in (429, 502, 503, 504) or attempt == attempts - 1:
                    detail = " error_type={} reason={}".format(error_type, reason) if reason else " error_type={}".format(error_type)
                    raise APIError("HTTP {}: {} {}{}".format(status, method, path, detail)) from None
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                LOG.error("lab_transport_error cluster=%s endpoint=%s connect_ip=%s method=%s", self.name,
                          self.cluster["url"], self.connect_ip or "dns", method, path)
                if attempt == attempts - 1:
                    raise Error("Falha de transporte no cluster {} ({}): {}; {}".format(
                        self.name, self.cluster["url"], method + " " + path, diagnosis(exc))) from None
            time.sleep(min(2 ** attempt, 8))

    def exists(self, index):
        return self.request("HEAD", "/" + index_name(index), allow=(404,)).get("_http_status") != 404

    def uuid(self, index):
        return self.request("GET", "/{}/_settings?flat_settings=true".format(index_name(index)))[index]["settings"]["index.uuid"]

    def status(self, index):
        return self.request("GET", "/_plugins/_replication/{}/_status".format(index_name(index)))

    def wait_index(self, index):
        deadline = time.monotonic() + self.cfg["wait_timeout"]
        while not self.exists(index):
            if time.monotonic() >= deadline:
                raise Error("Timeout esperando follower {}".format(index))
            time.sleep(self.cfg["poll_interval"])
        return self.uuid(index)

    def start(self, index, leader, leader_index):
        return self.request("PUT", "/_plugins/_replication/{}/_start".format(index_name(index)), {
            "leader_alias": self.cluster["remote_aliases"][leader], "leader_index": index_name(leader_index),
            "use_roles": {"leader_cluster_role": self.cfg["clusters"][leader]["leader_role"],
                          "follower_cluster_role": self.cluster["follower_role"]}})

    def bulk(self, operations):
        lines = []
        for index, doc_id, document in operations:
            lines.extend([json.dumps({"index": {"_index": index_name(index), "_id": doc_id}}), json.dumps(document)])
        response = self.request("POST", "/_bulk", "\n".join(lines) + "\n")
        if response.get("errors") or len(response.get("items", [])) != len(operations):
            raise Error("Bulk falhou; consulte o log do lab")

    def bulk_delete(self, operations):
        lines = [json.dumps({"delete": {"_index": index_name(index), "_id": doc_id}})
                 for index, doc_id in operations]
        if not lines:
            return 0
        response = self.request("POST", "/_bulk", "\n".join(lines) + "\n")
        items = response.get("items", [])
        if response.get("errors") or len(items) != len(operations):
            raise Error("Bulk de exclusão falhou; consulte o log do lab")
        return sum(1 for item in items if item["delete"].get("result") != "not_found")

    def count(self, index):
        response = self.request("GET", "/{}/_count".format(index_name(index)))
        if response.get("_shards", {}).get("failed", 0):
            raise Error("Contagem parcial no índice {}".format(index))
        return response["count"]

    def indices(self, scoped=True):
        pattern = quote(self.cfg["prefix"], safe="-._") + "*" if scoped else "*"
        rows = self.request("GET", "/_cat/indices/{}?format=json&h=index,status&expand_wildcards=open,closed".format(pattern))
        return sorted(row["index"] for row in rows if not row["index"].startswith("."))


def clients(cfg, credentials):
    return {name: Client(cfg, name, credentials[name]) for name in ("a", "b")}
