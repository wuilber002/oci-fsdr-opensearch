"""OCI SDK boundary, Secret validation and the public FSDR CLI, without OCI calls."""

import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from fsdr_opensearch import cli
from fsdr_opensearch.cloud import (Cloud, CloudError, decode_credentials, discover,
                                  endpoint, pair_identity, runtime_config, save_cache)
from fsdr_opensearch.core import Error, Store
from test_workflows import FakeCluster, demo_config
from opensearch_lab.core import Store as LabStore
from opensearch_lab.setup import setup


A = "ocid1.opensearchcluster.oc1.eu-frankfurt-1.aaaaafr"
B = "ocid1.opensearchcluster.oc1.uk-london-1.aaaaln"
S = "ocid1.vaultsecret.oc1.eu-frankfurt-1.aaaasecret"


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def entries():
    return [{"cluster_id": A, "credencial": {"user": "admin", "pass": 'test-"pass-A'}},
            {"cluster_id": B, "credencial": {"user": "admin", "pass": "test-pass-B"}}]


def metadata(cluster_id):
    is_a = cluster_id == A
    return {"id": cluster_id, "url": "https://a.example:9200" if is_a else "https://b.example:9200",
            "display_name": "Frankfurt" if is_a else "London", "version": "3.6.0",
            "region": "eu-frankfurt-1" if is_a else "uk-london-1", "private_ip": "10.0.0.1",
            "remote_ocids": {"cluster-b" if is_a else "cluster-a": B if is_a else A}}


class FakeCloud:
    def __init__(self, timeout=20):
        self.fail = {}
        self.read_ids = []

    def credentials(self, secret_id):
        return decode_credentials(encoded(list(reversed(entries()))))

    def cluster(self, cluster_id):
        self.read_ids.append(cluster_id)
        if cluster_id in self.fail:
            raise self.fail[cluster_id]
        return metadata(cluster_id)


