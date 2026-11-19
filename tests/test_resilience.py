import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fsdr_opensearch.resilience import readiness, rpo, snapshots
from fsdr_opensearch.reporting import record_evidence
from fsdr_opensearch.core import Store, write_json


def state():
    return {"schema": 2, "identity": "pair", "phase": "PROTECTION_RESTORED", "active": "a",
            "inventory_at": 1000, "indices": [{"a": "orders", "b": "orders-copy",
            "replication": {"status": "SYNCING", "leader_checkpoint": 10, "follower_checkpoint": 10}}]}


class SnapshotClient:
    def __init__(self, latest=True):
        self.latest = latest

    def status(self, index):
        return {"status": "SYNCING", "syncing_details": {"leader_checkpoint": 10, "follower_checkpoint": 10}}

    def request(self, method, path):
        if path.startswith("/_snapshot/repo") and "_all" not in path:
            return {"repo": {"type": "s3"}}
        if self.latest:
            return {"snapshots": [{"snapshot": "s1", "state": "SUCCESS", "end_time_in_millis": 1_000_000,
                                   "indices": ["orders", "orders-copy"]}]}
        return {"snapshots": []}


class ResilienceTests(unittest.TestCase):
    def cfg(self):
        return {"max_checkpoint_lag": 0, "enforce_rpo": True, "snapshot_repositories": {"a": "repo", "b": "repo"},
                "max_snapshot_age_hours": 1, "snapshot_required": True, "max_inventory_age_seconds": 3600}

    def test_rpo_uses_checkpoints_and_enforcement(self):
        data = state()
        data["indices"][0]["replication"]["follower_checkpoint"] = 8
        result = rpo(self.cfg(), data)
        self.assertEqual((result["status"], result["max_lag_operations"]), ("exceeded", 2))

    @patch("fsdr_opensearch.resilience.time.time", return_value=1001)
    def test_readiness_checks_snapshot_age_and_manifest_coverage(self, _clock):
        result = readiness(self.cfg(), state(), {"a": SnapshotClient(), "b": SnapshotClient()})
        self.assertTrue(result["ready"])
        self.assertEqual(result["snapshots"]["status"], "ready")

    @patch("fsdr_opensearch.resilience.time.time", return_value=1001)
    def test_required_bad_snapshot_blocks_readiness(self, _clock):
        result = snapshots(self.cfg(), state(), {"a": SnapshotClient(False), "b": SnapshotClient(False)})
        self.assertEqual(result["status"], "not_ready")

    def test_evidence_hashes_final_state_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            Store({"state_dir": directory, "state_schema": 2, "identity": "pair"}).write(state())
            write_json(Path(directory) / "last-run.json", {"outcome": "success"})
            result = record_evidence(directory, "switchover", {"outcome": "success"})
            self.assertEqual(result["command"], "switchover")
            self.assertIn("state.json", result["files"])
            self.assertTrue(list((Path(directory) / "history").glob("evidence-*.json")))


