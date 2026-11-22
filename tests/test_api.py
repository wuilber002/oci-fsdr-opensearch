"""Container API unit tests without an OCI connection or a running container."""

from pathlib import Path
import asyncio
import subprocess
import sys
import tempfile
import unittest
import json
import time
from unittest.mock import patch

from pydantic import ValidationError

from fsdr_opensearch import api
from fsdr_opensearch.core import Error
from fsdr_opensearch.workflow import record_event, step_for, workflow_snapshot


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
        self.assertIn("Controller job", api.DASHBOARD_HTML)
        self.assertIn("Replicated indices comparison", api.DASHBOARD_HTML)
        self.assertIn("top-nav", api.DASHBOARD_HTML)
        self.assertIn("job-grid", api.DASHBOARD_HTML)
        self.assertIn("clusterLabel", api.DASHBOARD_HTML)
        self.assertIn("statusIcon", api.DASHBOARD_HTML)
        self.assertIn('class="status-cell"', api.DASHBOARD_HTML)
        self.assertIn("HISTORY_PAGE_SIZE=11", api.DASHBOARD_HTML)
        self.assertIn("history-pagination", api.DASHBOARD_HTML)
        self.assertIn("EventSource", api.DASHBOARD_HTML)
        self.assertIn("/dashboard/v1/jobs/'+encodeURIComponent(id)+'/events", api.DASHBOARD_HTML)
        self.assertIn("/dashboard/v1/jobs/events", api.DASHBOARD_HTML)
        self.assertIn("/dashboard/v1/state/events", api.DASHBOARD_HTML)
        self.assertIn("setInterval(refreshComparison,5000)", api.DASHBOARD_HTML)
        self.assertNotIn("setInterval(refresh,5000)", api.DASHBOARD_HTML)
        self.assertIn("topology-unidirectional", api.DASHBOARD_HTML)
        self.assertIn("topology-bidirectional", api.DASHBOARD_HTML)
        self.assertIn("primary-identified", api.DASHBOARD_HTML)
        self.assertIn("primary-unavailable", api.DASHBOARD_HTML)
        self.assertIn("function stateLabel", api.DASHBOARD_HTML)
        self.assertIn("function summaryCard", api.DASHBOARD_HTML)
        self.assertIn("summary-card", api.DASHBOARD_HTML)
        self.assertIn("FSDR workflow", api.DASHBOARD_HTML)
        self.assertIn("workflowCard", api.DASHBOARD_HTML)
        self.assertNotIn("Protected documents", api.DASHBOARD_HTML)
        self.assertNotIn('id="active-job-info"', api.DASHBOARD_HTML)
        self.assertNotIn("api-token", api.DASHBOARD_HTML)

    def test_workflow_gates_enforce_the_audited_switchover_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            now = time.time()
            state = {"phase": "READY"}
            initial = workflow_snapshot(state_dir, state, now=now)
            self.assertTrue(step_for(initial, "inventory")["eligible"])
            self.assertFalse(step_for(initial, "validate-switchover")["eligible"])
            state["inventory_at"] = now

            def event(command, phase, status="succeeded"):
                job = {"id": command, "request": {"command": command}, "status": status,
                       "submitted_at": "2026-01-01T00:00:00Z", "started_at": "2026-01-01T00:00:01Z",
                       "finished_at": "2026-01-01T00:00:02Z", "exit_code": 0}
                record_event(state_dir, job, {**state, "phase": phase})

            event("inventory", "READY")
            event("validate-switchover", "READY")
            prepared = workflow_snapshot(state_dir, state, now=now)
            self.assertTrue(step_for(prepared, "switchover")["eligible"])

            state["phase"] = "SERVICE_RESTORED"
            event("switchover", "SERVICE_RESTORED")
            switched = workflow_snapshot(state_dir, state, now=now)
            self.assertTrue(step_for(switched, "validate-post-switchover")["eligible"])
            self.assertFalse(step_for(switched, "reprotect")["eligible"])

            event("validate-post-switchover", "SERVICE_RESTORED")
            validated = workflow_snapshot(state_dir, state, now=now)
            self.assertTrue(step_for(validated, "reprotect")["eligible"])

    def test_api_workflow_gate_rejects_out_of_order_switchover(self):
        class Manager:
            def recent(self):
                return []

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state"
                state.mkdir()
                (state / "state.json").write_text(json.dumps({"phase": "READY"}), encoding="utf-8")
                config = root / "config.json"
                config.write_text(json.dumps({"state_dir": str(state)}), encoding="utf-8")
                api.CONFIG = config
                with self.assertRaises(api.HTTPException) as rejected:
                    api.workflow_gate(api.OperationRequest(command="switchover"), Manager())
                self.assertEqual(rejected.exception.status_code, 409)
                api.workflow_gate(api.OperationRequest(command="inventory"), Manager())
        finally:
            api.CONFIG = original

    def test_workflow_expired_inventory_blocks_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = workflow_snapshot(Path(directory), {
                "phase": "READY", "inventory_at": time.time() - 3601,
            })
        step = step_for(snapshot, "validate-switchover")
        self.assertEqual(step["status"], "waiting")
        self.assertFalse(step["eligible"])
        self.assertIn("expired", step["requirements"][1]["detail"])

    def test_dashboard_job_log_stream_sends_output_then_end(self):
        class Manager:
            def __init__(self, directory):
                self.directory = Path(directory)

            def read(self, job_id):
                if job_id != "job":
                    raise KeyError(job_id)
                return {"status": "succeeded"}

            def output_path(self, job_id):
                return self.directory / (job_id + ".output")

        async def read_events(manager):
            return [event async for event in api.dashboard_output_stream(manager, "job", 0)]

        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(directory)
            manager.output_path("job").write_text("completed\n", encoding="utf-8")
            events = asyncio.run(read_events(manager))
        self.assertEqual(len(events), 2)
        self.assertIn("event: output", events[0])
        self.assertIn('"completed\\n"', events[0])
        self.assertIn("event: end", events[1])

    def test_dashboard_jobs_stream_announces_current_revision(self):
        class Manager:
            def job_revision(self):
                return 7

            def wait_for_job_change(self, revision, timeout):
                return revision

        async def first_event():
            stream = api.dashboard_jobs_stream(Manager(), 0)
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        event = asyncio.run(first_event())
        self.assertIn("id: 7", event)
        self.assertIn("event: jobs", event)
        self.assertIn('"revision": 7', event)

    def test_dashboard_state_stream_announces_current_state_version(self):
        async def first_event(path):
            stream = api.dashboard_state_stream(path)
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{}", encoding="utf-8")
            event = asyncio.run(first_event(path))
        self.assertIn("event: state", event)
        self.assertIn('"version"', event)

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
            def __init__(self, marked, syncing):
                self.marked = marked
                self.syncing = syncing

            def request(self, method, path):
                assert (method, path) == ("GET", "/_mapping")
                meta = {"fsdr": {"replicate": True}} if self.marked else {}
                return {"orders": {"mappings": {"_meta": meta}}}

            def document_counts(self, scoped=False):
                assert scoped is False
                return {"orders": 11}

            def status(self, index):
                return {"status": "SYNCING" if self.syncing else "REPLICATION NOT IN PROGRESS",
                        "syncing_details": {}}

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
                           return_value=(cfg, {"a": Client(True, False), "b": Client(False, True)})):
                    data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertTrue(data["clusters"]["a"]["indices"][0]["fsdr_replicate"])
        self.assertFalse(data["clusters"]["b"]["indices"][0]["fsdr_replicate"])
        self.assertEqual(data["topology"], "unidirectional")

    def test_monitor_marks_bidirectional_ccr_as_unsupported_topology(self):
        class Client:
            def request(self, method, path):
                return {"orders": {"mappings": {"_meta": {"fsdr": {"replicate": True}}}}}

            def document_counts(self, scoped=False):
                return {"orders": 11}

            def status(self, index):
                return {"status": "SYNCING", "syncing_details": {}}

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state"
                state.mkdir()
                (state / "state.json").write_text(json.dumps({
                    "active": "a", "indices": [{"a": "orders", "b": "orders"}],
                }))
                config = root / "config.json"
                config.write_text(json.dumps({"secret_id": "secret", "state_dir": str(state)}))
                api.CONFIG = config
                cfg = {"clusters": {
                    "a": {"id": "a-id", "display_name": "Alpha"},
                    "b": {"id": "b-id", "display_name": "Beta"},
                }}
                with patch("fsdr_opensearch.api._observer_clients",
                           return_value=(cfg, {"a": Client(), "b": Client()})):
                    data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertEqual(data["topology"], "bidirectional")
        self.assertEqual(data["topology_directions"], ["a->b", "b->a"])

    def test_observer_reports_incomplete_discovery_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "discovery.json"
            cache.write_text(json.dumps({"schema": 1, "secret_id": "secret", "clusters": {}}))
            with self.assertRaisesRegex(Error, "Discovery cache is incomplete"):
                api.read_cache(root, "secret")
