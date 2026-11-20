"""Container API unit tests without an OCI connection or a running container."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import json
from unittest.mock import patch

from pydantic import ValidationError

from fsdr_opensearch import api
from fsdr_opensearch.core import Error


class ApiTests(unittest.TestCase):
    def test_mutating_job_executes_by_default_using_internal_module(self):
        args = api.command_arguments(api.OperationRequest(
            command="reprotect", reprotect_parallelism=2))
        self.assertEqual(args[:3], [api.sys.executable, "-m", "fsdr_opensearch.cli"])
        self.assertNotIn("fsdr.py", args)
        self.assertNotIn("--execute", args)
        self.assertNotIn("--allow-recreate", args)

    def test_internal_module_entry_point_invokes_cli(self):
        result = subprocess.run([sys.executable, "-m", "fsdr_opensearch.cli", "--config-help"],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("FSDR configuration-file reference", result.stdout)

    def test_api_rejects_obsolete_execution_flags(self):
        with self.assertRaises(ValidationError):
            api.OperationRequest(command="inventory", execute=True)
        with self.assertRaises(ValidationError):
            api.OperationRequest(command="switchover", dry_run=True)
        with self.assertRaises(ValidationError):
            api.OperationRequest(command="reprotect", allow_recreate=True)

    def test_monitor_without_configuration_has_no_side_effect(self):
        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                api.CONFIG = Path(directory) / "missing.json"
                data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertEqual(data["clusters"], {})
        self.assertTrue(data["errors"])

    def test_dashboard_is_self_contained_and_does_not_embed_token(self):
        self.assertIn("/dashboard/v1/monitor", api.DASHBOARD_HTML)
        self.assertIn("Current controller job", api.DASHBOARD_HTML)
        self.assertIn("Replicated indices comparison", api.DASHBOARD_HTML)
        self.assertIn("top-nav", api.DASHBOARD_HTML)
        self.assertIn("job-grid", api.DASHBOARD_HTML)
        self.assertIn("clusterLabel", api.DASHBOARD_HTML)
        self.assertIn("statusIcon", api.DASHBOARD_HTML)
        self.assertIn('class="status-cell"', api.DASHBOARD_HTML)
        self.assertIn("HISTORY_PAGE_SIZE=11", api.DASHBOARD_HTML)
        self.assertIn("history-pagination", api.DASHBOARD_HTML)
        self.assertNotIn('id="active-job-info"', api.DASHBOARD_HTML)
        self.assertNotIn("api-token", api.DASHBOARD_HTML)

    def test_new_configured_deployment_queues_initial_inventory(self):
        class Manager:
            def __init__(self):
                self.requests = []

            def submit(self, request):
                self.requests.append(request)

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "config.json"
                state = root / "state"
                state.mkdir()
                config.write_text(json.dumps({"secret_id": "ocid1.vaultsecret.oc1.x.y", "state_dir": str(state)}))
                api.CONFIG = config
                manager = Manager()
                api._queue_initial_inventory(manager)
        finally:
            api.CONFIG = original
        self.assertEqual([request.command for request in manager.requests], ["inventory"])

    def test_startup_retries_initial_inventory_when_only_discovery_exists(self):
        class Manager:
            def __init__(self):
                self.requests = []

            def submit(self, request):
                self.requests.append(request)

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state"
                state.mkdir()
                (state / "discovery.json").write_text("{}")
                config = root / "config.json"
                config.write_text(json.dumps({"secret_id": "ocid1.vaultsecret.oc1.x.y", "state_dir": str(state)}))
                api.CONFIG = config
                manager = Manager()
                api._queue_initial_inventory(manager)
        finally:
            api.CONFIG = original
        self.assertEqual([request.command for request in manager.requests], ["inventory"])

    def test_observer_error_is_explained_without_credentials(self):
        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "config.json"
                config.write_text(json.dumps({"secret_id": "ocid1.vaultsecret.oc1.x.y"}))
                api.CONFIG = config
                with patch("fsdr_opensearch.api._observer_clients",
                           side_effect=Error("Discovery is unavailable")):
                    data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertEqual(data["errors"], ["Cannot prepare OpenSearch observer: Discovery is unavailable"])

    def test_monitor_reports_the_live_fsdr_replication_marker(self):
        class Client:
            def __init__(self, marked):
                self.marked = marked

            def request(self, method, path):
                assert (method, path) == ("GET", "/_mapping")
                meta = {"fsdr": {"replicate": True}} if self.marked else {}
                return {"orders": {"mappings": {"_meta": meta}}}

            def count(self, index):
                return 11

            def status(self, index):
                return {"status": "SYNCING", "syncing_details": {}}

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state"
                state.mkdir()
                (state / "state.json").write_text(json.dumps({
                    "active": "a", "phase": "PROTECTION_RESTORED",
                    "indices": [{"a": "orders", "b": "orders"}],
                }))
                config = root / "config.json"
                config.write_text(json.dumps({"secret_id": "secret", "state_dir": str(state)}))
                api.CONFIG = config
                cfg = {"clusters": {
                    "a": {"id": "a-id", "display_name": "Alpha"},
                    "b": {"id": "b-id", "display_name": "Beta"},
                }}
                with patch("fsdr_opensearch.api._observer_clients",
                           return_value=(cfg, {"a": Client(True), "b": Client(False)})):
                    data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertTrue(data["clusters"]["a"]["indices"][0]["fsdr_replicate"])
        self.assertFalse(data["clusters"]["b"]["indices"][0]["fsdr_replicate"])

    def test_observer_reports_incomplete_discovery_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "discovery.json"
            cache.write_text(json.dumps({"schema": 1, "secret_id": "secret", "clusters": {}}))
            with self.assertRaisesRegex(Error, "Discovery cache is incomplete"):
                api.read_cache(root, "secret")
