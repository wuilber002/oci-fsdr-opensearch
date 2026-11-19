"""Container API unit tests without an OCI connection or a running container."""

from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from fsdr_opensearch import api


class ApiTests(unittest.TestCase):
    def test_job_invokes_internal_module_not_removed_host_wrapper(self):
        args = api.command_arguments(api.OperationRequest(
            command="reprotect", execute=True, allow_recreate=True, reprotect_parallelism=2))
        self.assertEqual(args[:3], [api.sys.executable, "-m", "fsdr_opensearch.cli"])
        self.assertNotIn("fsdr.py", args)
        self.assertIn("--execute", args)
        self.assertIn("--allow-recreate", args)

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
        self.assertIn("Execution console", api.DASHBOARD_HTML)
        self.assertIn("Current environment", api.DASHBOARD_HTML)
        self.assertNotIn("api-token", api.DASHBOARD_HTML)
