"""Tests for persistent research tracking"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.research_plan import ResearchPlan


class ResearchPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tree.sqlite"
        self.store = Checkpoint(self.path)

        for fid, name in [
            ("AAAA-111", "First Person"),
            ("BBBB-222", "Second Person"),
        ]:
            self.store.seed([fid])
            self.store.save_response(
                PERSON_URL + fid,
                {"persons": [{"id": fid, "display": {"name": name}}]},
            )
            self.store.finish(fid, [])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sync_adds_people_without_duplicates(self):
        plan = ResearchPlan(self.store)

        self.assertEqual(plan.sync(), 2)
        self.assertEqual(plan.sync(), 2)
        self.assertEqual(len(plan.items()), 2)

    def test_status_survives_resync(self):
        plan = ResearchPlan(self.store)
        plan.sync()
        plan.set_status("AAAA-111", "done")

        plan.sync()

        items = {item["id"]: item for item in plan.items()}
        self.assertEqual(items["AAAA-111"]["status"], "done")
        self.assertEqual(items["BBBB-222"]["status"], "pending")

    def test_manual_priority_changes_order(self):
        plan = ResearchPlan(self.store)
        plan.sync()
        plan.set_priority("BBBB-222", 0)

        self.assertEqual(plan.items()[0]["id"], "BBBB-222")

    def test_progress_survives_reopening_database(self):
        plan = ResearchPlan(self.store)
        plan.sync()
        plan.set_status("AAAA-111", "postponed")

        self.store.close()
        self.store = Checkpoint(self.path)

        reopened = ResearchPlan(self.store)
        reopened.sync()
        items = {item["id"]: item for item in reopened.items()}

        self.assertEqual(items["AAAA-111"]["status"], "postponed")

    def test_invalid_status_is_rejected(self):
        plan = ResearchPlan(self.store)
        plan.sync()

        with self.assertRaises(ValueError):
            plan.set_status("AAAA-111", "deleted")


if __name__ == "__main__":
    unittest.main()