class SecretAndSDKTests(unittest.TestCase):
    def test_secret_order_does_not_determine_roles_or_identity(self):
        first = decode_credentials(encoded(entries()))
        second = decode_credentials(encoded(list(reversed(entries()))))
        self.assertEqual(first, second)
        self.assertEqual(pair_identity(first), pair_identity(second))

    def test_secret_rejects_missing_duplicate_invalid_and_incomplete_entries(self):
        bad = [[], entries()[:1], entries() + entries()[:1], [entries()[0]] * 2,
               [{"cluster_id": A, "credencial": {"user": "x"}}, entries()[1]],
               [{"cluster_id": "not-an-ocid", "credencial": {"user": "x", "pass": "x"}}, entries()[1]],
               [None, entries()[1]], {"clusters": entries()}]
        for document in bad:
            with self.subTest(document_type=type(document).__name__), self.assertRaises(Error):
                decode_credentials(encoded(document))

    def test_endpoint_normalization_and_rejection(self):
        self.assertEqual(endpoint("a.example"), "https://a.example:9200")
        self.assertEqual(endpoint("https://a.example:9200/"), "https://a.example:9200")
        for value in ("http://a.example", "https://user:pass@a.example", "https://a.example/path"):
            with self.assertRaises(Error):
                endpoint(value)

    def test_current_secret_and_regional_sdk_calls(self):
        calls = []

        class Secrets:
            def __init__(self, config, **kwargs):
                calls.append(("secret-region", config["region"]))

            def get_secret_bundle(self, secret_id, **kwargs):
                calls.append((secret_id, kwargs["stage"]))
                return NS(headers={}, data=NS(secret_bundle_content=NS(content_type="BASE64", content=encoded(entries()))))

        data = NS(id=A, lifecycle_state="ACTIVE", opensearch_fqdn="a.example", display_name="A",
                  opensearch_private_ip="10.0.0.1", software_version="3.6.0",
                  security_master_user_password_hash="must-never-be-persisted",
                  outbound_cluster_config=NS(is_enabled=True, outbound_clusters=[NS(display_name="cluster-b", seed_cluster_id=B)]))

        class Clusters:
            def __init__(self, config, **kwargs):
                calls.append(("cluster-region", config["region"]))

            def get_opensearch_cluster(self, cluster_id, **kwargs):
                calls.append(("get-cluster", cluster_id))
                return NS(headers={}, data=data)

        cloud = object.__new__(Cloud)
        cloud.sdk = NS(regions=NS(REGIONS_SHORT_NAMES={"fra": "eu-frankfurt-1"}),
                       secrets=NS(SecretsClient=Secrets), opensearch=NS(OpensearchClusterClient=Clusters),
                       retry=NS(NoneRetryStrategy=lambda: None))
        cloud.timeout, cloud.signer = 20, object()
        self.assertEqual(len(cloud.credentials(S)), 2)
        selected = cloud.cluster(A)
        self.assertIn((S, "CURRENT"), calls)
        self.assertIn(("cluster-region", "eu-frankfurt-1"), calls)
        self.assertNotIn("password", json.dumps(selected))
        self.assertEqual(selected["remote_ocids"], {"cluster-b": B})
        self.assertEqual(cloud.region("ocid1.vaultsecret.oc1.fra.secret"), "eu-frankfurt-1")

    def test_cached_source_metadata_only_for_explicit_transient_source_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            creds = cloud.credentials(S)
            cache = discover(cloud, S, creds, directory)
            save_cache(directory, cache)
            cloud.fail[A] = CloudError("GetOpensearchCluster", 503, True)
            self.assertEqual(discover(cloud, S, creds, directory, {A})["clusters"]["a"]["id"], A)
            with self.assertRaises(CloudError):
                discover(cloud, S, creds, directory)
            cloud.fail[A] = CloudError("GetOpensearchCluster", 403, False)
            with self.assertRaises(CloudError):
                discover(cloud, S, creds, directory, {A})
            text = (Path(directory) / "discovery.json").read_text()
            self.assertNotIn("test-pass", text)
            self.assertNotIn("credencial", text)

    def test_changed_pair_in_secret_rejects_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            creds = cloud.credentials(S)
            save_cache(directory, discover(cloud, S, creds, directory))
            changed = dict(creds)
            changed["ocid1.opensearchcluster.oc1.uk-london-1.other"] = changed.pop(B)
            with self.assertRaisesRegex(Error, "Cluster pair"):
                discover(cloud, S, changed, directory)

    def test_multiple_aliases_to_peer_are_resolved_deterministically(self):
        discovered = {"clusters": {"a": metadata(A), "b": metadata(B)}}
        discovered["clusters"]["a"]["remote_ocids"]["alternate-b"] = B
        cfg = runtime_config(discovered, "/tmp/test", "/tmp/test/log")
        self.assertEqual(cfg["clusters"]["a"]["remote_aliases"]["b"], "alternate-b")
        self.assertNotIn("prefix", cfg)
        self.assertIsNone(cfg.get("initial_primary"))


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cfg = demo_config(self.directory)
        self.cfg.update(state_dir=str(self.directory / "lab"), identity="lab")
        self.cs = {}
        for name in ("a", "b"):
            self.cs[name] = FakeCluster(self.cfg, name, self.cs)
        setup(self.cfg, self.cs, LabStore(self.cfg), 2, 3, 2, 1, 1)

    def run_cli(self, command, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("fsdr_opensearch.cli.Cloud", FakeCloud), \
                patch("fsdr_opensearch.cli.clients", return_value=self.cs):
            code = cli.main([command, S, "--state-dir", str(self.directory / "dr"), *extra])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None

    def test_secret_only_cli_round_trip_and_separate_state(self):
        self.assertEqual(self.run_cli("discover")[0], 0)
        code, response = self.run_cli("inventory")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["active"], "a")
        code, response = self.run_cli("switchover")
        self.assertEqual((code, response["result"]["active"]), (0, "b"))
        self.assertEqual(self.run_cli("reprotect", "--allow-recreate")[0], 0)
        self.assertEqual(self.run_cli("switchover")[1]["result"]["active"], "a")
        self.assertEqual(LabStore(self.cfg).read()["active"], "a")  # Lab state was never changed by DR.
        for file in (self.directory / "dr").rglob("*"):
            if file.is_file():
                self.assertNotIn("test-pass-B", file.read_text())

    def test_status_is_offline_and_never_reads_secret(self):
        self.run_cli("inventory")
        with patch("fsdr_opensearch.cli.Cloud", side_effect=AssertionError("SDK invoked")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["status", S, "--state-dir", str(self.directory / "dr")]), 0)

    def test_cli_failover_with_source_oci_down_uses_cache_and_never_source_opensearch(self):
        self.assertEqual(self.run_cli("inventory")[0], 0)
        cloud = FakeCloud()
        cloud.fail[A] = CloudError("GetOpensearchCluster", 503, True)
        self.cs["b"]._sync = lambda index: None
        self.cs["a"].request = lambda *args, **kwargs: self.fail("contacted source OpenSearch")
        self.cs["a"].uuid = lambda *args: self.fail("read source UUID")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch("fsdr_opensearch.cli.Cloud", return_value=cloud), \
                patch("fsdr_opensearch.cli.clients", return_value=self.cs):
            code = cli.main(["failover", S, "--state-dir", str(self.directory / "dr"),
                             "--source-fenced", "--accept-data-loss"])
        self.assertEqual(code, 0)

    def test_dr_cli_has_no_lab_flags(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for flag in ("--prefix", "--demo-only-routing", "--discard-demo-data"):
                with self.assertRaises(SystemExit) as raised:
                    cli.parser().parse_args(["switchover", S, flag, "value"])
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
