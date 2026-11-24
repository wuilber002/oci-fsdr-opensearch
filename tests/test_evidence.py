import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from fsdr_opensearch.core import write_json
from fsdr_opensearch.evidence import stage_and_publish


SECRET = "ocid1.vaultsecret.oc1.eu-frankfurt-1.aaaasecret"


class FakeCloud:
    instances = []

    def __init__(self, timeout):
        self.timeout = timeout
        self.sdk = type("SDK", (), {"retry": type("Retry", (), {"NoneRetryStrategy": staticmethod(lambda: None)})})()
        self.uploaded = []
        FakeCloud.instances.append(self)

    def region(self, secret_id):
        self.secret_id = secret_id
        return "eu-frankfurt-1"

    def object_storage(self, region):
        self.region_name = region
        return self, "namespace"

    def put_object(self, namespace, bucket, name, body, **kwargs):
        self.uploaded.append((namespace, bucket, name, body.read(), kwargs))
        return type("Response", (), {"headers": {}, "data": {}})()

    def _read(self, operation, callback):
        return callback().data


class FailingCloud(FakeCloud):
    def put_object(self, *args, **kwargs):
        raise RuntimeError("object storage unavailable")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        write_json(self.directory / "state.json", {
            "schema": 2, "identity": "pair-identity", "phase": "SERVICE_RESTORED",
            "active": "b", "transition": {"id": "transition-1"}, "indices": [],
        })
        write_json(self.directory / "dr-cycle.json", {
            "schema": 1, "id": "dr-20261124T020415Z-job123", "status": "active", "route": "switchover",
        })
        write_json(self.directory / "workflow.json", {"schema": 1, "events": []})
        write_json(self.directory / "discovery.json", {"schema": 1, "clusters": {}})
        write_json(self.directory / "inventory-health.json", {"outcome": "success"})
        write_json(self.directory / "last-run.json", {"command": "switchover"})
        history = self.directory / "history"
        write_json(history / "evidence-prior.json", {"files": {"state.json": "abc"}})
        # These files must never enter the remote evidence archive.
        write_json(self.directory / "api-secrets" / "api-token", {"secret": "forbidden"})
        (self.directory / "logs").mkdir()
        (self.directory / "logs" / "fsdr-secret.jsonl").write_text("forbidden", encoding="utf-8")

    @patch("fsdr_opensearch.evidence.Cloud", FakeCloud)
    def test_uploads_curated_immutable_bundle_without_credentials(self):
        result = stage_and_publish(self.directory, SECRET, "fsdr-evidence", "evidence", 20,
                                   {"command": "switchover", "outcome": "success"})
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["pending"], 0)
        uploads = FakeCloud.instances[-1].uploaded
        self.assertEqual(len(uploads), 2)
        self.assertTrue(all("/cycles/dr-20261124T020415Z-job123/executions/" in item[2] for item in uploads))
        bundle = next(item[3] for item in uploads if item[2].endswith("bundle.tar.gz"))
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            names = archive.getnames()
        self.assertIn("state.json", names)
        self.assertIn("dr-cycle.json", names)
        self.assertIn("workflow.json", names)
        self.assertIn("history/evidence-prior.json", names)
        self.assertNotIn("api-secrets/api-token", names)
        self.assertNotIn("logs/fsdr-secret.jsonl", names)
        self.assertFalse(list((self.directory / "evidence-outbox").glob("*")))

    @patch("fsdr_opensearch.evidence.Cloud", FakeCloud)
    def test_includes_current_structured_result_without_raw_log(self):
        results = self.directory / "results"
        write_json(results / "job123.json", {"result": {"indices": ["one"]}})
        (self.directory / "logs" / "controller.log").write_text("human log", encoding="utf-8")
        result = stage_and_publish(self.directory, SECRET, "fsdr-evidence", "evidence", 20,
                                   {"command": "reprotect", "outcome": "success",
                                    "result_file": "results/job123.json"})
        self.assertEqual(result["status"], "published")
        bundle = next(item[3] for item in FakeCloud.instances[-1].uploaded if item[2].endswith("bundle.tar.gz"))
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            self.assertIn("results/job123.json", archive.getnames())
            self.assertNotIn("logs/controller.log", archive.getnames())

    @patch("fsdr_opensearch.evidence.Cloud", FakeCloud)
    def test_publishes_readable_controller_log_as_a_separate_object(self):
        log = self.directory / "job.log"
        log.write_text("2026-11-24 INFO validation passed\n", encoding="utf-8")
        result = stage_and_publish(self.directory, SECRET, "fsdr-evidence", "evidence", 20,
                                   {"command": "validate-switchover", "outcome": "success"},
                                   job_log_path=log)
        self.assertEqual(result["status"], "published")
        self.assertTrue(result["controller_log"].endswith("/controller.log"))
        uploads = FakeCloud.instances[-1].uploaded
        self.assertEqual(len(uploads), 3)
        log_upload = next(item for item in uploads if item[2].endswith("/controller.log"))
        self.assertEqual(log_upload[3], b"2026-11-24 INFO validation passed\n")
        bundle = next(item[3] for item in uploads if item[2].endswith("bundle.tar.gz"))
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            self.assertIn("controller.log", archive.getnames())

    @patch("fsdr_opensearch.evidence.Cloud", FailingCloud)
    def test_keeps_bundle_in_private_outbox_when_upload_fails(self):
        result = stage_and_publish(self.directory, SECRET, "fsdr-evidence", "evidence", 20,
                                   {"command": "failover", "outcome": "success"})
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pending"], 1)
        self.assertTrue(list((self.directory / "evidence-outbox").glob("*.manifest.json")))
        self.assertTrue(list((self.directory / "evidence-outbox").glob("*.tar.gz")))

    def test_does_not_create_an_ephemeral_outbox_without_a_bucket(self):
        result = stage_and_publish(self.directory, SECRET, None, "evidence", 20,
                                   {"command": "inventory", "outcome": "success"})
        self.assertEqual(result["status"], "not_configured")
        self.assertFalse((self.directory / "evidence-outbox").exists())

    @patch("fsdr_opensearch.evidence.Cloud", FakeCloud)
    def test_inventory_maintenance_evidence_is_not_misclassified_as_a_dr_cycle(self):
        (self.directory / "dr-cycle.json").unlink()
        result = stage_and_publish(self.directory, SECRET, "fsdr-evidence", "evidence", 20,
                                   {"command": "inventory", "outcome": "success"})
        self.assertEqual(result["status"], "published")
        self.assertIn("/maintenance/", FakeCloud.instances[-1].uploaded[0][2])


if __name__ == "__main__":
    unittest.main()
