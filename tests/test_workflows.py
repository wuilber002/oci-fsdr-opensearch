"""Offline fault-injection tests. These do not emulate OCI infrastructure."""

import copy
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from fsdr_opensearch.client import Client
from fsdr_opensearch.core import Error, JsonFormatter, Store, index_name, load_config
from opensearch_lab.dashboard import mutate_once
from opensearch_lab.core import Error as LabError, Store as LabStore, load_fsdr_context
from opensearch_lab.cloud import discover_runtime as lab_discover_runtime
from opensearch_lab.cleanup import cleanup as lab_cleanup
from opensearch_lab.setup import setup
from opensearch_lab.topology import observe_writer
from fsdr_opensearch.operations import (STOPPED, inventory, precheck, reprotect, transition,
                                        validate_post_switchover, validate_switchover)


def demo_config(directory=None):
    return {"clusters": {
        "a": {"url": "https://a.example:9200", "private_ip": None, "username_env": "OPENSEARCH_A_USER",
              "password_env": "OPENSEARCH_A_PASSWORD", "ca_file": None,
              "leader_role": "cross_cluster_replication_leader_full_access",
              "follower_role": "cross_cluster_replication_follower_full_access", "remote_aliases": {"b": "cluster-b"}},
        "b": {"url": "https://b.example:9200", "private_ip": None, "username_env": "OPENSEARCH_B_USER",
              "password_env": "OPENSEARCH_B_PASSWORD", "ca_file": None,
              "leader_role": "cross_cluster_replication_leader_full_access",
              "follower_role": "cross_cluster_replication_follower_full_access", "remote_aliases": {"a": "cluster-a"}}},
        "initial_primary": "a", "expected_version": "3.6.0", "use_private_ip": False,
        "prefix": "fsdr-demo-", "state_dir": str(Path(directory or "/tmp") / "state"),
        "log_dir": str(Path(directory or "/tmp") / "logs"), "request_timeout": 20, "read_retries": 0,
        "wait_timeout": 0.02, "poll_interval": 0.001, "dashboard_interval": 2,
        "operations_per_second": 2, "create_probability": 0.6, "identity": "test"}


