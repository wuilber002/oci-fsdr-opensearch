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
        self.assertIn("/dashboard/v1/events", api.DASHBOARD_HTML)
        self.assertNotIn("/dashboard/v1/monitor", api.DASHBOARD_HTML)
        self.assertIn("Controller job", api.DASHBOARD_HTML)
        self.assertIn("Replicated indices comparison", api.DASHBOARD_HTML)
        self.assertIn("top-nav", api.DASHBOARD_HTML)
        self.assertIn("job-grid", api.DASHBOARD_HTML)
        self.assertIn("clusterLabel", api.DASHBOARD_HTML)
        self.assertIn("statusIcon", api.DASHBOARD_HTML)
        self.assertIn("rejected:'⊘'", api.DASHBOARD_HTML)
        self.assertIn("rejectionReason", api.DASHBOARD_HTML)
        self.assertIn("STATUS_ICON_TEMPLATE", api.DASHBOARD_HTML)
        self.assertIn("POPUP_TEMPLATE", api.DASHBOARD_HTML)
        self.assertIn('id="ui-popup"', api.DASHBOARD_HTML)
        self.assertIn("function connectPopups", api.DASHBOARD_HTML)
        self.assertIn("function popupAttrs", api.DASHBOARD_HTML)
        self.assertIn("data-popup", api.DASHBOARD_HTML)
        self.assertIn("status-icon-glyph", api.DASHBOARD_HTML)
        self.assertIn('class="status-cell"', api.DASHBOARD_HTML)
        self.assertIn('id="log-job-status"', api.DASHBOARD_HTML)
        self.assertIn("function jobTime", api.DASHBOARD_HTML)
        self.assertIn("date.toLocaleString()", api.DASHBOARD_HTML)
        self.assertIn("height:680px", api.DASHBOARD_HTML)
        self.assertIn("HISTORY_PAGE_SIZE=10", api.DASHBOARD_HTML)
        self.assertIn("INDEX_PAGE_SIZE=10", api.DASHBOARD_HTML)
        self.assertIn("function changeIndexPage", api.DASHBOARD_HTML)
        self.assertIn("index-pagination", api.DASHBOARD_HTML)
        self.assertIn("#comparison .card{display:flex;flex-direction:column;min-height:590px}", api.DASHBOARD_HTML)
        self.assertIn(".history-table tr{height:50px}", api.DASHBOARD_HTML)
        self.assertIn("history-pagination", api.DASHBOARD_HTML)
        self.assertIn("EventSource", api.DASHBOARD_HTML)
        self.assertIn("/dashboard/v1/jobs/'+encodeURIComponent(id)+'/events", api.DASHBOARD_HTML)
        self.assertIn("connectDashboardStream", api.DASHBOARD_HTML)
        self.assertIn("event=>{try{applyDashboard(JSON.parse(event.data))}", api.DASHBOARD_HTML)
        self.assertIn("/dashboard/v1/jobs/events", api.DASHBOARD_HTML)
        self.assertIn("connectJobStream", api.DASHBOARD_HTML)
        self.assertNotIn("/dashboard/v1/state/events", api.DASHBOARD_HTML)
        self.assertNotIn("refreshComparison", api.DASHBOARD_HTML)
        self.assertNotIn("fetch(", api.DASHBOARD_HTML)
        self.assertIn("topology-unidirectional", api.DASHBOARD_HTML)
        self.assertIn("topology-bidirectional", api.DASHBOARD_HTML)
        self.assertIn("primary-identified", api.DASHBOARD_HTML)
        self.assertIn("primary-unavailable", api.DASHBOARD_HTML)
        self.assertIn("grid-template-columns:repeat(8,minmax(0,1fr))", api.DASHBOARD_HTML)
        self.assertIn("Replication health", api.DASHBOARD_HTML)
        self.assertIn("Inventory schedule", api.DASHBOARD_HTML)
        self.assertIn("mon.workflow?.inventory?.age_seconds", api.DASHBOARD_HTML)
        self.assertNotIn("mon.workflow?.inventory_age_seconds", api.DASHBOARD_HTML)
        self.assertIn("Protection status", api.DASHBOARD_HTML)
        self.assertIn("Recovery point", api.DASHBOARD_HTML)
        self.assertIn("Cluster health", api.DASHBOARD_HTML)
        self.assertIn("primaryStat(mon)+topologyStat(topology)+stateStat(mon)+replicationStat(mon)", api.DASHBOARD_HTML)
        self.assertIn("function stateLabel", api.DASHBOARD_HTML)
        self.assertIn("function summaryCard", api.DASHBOARD_HTML)
        self.assertIn("summary-card", api.DASHBOARD_HTML)
        self.assertIn("Recovery paths", api.DASHBOARD_HTML)
        self.assertIn("renderRecoveryPaths", api.DASHBOARD_HTML)
        self.assertIn("drawRecoveryConnectors", api.DASHBOARD_HTML)
        self.assertIn("recovery-connectors", api.DASHBOARD_HTML)
        self.assertIn("recovery-connector-blocked", api.DASHBOARD_HTML)
        self.assertIn("recovery-connector-open", api.DASHBOARD_HTML)
        self.assertIn("['succeeded','ready','queued','running'].includes(to.dataset.status)", api.DASHBOARD_HTML)
        self.assertIn(".recovery-node.status-succeeded{border-color:#4ade80}", api.DASHBOARD_HTML)
        self.assertIn("marker#recovery-arrow-open path{fill:#4ade80", api.DASHBOARD_HTML)
        self.assertIn("V '+postStart.y+'", api.DASHBOARD_HTML)
        self.assertIn("isOpen(failover,shared[0])", api.DASHBOARD_HTML)
        self.assertIn("ready-pulse", api.DASHBOARD_HTML)
        self.assertIn("path-name", api.DASHBOARD_HTML)
        self.assertIn("state-service-restored{background:#102a45", api.DASHBOARD_HTML)
        self.assertIn("ACTIVE PATH", api.DASHBOARD_HTML)
        self.assertIn("path-unavailable", api.DASHBOARD_HTML)
        self.assertIn("workflowAttemptTime", api.DASHBOARD_HTML)
        self.assertIn("FLOW_CARD_TEMPLATE", api.DASHBOARD_HTML)
        self.assertIn("function flowCard", api.DASHBOARD_HTML)
        self.assertIn("function inventoryAge", api.DASHBOARD_HTML)
        self.assertIn("function inventoryAgeTone", api.DASHBOARD_HTML)
        self.assertIn("function updateInventoryAges", api.DASHBOARD_HTML)
        self.assertIn("inventory-age", api.DASHBOARD_HTML)
        self.assertIn(".inventory-age.warning", api.DASHBOARD_HTML)
        self.assertIn(".inventory-age.expired", api.DASHBOARD_HTML)
        self.assertIn("--:--:-- --/--/----", api.DASHBOARD_HTML)
        self.assertIn("requirementTooltip", api.DASHBOARD_HTML)
        self.assertNotIn("Protected documents", api.DASHBOARD_HTML)
        self.assertNotIn("Planned switchover workflow", api.DASHBOARD_HTML)
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
            switchover_step = step_for(prepared, "switchover")
            self.assertTrue(switchover_step["eligible"])
            self.assertTrue(all(item.get("explanation") for item in switchover_step["requirements"]))
            self.assertEqual(step_for(prepared, "inventory")["last_attempt_at"], "2026-01-01T00:00:02Z")

            state["phase"] = "SERVICE_RESTORED"
            event("switchover", "SERVICE_RESTORED")
            switched = workflow_snapshot(state_dir, state, now=now)
            self.assertEqual(step_for(switched, "switchover")["status"], "succeeded")
            self.assertEqual(switched["selected_path"], "switchover")
            self.assertTrue(next(path for path in switched["paths"] if path["id"] == "switchover")["active"])
            self.assertTrue(next(path for path in switched["paths"] if path["id"] == "failover")["unavailable"])
            self.assertTrue(step_for(switched, "validate-post-switchover")["eligible"])
            self.assertFalse(step_for(switched, "reprotect")["eligible"])

            event("validate-post-switchover", "SERVICE_RESTORED")
            validated = workflow_snapshot(state_dir, state, now=now)
            self.assertTrue(step_for(validated, "reprotect")["eligible"])
            state["phase"] = "PROTECTION_RESTORED"
            reset = workflow_snapshot(state_dir, state, now=now)
            self.assertIsNone(reset["selected_path"])
            self.assertTrue(reset["paths_resettable"])
            self.assertFalse(any(path["unavailable"] for path in reset["paths"]))

    def test_successful_reprotect_resets_the_visual_workflow_cycle(self):
        """A restored protection state starts the next path from inventory.

        The ledger keeps the preceding operations for audit, but their green
        cards/connectors must not be projected as the path for the next
        recovery cycle.
        """
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            now = time.time()
            state = {"phase": "PROTECTION_RESTORED", "inventory_at": now,
                     "indices": [{"name": "orders"}]}

            for sequence, command in enumerate((
                    "inventory", "validate-switchover", "switchover",
                    "validate-post-switchover", "reprotect"), start=1):
                timestamp = "2026-01-01T00:00:{:02d}Z".format(sequence)
                record_event(state_dir, {
                    "id": "{}-{}".format(command, sequence),
                    "request": {"command": command}, "status": "succeeded",
                    "submitted_at": timestamp, "started_at": timestamp,
                    "finished_at": timestamp, "exit_code": 0,
                }, state)

            snapshot = workflow_snapshot(state_dir, state, now=now)
            self.assertEqual(snapshot["cycle_reset_at"], "2026-01-01T00:00:05Z")
            self.assertEqual(step_for(snapshot, "inventory")["status"], "succeeded")
            self.assertEqual(step_for(snapshot, "inventory")["last_attempt_at"], "2026-01-01T00:00:01Z")
            self.assertEqual(step_for(snapshot, "validate-switchover")["status"], "ready")
            self.assertEqual(step_for(snapshot, "switchover")["status"], "waiting")
            self.assertIsNone(snapshot["selected_path"])
            self.assertFalse(any(path["unavailable"] for path in snapshot["paths"]))
            self.assertEqual(len(snapshot["events"]), 5)

    def test_reprotect_keeps_completed_path_visible_for_thirty_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            finished_at = 2_000_000_000
            state = {"phase": "PROTECTION_RESTORED", "inventory_at": finished_at,
                     "indices": [{"name": "orders"}]}
            for sequence, command in enumerate((
                    "inventory", "validate-switchover", "switchover",
                    "validate-post-switchover", "reprotect"), start=1):
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
                    finished_at - 5 + sequence))
                record_event(state_dir, {
                    "id": "{}-{}".format(command, sequence),
                    "request": {"command": command}, "status": "succeeded",
                    "submitted_at": timestamp, "started_at": timestamp,
                    "finished_at": timestamp, "exit_code": 0,
                }, state)

            pending = workflow_snapshot(state_dir, state, now=finished_at + 5)
            self.assertEqual(step_for(pending, "reprotect")["status"], "succeeded")
            self.assertEqual(pending["selected_path"], "switchover")
            self.assertFalse(pending["paths_resettable"])
            self.assertEqual(pending["cycle_reset_pending_until"], finished_at + 30)

            reset = workflow_snapshot(state_dir, state, now=finished_at + 30.1)
            self.assertEqual(step_for(reset, "inventory")["status"], "succeeded")
            self.assertIsNone(reset["selected_path"])
            self.assertTrue(reset["paths_resettable"])
            self.assertIsNone(reset["cycle_reset_pending_until"])

    def test_new_cycle_does_not_project_previous_reprotect_as_succeeded(self):
        """A former recovery must not complete reprotect in a later transition."""
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            state = {"phase": "SERVICE_RESTORED", "inventory_at": 2_000_000_020,
                     "indices": [{"name": "orders"}]}

            def event(command, second):
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(second))
                record_event(state_dir, {
                    "id": "{}-{}".format(command, second),
                    "request": {"command": command}, "status": "succeeded",
                    "submitted_at": timestamp, "started_at": timestamp,
                    "finished_at": timestamp, "exit_code": 0,
                }, state)

            # Completed old cycle.
            for offset, command in enumerate((
                    "inventory", "validate-switchover", "switchover",
                    "validate-post-switchover", "reprotect")):
                event(command, 2_000_000_000 + offset)
            # New planned transition and its post-transition validation.
            for offset, command in enumerate((
                    "inventory", "validate-switchover", "switchover",
                    "validate-post-switchover")):
                event(command, 2_000_000_020 + offset)

            snapshot = workflow_snapshot(state_dir, state, now=2_000_000_030)
            self.assertEqual(step_for(snapshot, "validate-post-switchover")["status"], "succeeded")
            self.assertEqual(step_for(snapshot, "reprotect")["status"], "ready")
            self.assertTrue(step_for(snapshot, "reprotect")["eligible"])
            self.assertEqual(snapshot["selected_path"], "switchover")

    def test_new_inventory_invalidates_a_previous_switchover_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            initial_inventory = 2_000_000_000
            refreshed_inventory = initial_inventory + 20 * 60
            state = {"phase": "PROTECTION_RESTORED", "inventory_at": refreshed_inventory,
                     "indices": [{"name": "orders"}]}

            def event(command, timestamp, inventory_at):
                value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
                record_event(state_dir, {
                    "id": "{}-{}".format(command, timestamp),
                    "request": {"command": command}, "status": "succeeded",
                    "submitted_at": value, "started_at": value,
                    "finished_at": value, "exit_code": 0,
                }, {"phase": "PROTECTION_RESTORED", "inventory_at": inventory_at})

            event("inventory", initial_inventory, initial_inventory)
            event("validate-switchover", initial_inventory + 1, initial_inventory)
            event("inventory", refreshed_inventory, refreshed_inventory)

            snapshot = workflow_snapshot(state_dir, state, now=refreshed_inventory + 60)
            validation = step_for(snapshot, "validate-switchover")
            self.assertEqual(step_for(snapshot, "inventory")["status"], "succeeded")
            self.assertEqual(validation["status"], "ready")
            self.assertTrue(validation["eligible"])
            self.assertIn("newer inventory", validation["status_detail"])
            self.assertFalse(step_for(snapshot, "switchover")["eligible"])

    def test_fresh_inventory_remains_usable_after_a_completed_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            now = 2_000_000_000
            state = {"phase": "PROTECTION_RESTORED", "inventory_at": now - 30 * 60,
                     "indices": [{"name": "orders"}]}
            for sequence, command in enumerate((
                    "inventory", "validate-switchover", "switchover",
                    "validate-post-switchover", "reprotect"), start=1):
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60 + sequence))
                record_event(state_dir, {
                    "id": "{}-{}".format(command, sequence),
                    "request": {"command": command}, "status": "succeeded",
                    "submitted_at": timestamp, "started_at": timestamp,
                    "finished_at": timestamp, "exit_code": 0,
                }, state)
            snapshot = workflow_snapshot(state_dir, state, now=now)
            inventory = step_for(snapshot, "inventory")
            validation = step_for(snapshot, "validate-switchover")
            self.assertEqual(inventory["status"], "succeeded")
            self.assertTrue(validation["eligible"])
            self.assertTrue(snapshot["inventory"]["fresh"])
            self.assertEqual(snapshot["inventory"]["age_seconds"], 30 * 60)

    def test_dashboard_inventory_age_uses_the_last_explicit_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            inventory_at = 2_000_000_000
            inventory_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(inventory_at))
            record_event(state_dir, {
                "id": "inventory", "request": {"command": "inventory"},
                "status": "succeeded", "submitted_at": inventory_time,
                "started_at": inventory_time, "finished_at": inventory_time,
                "exit_code": 0,
            }, {"phase": "READY", "inventory_at": inventory_at})
            # Compatibility case: an older reprotect wrote its completion time
            # into state.inventory_at. The counter must still show inventory.
            snapshot = workflow_snapshot(state_dir, {
                "phase": "PROTECTION_RESTORED", "inventory_at": inventory_at + 600,
                "indices": [{"name": "orders"}],
            }, now=inventory_at + 600)
            self.assertEqual(snapshot["inventory_at"], inventory_at)

    def test_completed_inventory_does_not_lose_a_gate_while_validation_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            inventory_at = 2_000_000_000
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(inventory_at))
            record_event(state_dir, {
                "id": "inventory-success", "request": {"command": "inventory"},
                "status": "succeeded", "submitted_at": timestamp,
                "started_at": timestamp, "finished_at": timestamp, "exit_code": 0,
                "workflow_evidence": {"requirements": [{
                    "key": "stable-state", "met": True, "detail": "FSDR was READY.",
                    "explanation": "The step was admitted from a stable state.", "level": "waiting",
                }]},
            }, {"phase": "PROTECTION_RESTORED", "inventory_at": inventory_at})
            validation = {"id": "validation-running", "request": {"command": "validate-switchover"},
                          "status": "running", "submitted_at": timestamp,
                          "workflow_evidence": {"requirements": [{
                              "key": "fresh-inventory", "met": True, "detail": "Inventory is fresh.",
                              "explanation": "A fresh inventory is required.", "level": "waiting",
                          }]}}
            snapshot = workflow_snapshot(state_dir, {
                "phase": "PROTECTION_RESTORED", "inventory_at": inventory_at,
            }, jobs=[validation], now=inventory_at + 60)
            inventory = step_for(snapshot, "inventory")
        self.assertEqual(inventory["status"], "succeeded")
        self.assertNotIn("controller-idle", [gate["key"] for gate in inventory["requirements"]])
        self.assertTrue(all(gate["met"] for gate in inventory["requirements"]))
        running_inventory = {"id": "inventory-running", "request": {"command": "inventory"},
                             "status": "running", "submitted_at": timestamp}
        running_snapshot = workflow_snapshot(state_dir, {
            "phase": "PROTECTION_RESTORED", "inventory_at": inventory_at,
        }, jobs=[running_inventory], now=inventory_at + 60)
        self.assertEqual(len(step_for(running_snapshot, "inventory")["requirements"]),
                         len(inventory["requirements"]))

    def test_completed_step_preserves_its_admission_gates_after_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            job = {
                "id": "inventory-success", "request": {"command": "inventory"},
                "status": "succeeded", "submitted_at": "2026-01-01T00:00:00Z",
                "started_at": "2026-01-01T00:00:01Z", "finished_at": "2026-01-01T00:00:02Z",
                "workflow_evidence": {"captured_at": "2026-01-01T00:00:01Z", "requirements": [{
                    "key": "stable-state", "met": True, "detail": "FSDR was READY.",
                    "explanation": "The step was admitted from a stable state.", "level": "waiting",
                }]},
            }
            record_event(state_dir, job, {"phase": "SERVICE_RESTORED"})
            snapshot = workflow_snapshot(state_dir, {"phase": "SERVICE_RESTORED"})
            inventory = step_for(snapshot, "inventory")
        self.assertEqual(inventory["status"], "succeeded")
        self.assertTrue(inventory["requirements"][0]["met"])
        self.assertEqual(inventory["requirements"][0]["detail"], "FSDR was READY.")

    def test_running_step_preserves_its_admission_gates_after_phase_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            running = {
                "id": "reprotect-running", "request": {"command": "reprotect"},
                "status": "running", "submitted_at": "2026-01-01T00:00:00Z",
                "workflow_evidence": {"requirements": [{
                    "key": "service-restored", "met": True,
                    "detail": "FSDR state was SERVICE_RESTORED.",
                    "explanation": "The step was admitted before phase transition.", "level": "waiting",
                }]},
            }
            snapshot = workflow_snapshot(state_dir, {"phase": "REPROTECTING"}, jobs=[running])
            reprotect = step_for(snapshot, "reprotect")
        self.assertEqual(reprotect["status"], "running")
        self.assertTrue(reprotect["requirements"][0]["met"])
        self.assertEqual(reprotect["requirements"][0]["detail"], "FSDR state was SERVICE_RESTORED.")

    def test_legacy_running_step_is_not_repainted_after_phase_change(self):
        with tempfile.TemporaryDirectory() as directory:
            running = {"id": "legacy-reprotect", "request": {"command": "reprotect"},
                       "status": "running", "submitted_at": "2026-01-01T00:00:00Z"}
            snapshot = workflow_snapshot(Path(directory), {"phase": "REPROTECTING"}, jobs=[running])
            reprotect = step_for(snapshot, "reprotect")
        self.assertTrue(all(item["met"] for item in reprotect["requirements"]))

    def test_stale_inventory_is_not_frozen_before_switchover_starts(self):
        class Manager:
            def recent(self):
                return []

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state_dir = root / "state"
                state_dir.mkdir()
                state = {"phase": "READY", "inventory_at": time.time() - 3601}
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                for command in ("inventory", "validate-switchover"):
                    record_event(state_dir, {
                        "id": command, "request": {"command": command}, "status": "succeeded",
                        "submitted_at": timestamp, "started_at": timestamp,
                        "finished_at": timestamp, "exit_code": 0,
                    }, state)
                snapshot = workflow_snapshot(state_dir, state)
                self.assertEqual(snapshot["inventory_at"], state["inventory_at"])
                self.assertEqual(step_for(snapshot, "inventory")["status"], "ready")
                self.assertEqual(step_for(snapshot, "validate-switchover")["status"], "waiting")
                switchover = step_for(snapshot, "switchover")
                self.assertEqual(switchover["status"], "waiting")
                self.assertFalse(switchover["eligible"])
                self.assertFalse(next(item for item in switchover["requirements"]
                                      if item["key"] == "fresh-inventory")["met"])

                (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
                config = root / "config.json"
                config.write_text(json.dumps({"state_dir": str(state_dir)}), encoding="utf-8")
                api.CONFIG = config
                with self.assertRaises(api.HTTPException) as rejected:
                    api.workflow_gate(api.OperationRequest(command="switchover"), Manager())
                self.assertEqual(rejected.exception.status_code, 409)

                transitioned = {**state, "phase": "SERVICE_RESTORED"}
                record_event(state_dir, {
                    "id": "switchover", "request": {"command": "switchover"}, "status": "succeeded",
                    "submitted_at": timestamp, "started_at": timestamp,
                    "finished_at": timestamp, "exit_code": 0,
                }, transitioned)
                frozen = workflow_snapshot(state_dir, transitioned)
                self.assertEqual(step_for(frozen, "inventory")["status"], "succeeded")
                self.assertEqual(step_for(frozen, "validate-switchover")["status"], "succeeded")
        finally:
            api.CONFIG = original

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

    def test_rejected_submission_is_persisted_for_dashboard_audit(self):
        class Manager:
            def __init__(self):
                self.rejection = None

            def recent(self):
                return []

            def reject(self, request, rejection):
                self.rejection = rejection
                return {"id": "rejected-job", "finished_at": "2026-01-01T00:00:00Z"}

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
                manager = Manager()
                with patch.object(api, "api_manager", return_value=manager):
                    with self.assertRaises(api.HTTPException) as rejected:
                        api.submit(api.OperationRequest(command="switchover"))
                self.assertEqual(rejected.exception.status_code, 409)
                self.assertEqual(rejected.exception.detail["job_id"], "rejected-job")
                self.assertEqual(manager.rejection["code"], "workflow_prerequisites_unmet")
        finally:
            api.CONFIG = original

    def test_rejected_job_has_a_human_readable_audit_log(self):
        original = api.JOB_DIRECTORY
        try:
            with tempfile.TemporaryDirectory() as directory:
                api.JOB_DIRECTORY = Path(directory)
                manager = api.JobManager()
                job = manager.reject(api.OperationRequest(command="switchover"), {
                    "code": "workflow_prerequisites_unmet",
                    "requirements": [{"key": "inventory", "met": False,
                                      "detail": "No fresh inventory is available.",
                                      "explanation": "Run inventory before a planned switchover."}],
                })
                self.assertEqual(job["status"], "rejected")
                self.assertEqual(manager.recent()[0]["id"], job["id"])
                output = manager.output_path(job["id"]).read_text(encoding="utf-8")
                self.assertIn("rejected before execution", output)
                self.assertIn("No fresh inventory", output)
        finally:
            api.JOB_DIRECTORY = original

    def test_api_automatically_resumes_pending_failover_for_plain_command(self):
        class Manager:
            def __init__(self):
                self.requests = []

            def recent(self):
                return []

            def submit(self, request):
                self.requests.append(request)
                return {"id": "resume-job", "status": "queued", "submitted_at": "now"}

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                state_dir = Path(directory) / "state"
                state_dir.mkdir()
                (state_dir / "state.json").write_text(json.dumps({
                    "phase": "SERVICE_RESTORED",
                    "transition": {"mode": "failover", "steps": ["stop_followers", "enable_target"]},
                }), encoding="utf-8")
                config = Path(directory) / "config.json"
                config.write_text(json.dumps({"state_dir": str(state_dir)}), encoding="utf-8")
                api.CONFIG = config
                manager = Manager()
                with patch.object(api, "api_manager", return_value=manager):
                    response = api.submit(api.OperationRequest(command="failover"))
                self.assertEqual(response["id"], "resume-job")
                self.assertTrue(manager.requests[0].resume)
                self.assertIn("--resume", api.command_arguments(manager.requests[0]))
        finally:
            api.CONFIG = original

    def test_api_does_not_resume_a_completed_fenced_failover(self):
        request = api.OperationRequest(command="failover")
        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                state_dir = Path(directory) / "state"
                state_dir.mkdir()
                (state_dir / "state.json").write_text(json.dumps({
                    "phase": "SERVICE_RESTORED",
                    "transition": {"mode": "failover", "steps": ["fence_source", "stop_followers", "enable_target"]},
                }), encoding="utf-8")
                config = Path(directory) / "config.json"
                config.write_text(json.dumps({"state_dir": str(state_dir)}), encoding="utf-8")
                api.CONFIG = config
                self.assertFalse(api.automatically_resume_failover(request).resume)
        finally:
            api.CONFIG = original

    def test_workflow_gate_leaves_explicit_resume_to_the_controller(self):
        class Manager:
            def recent(self):
                return []

        api.workflow_gate(api.OperationRequest(command="switchover", resume=True), Manager())

    def test_failover_requires_explicit_risk_attestations_but_not_fresh_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            state = {"phase": "READY", "inventory_at": time.time() - 7200,
                     "indices": [{"a": "orders", "b": "orders"}]}
            blocked = workflow_snapshot(state_dir, state, candidate={})
            self.assertFalse(step_for(blocked, "failover")["eligible"])
            accepted = workflow_snapshot(state_dir, state, candidate={
                "source_fenced": True, "accept_data_loss": True,
                "use_follower_annotations": True,
            })
            self.assertTrue(step_for(accepted, "failover")["eligible"])
            self.assertIn("failover", [step["command"] for step in accepted["steps"]])
            self.assertEqual([path["id"] for path in accepted["paths"]],
                             ["switchover", "failover", "shared"])
            queued = workflow_snapshot(state_dir, state, jobs=[{
                "request": {"command": "failover"}, "status": "queued",
                "submitted_at": "2026-01-01T00:00:00Z",
            }])
            self.assertEqual(queued["selected_path"], "failover")
            self.assertTrue(next(path for path in queued["paths"] if path["id"] == "switchover")["unavailable"])

    def test_expired_inventory_is_visible_but_can_be_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = workflow_snapshot(Path(directory), {
                "phase": "READY", "inventory_at": time.time() - 3601,
            })
        inventory = step_for(snapshot, "inventory")
        freshness = next(gate for gate in inventory["requirements"] if gate["key"] == "fresh-inventory")
        self.assertEqual(inventory["status"], "ready")
        self.assertTrue(inventory["eligible"])
        self.assertFalse(freshness["met"])
        self.assertIn("expired", freshness["detail"])

    def test_workflow_expired_inventory_blocks_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = workflow_snapshot(Path(directory), {
                "phase": "READY", "inventory_at": time.time() - 3601,
            })
        step = step_for(snapshot, "validate-switchover")
        self.assertEqual(step["status"], "waiting")
        self.assertFalse(step["eligible"])
        expired_gate = next(gate for gate in step["requirements"] if gate["key"] == "fresh-inventory")
        self.assertIn("expired", expired_gate["detail"])

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

    def test_dashboard_job_log_stream_ends_for_rejected_job(self):
        class Manager:
            def __init__(self, directory):
                self.directory = Path(directory)

            def read(self, job_id):
                return {"status": "rejected"}

            def output_path(self, job_id):
                return self.directory / (job_id + ".output")

        async def read_events(manager):
            return [event async for event in api.dashboard_output_stream(manager, "job", 0)]

        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(directory)
            manager.output_path("job").write_text("rejected\n", encoding="utf-8")
            events = asyncio.run(read_events(manager))
        self.assertEqual(len(events), 2)
        self.assertIn('"rejected"', events[1])

    def test_dashboard_snapshot_payload_contains_monitor_and_job_history(self):
        class Manager:
            def recent(self):
                return [{"id": "job", "status": "running"}]

        with patch.object(api, "monitor_snapshot", return_value={"phase": "READY"}):
            payload = api.dashboard_snapshot_payload(Manager())
        self.assertEqual(payload["monitor"]["phase"], "READY")
        self.assertEqual(payload["jobs"]["jobs"][0]["id"], "job")

    def test_dashboard_jobs_stream_announces_current_revision(self):
        class Manager:
            def job_revision(self):
                return 7

            def recent(self):
                return [{"id": "job", "status": "queued"}]

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
        self.assertIn('"jobs": [{"id": "job"', event)

    def test_dashboard_jobs_stream_does_not_swallow_the_next_job_revision(self):
        class Manager:
            def __init__(self):
                self.revision = 0
                self.waits = 0

            def job_revision(self):
                return self.revision

            def recent(self):
                return [{"id": "job", "status": "running" if self.revision else "queued"}]

            def wait_for_job_change(self, revision, timeout):
                self.waits += 1
                if self.waits == 1:
                    self.revision = 1
                    return self.revision
                raise AssertionError("the changed revision must be published before waiting again")

        async def two_events(manager):
            stream = api.dashboard_jobs_stream(manager, 0)
            try:
                return await anext(stream), await anext(stream)
            finally:
                await stream.aclose()

        async def direct_call(function, *args):
            return function(*args)

        manager = Manager()
        with patch("fsdr_opensearch.api.current_workflow", return_value={"steps": []}), \
                patch("fsdr_opensearch.api.asyncio.to_thread", new=direct_call):
            initial, changed = asyncio.run(two_events(manager))
        self.assertIn('"status": "queued"', initial)
        self.assertIn("id: 1", changed)
        self.assertIn('"status": "running"', changed)
        self.assertEqual(manager.waits, 1)

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

    def test_effective_write_state_uses_all_authoritative_gates(self):
        healthy = {"status": "green", "active_primary_shards": 1}
        self.assertEqual(api._effective_write_state({}, None, True, healthy, None, False)[0], "read_write")
        self.assertEqual(api._effective_write_state({}, "cluster.blocks.read_only", True, healthy, None, False)[0], "read_only")
        self.assertEqual(api._effective_write_state({"index.blocks.metadata": "true"}, None, True, healthy, None, False)[0], "read_write")
        self.assertEqual(api._effective_write_state({"index.blocks.write": "true"}, None, True, healthy, None, False)[0], "read_only")
        self.assertEqual(api._effective_write_state({}, None, True, healthy, "SYNCING", True)[0], "read_only")
        self.assertEqual(api._effective_write_state({}, None, True, {"status": "red", "active_primary_shards": 0}, None, False)[0], "unavailable")
        self.assertEqual(api._effective_write_state({}, None, False, healthy, None, False)[0], "unknown")
        self.assertEqual(api._effective_write_state(None, None, True, healthy, None, False)[0], "unknown")

    def test_monitor_exposes_inventory_schedule_and_cluster_health(self):
        class Client:
            def request(self, method, path):
                if path == "/_cluster/health":
                    return {"status": "green"}
                if path == "/_mapping":
                    return {"orders": {"mappings": {"_meta": {"fsdr": {"replicate": True}}}}}
                if path == "/_settings?flat_settings=true":
                    return {"orders": {"settings": {}}}
                if path == "/_cluster/settings?include_defaults=true&flat_settings=true":
                    return {"defaults": {}, "persistent": {}, "transient": {}}
                if path == "/_cluster/health?level=indices":
                    return {"indices": {"orders": {"status": "green", "active_primary_shards": 1}}}
                raise AssertionError((method, path))

            def document_counts(self, scoped=False):
                return {"orders": 2}

            def status(self, index):
                return {"status": "SYNCING", "syncing_details": {"leader_checkpoint": 10, "follower_checkpoint": 10}}

        original = api.CONFIG
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state_dir = root / "state"
                state_dir.mkdir()
                now = time.time()
                (state_dir / "state.json").write_text(json.dumps({"active": "a", "phase": "PROTECTION_RESTORED", "inventory_at": now, "indices": [{"a": "orders", "b": "orders"}]}))
                (state_dir / "inventory-health.json").write_text(json.dumps({"outcome": "success", "duration_seconds": 3.2, "last_success_at": now}))
                config = root / "config.json"
                config.write_text(json.dumps({"secret_id": "secret", "state_dir": str(state_dir), "inventory_enabled": True, "inventory_interval_hours": 2}))
                api.CONFIG = config
                cfg = {"clusters": {"a": {"id": "a", "display_name": "Alpha"}, "b": {"id": "b", "display_name": "Beta"}}}
                with patch("fsdr_opensearch.api._observer_clients", return_value=(cfg, {"a": Client(), "b": Client()})):
                    data = api.monitor_snapshot()
        finally:
            api.CONFIG = original
        self.assertEqual(data["inventory_schedule"]["last"]["duration_seconds"], 3.2)
        self.assertTrue(data["inventory_schedule"]["enabled"])
        self.assertEqual(data["inventory_schedule"]["interval_seconds"], 7200)
        self.assertEqual(data["clusters"]["a"]["health"], "green")

    def test_monitor_reports_the_live_fsdr_replication_marker(self):
        class Client:
            def __init__(self, marked, syncing, write_blocked=False):
                self.marked = marked
                self.syncing = syncing
                self.write_blocked = write_blocked

            def request(self, method, path):
                if (method, path) == ("GET", "/_cluster/health"):
                    return {"status": "green"}
                if (method, path) == ("GET", "/_mapping"):
                    meta = {"fsdr": {"replicate": True}} if self.marked else {}
                    return {"orders": {"mappings": {"_meta": meta}}}
                if (method, path) == ("GET", "/_settings?flat_settings=true"):
                    return {"orders": {"settings": {"index.blocks.write": str(self.write_blocked).lower()}}}
                if (method, path) == ("GET", "/_cluster/settings?include_defaults=true&flat_settings=true"):
                    return {"defaults": {}, "persistent": {}, "transient": {}}
                assert (method, path) == ("GET", "/_cluster/health?level=indices")
                return {"indices": {"orders": {"status": "green", "active_primary_shards": 1}}}

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
        self.assertEqual(data["clusters"]["a"]["indices"][0]["write_mode"], "read_write")
        self.assertEqual(data["clusters"]["b"]["indices"][0]["write_mode"], "read_only")
        self.assertIn("CCR follower in SYNCING", data["clusters"]["b"]["indices"][0]["write_detail"])
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
