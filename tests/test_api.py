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
    def test_job_invokes_internal_module_not_removed_host_wrapper(self):
        args = api.command_arguments(api.OperationRequest(
            command="reprotect", execute=True, allow_recreate=True, reprotect_parallelism=2))
        self.assertEqual(args[:3], [api.sys.executable, "-m", "fsdr_opensearch.cli"])
        self.assertNotIn("fsdr.py", args)
        self.assertIn("--execute", args)
        self.assertIn("--allow-recreate", args)

    def test_internal_module_entry_point_invokes_cli(self):
        result = subprocess.run([sys.executable, "-m", "fsdr_opensearch.cli", "--config-help"],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("FSDR configuration-file reference", result.stdout)

    def test_api_rejects_invalid_execution_mode(self):
        with self.assertRaises(ValidationError):
            api.OperationRequest(command="inventory", execute=True)
        with self.assertRaises(ValidationError):
            api.OperationRequest(command="switchover", execute=True, dry_run=True)

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
        self.assertIn("Current environment", api.DASHBOARD_HTML)
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

    def test_observer_reports_incomplete_discovery_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "discovery.json"
            cache.write_text(json.dumps({"schema": 1, "secret_id": "secret", "clusters": {}}))
            with self.assertRaisesRegex(Error, "Discovery cache is incomplete"):
                api.read_cache(root, "secret")