class FakeCluster:
    def __init__(self, cfg, name, clusters):
        self.cfg, self.name, self.clusters = cfg, name, clusters
        self.data = {}
        self.calls = []
        self.after_delete_failure = False
        self.after_stop_failure = False
        self.after_bulk_failure = False
        self.after_start_failure = False
        self.autofollow = []
        self.stale_checkpoint = None

    def _sync(self, index):
        data = self.data[index]
        if data.get("follow"):
            source, leader_index = data["follow"]
            leader = self.clusters[source].data[leader_index]
            data["docs"] = copy.deepcopy(leader["docs"])
            data["seq"] = leader["seq"]

    def exists(self, index):
        return index in self.data

    def uuid(self, index):
        return self.data[index]["uuid"]

    def wait_index(self, index):
        return self.uuid(index)

    def indices(self, scoped=True):
        return sorted(self.data)

    def status(self, index):
        data = self.data[index]
        if not data.get("follow"):
            return {"status": STOPPED}
        self._sync(index)
        source, leader_index = data["follow"]
        checkpoint = self.stale_checkpoint if self.stale_checkpoint is not None else data["seq"]
        return {"status": "SYNCING", "leader_alias": self.cfg["clusters"][self.name]["remote_aliases"][source],
                "leader_index": leader_index, "syncing_details": {
                    "leader_checkpoint": checkpoint, "follower_checkpoint": checkpoint}}

    def start(self, index, leader, leader_index):
        self.calls.append(("START", index))
        if index in self.data:
            raise Error("existing index")
        self.data[index] = copy.deepcopy(self.clusters[leader].data[leader_index])
        self.data[index].update(uuid=uuid.uuid4().hex, follow=(leader, leader_index), blocked=True)
        if self.after_start_failure:
            self.after_start_failure = False
            raise Error("response lost after start")

    def count(self, index):
        self._sync(index)
        return len(self.data[index]["docs"])

    def bulk(self, operations):
        for index, doc_id, doc in operations:
            self.data[index]["docs"][doc_id] = doc
            self.data[index]["seq"] += 1
        if self.after_bulk_failure:
            self.after_bulk_failure = False
            raise Error("response lost after committed bulk")

    def request(self, method, path, body=None, allow=()):
        self.calls.append((method, path))
        path = path.split("?")[0]
        if path == "/":
            return {"version": {"number": self.cfg["expected_version"]}, "cluster_uuid": self.name}
        if path == "/_cluster/health":
            return {"status": "green"}
        if path == "/_remote/info":
            return {v: {"connected": True} for v in self.cfg["clusters"][self.name]["remote_aliases"].values()}
        if path == "/_plugins/_security/authinfo":
            return {"roles": ["cross_cluster_replication_leader_full_access", "cross_cluster_replication_follower_full_access"]}
        if path == "/_plugins/_replication/autofollow_stats":
            return {"autofollow_stats": self.autofollow}
        parts = path.strip("/").split("/")
        if parts[:2] == ["_plugins", "_replication"]:
            index, operation = parts[2:]
            if operation == "_stop":
                self._sync(index)
                self.data[index]["follow"] = None
                if self.after_stop_failure:
                    self.after_stop_failure = False
                    raise Error("response lost after stop")
                return {"acknowledged": True}
        index = parts[0]
        if len(parts) == 1:
            if method == "PUT":
                self.data[index] = {"uuid": uuid.uuid4().hex, "docs": {}, "seq": -1,
                                    "mapping": body["mappings"], "blocked": False, "follow": None}
                return {"acknowledged": True}
            if method == "DELETE":
                del self.data[index]
                if self.after_delete_failure:
                    self.after_delete_failure = False
                    raise Error("response lost after delete")
                return {"acknowledged": True}
        data = self.data[index]
        if parts[1:] == ["_block", "write"]:
            data["blocked"] = True
            # The block API need not report shard acknowledgement. Production
            # safety is established by reading index.blocks.write afterwards.
            return {"acknowledged": True}
        if parts[1] == "_settings":
            if method == "PUT":
                data["blocked"] = body["index.blocks.write"]
                return {"acknowledged": True}
            return {index: {"settings": {"index.uuid": data["uuid"], "index.blocks.write": str(data["blocked"]).lower()}}}
        if parts[1] == "_mapping":
            return {index: {"mappings": data["mapping"]}}
        if parts[1] == "_refresh":
            return {"_shards": {"failed": 0}}
        if parts[1] == "_stats":
            return {"indices": {index: {"shards": {"0": [{"routing": {"primary": True},
                    "seq_no": {"max_seq_no": data["seq"], "global_checkpoint": data["seq"]}}]}}}}
        if parts[1] == "_search":
            ids = list(data["docs"])[:1]
            return {"hits": {"hits": [{"_id": value} for value in ids]}}
        if parts[1] in ("_doc", "_create"):
            doc_id = parts[2]
            if method == "GET":
                self._sync(index)
                return {"found": doc_id in data["docs"]}
            if data["blocked"] or data.get("follow"):
                raise Error("write blocked")
            if method == "PUT":
                data["docs"][doc_id] = body
            elif method == "DELETE":
                data["docs"].pop(doc_id, None)
            data["seq"] += 1
            return {"result": "ok"}
        raise AssertionError((method, path, body))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = demo_config(self.temp.name)
        self.cfg.update(state_dir=str(Path(self.temp.name) / "dr"))
        self.store = Store(self.cfg)
        self.labstore = LabStore(dict(self.cfg, state_dir=str(Path(self.temp.name) / "lab"), identity="lab"))
        self.cs = {}
        for name in ("a", "b"):
            self.cs[name] = FakeCluster(self.cfg, name, self.cs)

    def seed(self, indices=2):
        setup(self.cfg, self.cs, self.labstore, indices, 5, 2, 1, 1)
        inventory(self.cfg, self.cs, self.store)

    def switch(self, resume=False):
        return transition(self.cfg, self.cs, self.store, "switchover", resume=resume)

    def protect(self):
        return reprotect(self.cfg, self.cs, self.store, allow_recreate=True)

    def protect_with_parallelism(self, parallelism):
        return reprotect(self.cfg, self.cs, self.store, allow_recreate=True,
                         parallelism=parallelism)

    def test_setup_and_round_trip(self):
        self.seed()
        original = self.cs["a"].uuid("fsdr-demo-0001")
        result = self.switch()
        self.assertEqual((result["active"], result["phase"]), ("b", "SERVICE_RESTORED"))
        self.assertEqual(self.cs["a"].uuid("fsdr-demo-0001"), original)
        self.assertEqual(self.cs["b"].count("fsdr-demo-0001"), 5)
        self.assertEqual(self.protect()["phase"], "PROTECTION_RESTORED")
        self.assertNotEqual(self.cs["a"].uuid("fsdr-demo-0001"), original)
        self.assertEqual(self.switch()["active"], "a")
        self.protect()
        self.assertEqual(self.cs["a"].count("fsdr-demo-0001"), 5)
        self.assertEqual(self.cs["b"].count("fsdr-demo-0001"), 5)

    def test_lab_cleanup_removes_only_owned_registered_pairs(self):
        setup(self.cfg, self.cs, self.labstore, 2, 5, 2, 1, 1)
        demo_uuid = self.labstore.read()["demo_uuid"]
        cleanup_cfg = dict(self.cfg, fsdr_state={"phase": "PROTECTION_RESTORED"})
        plan = lab_cleanup(cleanup_cfg, self.cs, self.labstore)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(len(plan["indices"]), 2)
        self.assertTrue(self.cs["a"].exists("fsdr-demo-0001"))

        # A reprotect-style rebuild changes UUIDs but preserves mappings.
        self.cs["b"].data["fsdr-demo-0001"]["uuid"] = uuid.uuid4().hex

        result = lab_cleanup(cleanup_cfg, self.cs, self.labstore, execute=True)
        self.assertEqual(result["deleted_pairs"], 2)
        self.assertFalse(self.cs["a"].data)
        self.assertFalse(self.cs["b"].data)
        self.assertIsNone(self.labstore.read(required=False))

        setup(self.cfg, self.cs, self.labstore, 1, 5, 2, 1, 1)
        self.assertEqual(self.labstore.read()["demo_uuid"], demo_uuid)

    def test_lab_cleanup_refuses_when_fsdr_is_transitioning(self):
        setup(self.cfg, self.cs, self.labstore, 1, 5, 2, 1, 1)
        cleanup_cfg = dict(self.cfg, fsdr_state={"phase": "TRANSITION"})
        with self.assertRaisesRegex(LabError, "bloqueada durante DR"):
            lab_cleanup(cleanup_cfg, self.cs, self.labstore)

    def test_validate_switchover_persists_inventory_and_passes_readiness(self):
        self.seed()
        result = validate_switchover(self.cfg, self.cs, self.store)
        self.assertEqual((result["validation"], result["active"], result["index_count"]),
                         ("switchover", "a", 2))
        self.assertTrue(result["ready"])
        self.assertEqual(self.store.read()["phase"], "READY")

    def test_inventory_requires_complete_fsdr_annotations(self):
        self.seed()
        result = inventory(self.cfg, self.cs, self.store)
        self.assertEqual(result["annotation"]["mode"], "required")
        del self.cs["a"].data["fsdr-demo-0001"]["mapping"]["_meta"]["fsdr"]
        with self.assertRaisesRegex(Error, "replication marker is required"):
            inventory(self.cfg, self.cs, self.store)

    def test_failover_can_confirm_stale_manifest_with_follower_annotations(self):
        self.seed()
        state = self.store.read()
        state["inventory_at"] = 0
        self.store.write(state)
        result = transition(self.cfg, self.cs, self.store, "failover",
                            source_fenced=True, accept_data_loss=True,
                            use_follower_annotations=True)
        self.assertEqual(result["phase"], "SERVICE_RESTORED")
        self.assertTrue(result["transition"]["annotation_fallback"]["used"])

    def test_post_switchover_validation_checks_blocks_and_document_counts(self):
        self.seed()
        self.switch()
        result = validate_post_switchover(self.cfg, self.cs, self.store)
        self.assertTrue(result["ready_for_reprotect"])
        self.assertEqual(result["active"], "b")
        self.assertEqual([item["documents"] for item in result["indices"]], [5, 5])

    def test_post_switchover_validation_rejects_changed_document_count(self):
        self.seed()
        self.switch()
        self.cs["b"].data["fsdr-demo-0001"]["docs"]["after-switch"] = {"value": 1}
        with self.assertRaisesRegex(Error, "Document count mismatch"):
            validate_post_switchover(self.cfg, self.cs, self.store)

    def test_reprotect_starts_and_waits_in_bounded_batches(self):
        self.seed(indices=4)
        self.switch()
        with patch("fsdr_opensearch.operations.wait_sync") as wait:
            result = self.protect_with_parallelism(2)
        self.assertEqual(result["phase"], "PROTECTION_RESTORED")
        batches = [len(call.args[2]) for call in wait.call_args_list]
        self.assertEqual(batches, [2, 2, 4])
        self.assertEqual(self.store.read()["transition"]["reprotect_parallelism"], 2)

    def test_reprotect_rejects_invalid_parallelism(self):
        self.seed()
        self.switch()
        with self.assertRaisesRegex(Error, "positive integer"):
            self.protect_with_parallelism(0)

    def test_precheck_explains_how_to_add_missing_reverse_alias(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["clusters"]["b"]["remote_aliases"] = {}
        with self.assertRaisesRegex(Error, "Cross cluster connections > Inbound") as raised:
            precheck(cfg, self.cs)
        self.assertIn("CCR alias for a", str(raised.exception))

    def test_setup_resume_after_bulk_response_lost(self):
        self.cs["a"].after_bulk_failure = True
        with self.assertRaises(Error):
            self.seed()
        self.assertEqual(self.labstore.read()["phase"], "SETUP")
        self.seed()
        self.assertEqual(self.cs["a"].count("fsdr-demo-0001"), 5)

    def test_completed_setup_cannot_overwrite_live_documents(self):
        self.seed()
        with self.assertRaises(LabError):
            self.seed()

    def test_completed_setup_expands_to_requested_total_without_touching_existing_indices(self):
        self.seed(indices=2)
        existing_uuids = {name: self.cs[name].uuid("fsdr-demo-0001") for name in ("a", "b")}
        setup(self.cfg, self.cs, self.labstore, 5, 5, 2, 1, 1)
        state = self.labstore.read()
        self.assertEqual((state["phase"], len(state["indices"])), ("PROTECTION_RESTORED", 5))
        self.assertNotIn("setup_pending", state)
        self.assertEqual(existing_uuids["a"], self.cs["a"].uuid("fsdr-demo-0001"))
        self.assertEqual(existing_uuids["b"], self.cs["b"].uuid("fsdr-demo-0001"))
        for number in range(1, 6):
            index = "fsdr-demo-{:04d}".format(number)
            self.assertEqual(self.cs["a"].count(index), 5)
            self.assertEqual(self.cs["b"].count(index), 5)

    def test_setup_refuses_preexisting_index(self):
        self.cs["a"].data["fsdr-demo-0001"] = {}
        with self.assertRaises(LabError):
            self.seed()
        self.assertIsNone(self.store.read(required=False))

    def test_reprotect_requires_explicit_destructive_flags(self):
        self.seed()
        self.switch()
        old_uuid = self.cs["a"].uuid("fsdr-demo-0001")
        with self.assertRaises(Error):
            reprotect(self.cfg, self.cs, self.store)
        self.assertEqual(old_uuid, self.cs["a"].uuid("fsdr-demo-0001"))

    def test_reprotect_resumes_lost_delete_response_without_deleting_new_follower(self):
        self.seed()
        self.switch()
        self.cs["a"].after_delete_failure = True
        with self.assertRaises(Error):
            self.protect()
        self.assertFalse(self.cs["a"].exists("fsdr-demo-0001"))
        self.protect()
        deletes = [call for call in self.cs["a"].calls if call == ("DELETE", "/fsdr-demo-0001")]
        self.assertEqual(len(deletes), 1)

    def test_reprotect_resumes_after_start_response_lost(self):
        self.seed()
        self.switch()
        self.cs["a"].after_start_failure = True
        with self.assertRaises(Error):
            self.protect()
        follower_uuid = self.cs["a"].uuid("fsdr-demo-0001")
        self.protect()
        self.assertEqual(follower_uuid, self.cs["a"].uuid("fsdr-demo-0001"))

    def test_resume_after_partial_stop(self):
        self.seed()
        self.cs["b"].after_stop_failure = True
        with self.assertRaises(Error):
            self.switch()
        self.assertEqual(self.store.read()["phase"], "TRANSITION")
        self.assertEqual(self.switch(resume=True)["phase"], "SERVICE_RESTORED")

    def test_equal_but_stale_ccr_checkpoints_do_not_allow_switchover(self):
        self.seed()
        self.cs["b"].stale_checkpoint = -1
        with self.assertRaisesRegex(Error, "Timeout"):
            self.switch()
        self.assertEqual(self.store.read()["active"], "a")
        self.assertTrue(self.cs["a"].data["fsdr-demo-0001"]["blocked"])

    def test_failover_requires_fencing_and_data_loss_acknowledgements(self):
        self.seed()
        with self.assertRaises(Error):
            transition(self.cfg, self.cs, self.store, "failover")

    def test_failover_never_contacts_source(self):
        self.seed()
        # Disconnect source after freezing the already-received follower snapshot.
        # FakeCluster's replication mirror is disabled while retaining CCR metadata.
        self.cs["b"]._sync = lambda index: None
        self.cs["a"].request = lambda *args, **kwargs: self.fail("contacted unavailable source")
        self.cs["a"].uuid = lambda *args: self.fail("read unavailable source UUID")
        state = transition(self.cfg, self.cs, self.store, "failover",
                           source_fenced=True, accept_data_loss=True)
        self.assertEqual(state["active"], "b")

    def test_uuid_drift_blocks_delete(self):
        self.seed()
        self.switch()
        self.cs["a"].data["fsdr-demo-0001"]["uuid"] = "external-recreation"
        with self.assertRaisesRegex(Error, "UUID"):
            self.protect()
        self.assertTrue(self.cs["a"].exists("fsdr-demo-0001"))

    def test_autofollow_is_rejected_before_switch(self):
        self.seed()
        self.cs["b"].autofollow = [{"name": "external-rule"}]
        with self.assertRaisesRegex(Error, "Auto-follow"):
            self.switch()
        self.assertEqual(self.store.read()["phase"], "READY")

    def test_wrong_leader_alias_blocks_inventory(self):
        self.seed()
        original = self.cs["b"].status

        def wrong_alias(index):
            result = original(index)
            result["leader_alias"] = "unrelated-cluster"
            return result

        with patch.object(self.cs["b"], "status", side_effect=wrong_alias), self.assertRaises(Error):
            inventory(self.cfg, self.cs, self.store)

    def test_sync_failure_leaves_service_active_and_protection_pending(self):
        self.seed()
        self.switch()
        with patch("fsdr_opensearch.operations.wait_sync", side_effect=Error("sync failed")):
            with self.assertRaises(Error):
                self.protect()
        state = self.store.read()
        self.assertEqual((state["active"], state["phase"]), ("b", "REPROTECTING"))
        self.assertEqual(self.protect()["phase"], "PROTECTION_RESTORED")

    def test_external_recreation_after_delete_is_never_deleted(self):
        self.seed()
        self.switch()
        self.cs["a"].after_delete_failure = True
        with self.assertRaises(Error):
            self.protect()
        replacement = copy.deepcopy(self.cs["b"].data["fsdr-demo-0001"])
        replacement["uuid"] = "external"
        self.cs["a"].data["fsdr-demo-0001"] = replacement
        with self.assertRaisesRegex(Error, "UUID"):
            self.protect()
        self.assertEqual(self.cs["a"].uuid("fsdr-demo-0001"), "external")

    def test_new_switchover_requires_reprotect_first(self):
        self.seed()
        self.switch()
        with self.assertRaises(Error):
            self.switch()

    def test_dr_never_creates_or_deletes_documents(self):
        self.seed()
        for client in self.cs.values():
            client.calls.clear()
        self.switch()
        self.protect()
        for client in self.cs.values():
            self.assertFalse(any('/_doc/' in path or '/_create/' in path
                                 for method, path in client.calls))

    def test_generator_uses_new_primary_after_switch(self):
        self.seed()
        self.switch()
        state = observe_writer(self.cfg, self.cs, self.labstore.read())
        from unittest.mock import Mock
        rng = Mock()
        rng.choice.return_value = state["indices"][0]
        rng.random.return_value = 0
        rng.randrange.return_value = 42
        self.assertEqual(mutate_once(self.cfg, self.cs, state, rng), "create")
        self.assertEqual(self.cs["b"].count("fsdr-demo-0001"), 6)
        self.assertEqual(self.cs["a"].count("fsdr-demo-0001"), 5)
        rng.random.return_value = 1
        self.assertEqual(mutate_once(self.cfg, self.cs, state, rng), "delete")
        self.assertEqual(self.cs["b"].count("fsdr-demo-0001"), 5)

    def test_lab_observes_both_directions_without_dr_state(self):
        self.seed()
        lab_before = self.labstore.path.read_bytes()
        self.switch()
        self.assertEqual(observe_writer(self.cfg, self.cs, self.labstore.read())["active"], "b")
        self.protect()
        self.assertEqual(observe_writer(self.cfg, self.cs, self.labstore.read())["active"], "b")
        self.switch()
        self.assertEqual(observe_writer(self.cfg, self.cs, self.labstore.read())["active"], "a")
        self.assertEqual(self.labstore.path.read_bytes(), lab_before)


    def test_lab_pauses_when_both_sides_are_writable(self):
        self.seed()
        for index in self.cs["b"].data:
            self.cs["b"].data[index].update(follow=None, blocked=False)
        with self.assertRaisesRegex(LabError, "ambígua"):
            observe_writer(self.cfg, self.cs, self.labstore.read())

    def test_dr_inventory_includes_replicated_indices_outside_lab_prefix(self):
        self.seed()
        self.cs["a"].data["application-orders"] = copy.deepcopy(self.cs["a"].data["fsdr-demo-0001"])
        self.cs["b"].start("orders-standby", "a", "application-orders")
        result = inventory(self.cfg, self.cs, self.store)
        self.assertTrue(any(e["a"] == "application-orders" and e["b"] == "orders-standby" for e in result["indices"]))

    def test_bidirectional_ccr_is_ambiguous(self):
        self.seed()
        self.cs["b"].data["orders-b"] = copy.deepcopy(self.cs["a"].data["fsdr-demo-0001"])
        self.cs["a"].start("orders-b-copy", "b", "orders-b")
        with self.assertRaisesRegex(Error, "ambiguous"):
            inventory(self.cfg, self.cs, self.store)


class CoreTests(unittest.TestCase):
    def test_lab_uses_nested_clusters_from_fsdr_discovery(self):
        first = "ocid1.opensearchcluster.oc1.eu-frankfurt-1.first"
        second = "ocid1.opensearchcluster.oc1.uk-london-1.second"
        secret = "ocid1.vaultsecret.oc1.eu-frankfurt-1.example"

        class FakeLabCloud:
            def __init__(self, timeout):
                pass

            def secret(self, secret_id):
                return {first: {"user": "u", "pass": "one"}, second: {"user": "u", "pass": "two"}}

            def cluster(self, cluster_id):
                peer = second if cluster_id == first else first
                return {"id": cluster_id, "url": "https://{}.example:9200".format(cluster_id[-5:]),
                        "private_ip": "10.0.0.1", "display_name": cluster_id[-5:], "version": "3.6.0",
                        "remote_ocids": {"peer": peer}}

        with tempfile.TemporaryDirectory() as directory, patch("opensearch_lab.cloud.Cloud", FakeLabCloud):
            cfg = {"secret_id": secret, "state_dir": directory, "log_dir": directory, "request_timeout": 20,
                   "ca_file": None, "leader_role": None, "follower_role": None}
            fsdr = {"discovery": {"clusters": {"a": {"id": first}, "b": {"id": second}}},
                    "state": {"active": "b"}, "fsdr_state_dir": "/var/lib/oci-opensearch-fsdr"}
            runtime, credentials = lab_discover_runtime(cfg, fsdr)
            self.assertEqual((runtime["clusters"]["a"]["id"], runtime["initial_primary"]), (first, "b"))
            self.assertEqual(credentials["b"]["pass"], "two")

    def test_lab_reads_secret_and_active_direction_from_fsdr_files(self):
        secret = "ocid1.vaultsecret.oc1.eu-frankfurt-1.example"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "fsdr-state"
            state_dir.mkdir()
            config = root / "config.json"
            config.write_text(json.dumps({"secret_id": secret, "state_dir": str(state_dir),
                                          "use_private_ip": True}))
            (state_dir / "discovery.json").write_text(json.dumps({"secret_id": secret, "clusters": {
                "a": {"id": "ocid1.opensearchcluster.oc1.eu-frankfurt-1.a"},
                "b": {"id": "ocid1.opensearchcluster.oc1.uk-london-1.b"}}}))
            (state_dir / "state.json").write_text(json.dumps({"schema": 2, "active": "b", "indices": [{"a": "x", "b": "y"}]}))
            context = load_fsdr_context(config)
            self.assertEqual((context["secret_id"], context["state"]["active"]), (secret, "b"))
            self.assertTrue(context["use_private_ip"])

    def test_rejects_wildcards_and_url_injection_in_index_names(self):
        for name in ("*", "_all", "a,b", "a/b", "a?x=y", "UPPER", "..", ""):
            with self.subTest(name=name), self.assertRaises(Error):
                index_name(name)

    def test_redacts_password_in_exception_and_message(self):
        secret = 'secr"et-password'
        formatter = JsonFormatter("run", [secret])
        record = logging.LogRecord("fsdr", logging.ERROR, "", 1, "failure %s", (secret,), None)
        output = formatter.format(record)
        self.assertNotIn("password", output)
        self.assertIn("[REDACTED]", output)
        json.loads(output)

    def test_operation_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = {"state_dir": directory, "identity": "x"}
            first, second = Store(cfg), Store(cfg)
            with first.lock():
                with self.assertRaises(Error), second.lock(blocking=False):
                    pass

    def test_state_identity_prevents_accidental_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"state_dir": directory, "identity": "x", "initial_primary": "a"})
            store.write(store.new())
            with self.assertRaises(Error):
                Store({"state_dir": directory, "identity": "different"}).read()

    def test_http_transport_never_retries_mutation(self):
        cfg = demo_config()
        with patch.dict(os.environ, OPENSEARCH_A_USER="user", OPENSEARCH_A_PASSWORD="password"):
            client = Client(cfg, "a")
        with patch.object(client.opener, "open", side_effect=URLError("offline")) as opened:
            with self.assertRaises(Error):
                client.request("DELETE", "/fsdr-demo-0001")
            self.assertEqual(opened.call_count, 1)

    def test_read_transport_error_identifies_dns_and_confirms_no_mutation(self):
        cfg = demo_config()
        cfg["read_retries"] = 0
        with patch.dict(os.environ, OPENSEARCH_A_USER="user", OPENSEARCH_A_PASSWORD="password"):
            client = Client(cfg, "a")
        dns_error = URLError(socket.gaierror(-2, "name or service not known"))
        with patch.object(client.opener, "open", side_effect=dns_error):
            with self.assertRaises(Error) as raised:
                client.request("GET", "/")
        self.assertIn("no change was sent", str(raised.exception))
        self.assertIn("private DNS", str(raised.exception))
        self.assertIn(cfg["clusters"]["a"]["url"], str(raised.exception))

    def test_private_ip_connects_without_replacing_tls_hostname(self):
        cfg = demo_config()
        cfg["use_private_ip"] = True
        cfg["clusters"]["a"]["private_ip"] = "10.40.2.134"
        with patch.dict(os.environ, OPENSEARCH_A_USER="user", OPENSEARCH_A_PASSWORD="password"):
            client = Client(cfg, "a")
        self.assertEqual(client.connect_ip, "10.40.2.134")
        with client.private_ip_resolution():
            addresses = socket.getaddrinfo(client.endpoint_host, 9200)
        self.assertEqual(addresses[0][4][0], "10.40.2.134")

    def test_bulk_logs_and_rejects_partial_failure(self):
        client = object.__new__(Client)
        client.name = "a"
        client.request = lambda *args: {"errors": True, "items": [
            {"index": {"status": 201, "_id": "1"}},
            {"index": {"status": 429, "_id": "2", "error": {"type": "rejected"}}}]}
        with self.assertRaises(Error):
            client.bulk([("demo", "1", {}), ("demo", "2", {})])


if __name__ == "__main__":
    unittest.main()
