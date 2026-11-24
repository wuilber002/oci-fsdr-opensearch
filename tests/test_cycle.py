import tempfile
import time
from pathlib import Path
import unittest

from fsdr_opensearch.workflow import (bind_cycle, invalidate_expired_planned_cycle,
                                      read_cycle, record_cycle_outcome)


def job(job_id, command, *, scheduled=False, status="succeeded"):
    return {
        "id": job_id,
        "submitted_at": "2026-11-24T02:04:15Z",
        "finished_at": "2026-11-24T02:04:30Z",
        "status": status,
        "request": {"command": command, "scheduled": scheduled},
    }


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name)
        self.now = time.time()
        self.state = {"phase": "READY", "inventory_at": self.now}

    def test_scheduled_inventory_never_creates_a_cycle(self):
        self.assertIsNone(bind_cycle(self.state_dir, self.state, job("scheduled", "inventory", scheduled=True)))
        self.assertIsNone(read_cycle(self.state_dir))

    def test_rejected_request_can_join_but_not_start_a_cycle(self):
        rejected = job("rejected", "switchover")
        self.assertIsNone(bind_cycle(self.state_dir, self.state, rejected, allow_start=False))
        active = bind_cycle(self.state_dir, self.state, job("initial", "inventory"))
        self.assertIsNotNone(active)
        self.assertEqual(bind_cycle(self.state_dir, self.state, rejected, allow_start=False)["id"],
                         active["id"])

    def test_planned_steps_share_one_cycle_until_reprotect(self):
        first = job("a" * 32, "inventory")
        cycle = bind_cycle(self.state_dir, self.state, first)
        first["cycle_id"] = cycle["id"]
        self.assertEqual(cycle["id"], "dr-20261124T020415Z-aaaaaaaaaaaa")
        self.assertEqual(cycle["route"], "switchover")

        validate = job("b" * 32, "validate-switchover")
        self.assertEqual(bind_cycle(self.state_dir, self.state, validate)["id"], cycle["id"])
        validate["cycle_id"] = cycle["id"]
        refreshed_at = self.now + 100
        record_cycle_outcome(self.state_dir, first, {"phase": "READY", "inventory_at": refreshed_at})
        self.assertEqual(read_cycle(self.state_dir)["inventory_expires_at"], refreshed_at + 3_600)

        reprotect = job("c" * 32, "reprotect")
        reprotect["cycle_id"] = cycle["id"]
        record_cycle_outcome(self.state_dir, reprotect, {"phase": "PROTECTION_RESTORED"})
        self.assertEqual(read_cycle(self.state_dir)["status"], "completed")

    def test_expired_planned_cycle_is_invalidated_before_new_cycle(self):
        initial = bind_cycle(self.state_dir, self.state, job("a" * 32, "inventory"))
        tracked = job("a" * 32, "inventory")
        tracked["cycle_id"] = initial["id"]
        record_cycle_outcome(self.state_dir, tracked, self.state)
        invalidate_expired_planned_cycle(self.state_dir, self.state, now=self.now + 3_601)
        self.assertEqual(read_cycle(self.state_dir)["status"], "invalidated")
        replacement = bind_cycle(self.state_dir, self.state, job("b" * 32, "inventory"))
        self.assertNotEqual(replacement["id"], initial["id"])
        self.assertEqual(replacement["status"], "active")

    def test_failover_cycle_has_no_inventory_expiry_and_ends_only_after_reprotect(self):
        cycle = bind_cycle(self.state_dir, self.state, job("f" * 32, "failover"))
        self.assertEqual(cycle["route"], "failover")
        self.assertIsNone(cycle["inventory_expires_at"])
        self.assertEqual(invalidate_expired_planned_cycle(self.state_dir, self.state, now=time.time() + 86400)["status"], "active")
        completion = job("r" * 32, "reprotect")
        completion["cycle_id"] = cycle["id"]
        record_cycle_outcome(self.state_dir, completion, {"phase": "PROTECTION_RESTORED"})
        self.assertEqual(read_cycle(self.state_dir)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
