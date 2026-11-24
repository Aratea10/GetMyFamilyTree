"""Tests for manual genealogy research organization"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_plan import ResearchPlan


class ResearchPlannerFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "research.sqlite"
        self.store = Checkpoint(self.path)

        self.store.set_origin("SELF-111")

        people = {
            "SELF-111": "Unknown",
            "FATH-111": "Male",
            "MOTH-111": "Female",
            "OTHR-111": "Unknown",
        }

        for fid, gender in people.items():
            self.store.seed([fid])
            self.store.save_response(
                PERSON_URL + fid,
                {
                    "persons": [
                        {
                            "id": fid,
                            "display": {"name": fid},
                            "gender": {"type": f"http://gedcomx.org/{gender}"},
                        }
                    ]
                },
            )
            self.store.finish(fid, [])

        self.store.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": [
                    {
                        "child": {"resourceId": "SELF-111"},
                        "parent1": {"resourceId": "FATH-111"},
                        "parent2": {"resourceId": "MOTH-111"},
                    }
                ]
            },
        )

        self.plan = ResearchPlan(self.store)
        self.plan.sync()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def by_id(self):
        return {item["id"]: item for item in self.plan.items()}

    def test_category_override_survives_reopening(self):
        self.plan.set_category("FATH-111", "collateral")

        item = self.by_id()["FATH-111"]
        self.assertEqual(item["category"], "collateral")
        self.assertEqual(item["recommended_category"], "ancestor")
        self.assertTrue(item["category_is_manual"])

        self.store.close()
        self.store = Checkpoint(self.path)
        self.plan = ResearchPlan(self.store)
        self.plan.sync()

        self.assertEqual(
            self.by_id()["FATH-111"]["category"],
            "collateral",
        )

        self.plan.set_category("FATH-111", None)

        item = self.by_id()["FATH-111"]
        self.assertEqual(item["category"], "ancestor")
        self.assertFalse(item["category_is_manual"])

    def test_branch_override_and_restore(self):
        self.plan.set_branches("FATH-111", ["MOTH-111"])

        item = self.by_id()["FATH-111"]

        self.assertEqual(
            [branch["id"] for branch in item["branches"]],
            ["MOTH-111"],
        )
        self.assertTrue(item["branches_are_manual"])

        self.plan.sync()

        self.assertEqual(
            [branch["id"] for branch in self.by_id()["FATH-111"]["branches"]],
            ["MOTH-111"],
        )

        self.plan.set_branches("FATH-111", None)

        item = self.by_id()["FATH-111"]
        self.assertFalse(item["branches_are_manual"])
        self.assertEqual(
            item["branches"],
            item["recommended_branches"],
        )

    def test_manual_order_survives_reopening(self):
        self.plan.move("MOTH-111", before_id="SELF-111")

        self.assertEqual(
            self.plan.items(order="manual")[0]["id"],
            "MOTH-111",
        )

        self.assertEqual(
            self.plan.items(order="recommended")[0]["id"],
            "SELF-111",
        )

        self.store.close()
        self.store = Checkpoint(self.path)
        self.plan = ResearchPlan(self.store)

        self.assertEqual(
            self.plan.items(order="manual")[0]["id"],
            "MOTH-111",
        )

    def test_new_people_append_after_manual_reordering(self):
        self.plan.move("MOTH-111", before_id="SELF-111")

        self.store.seed(["LATE-111"])
        self.store.save_response(
            PERSON_URL + "LATE-111",
            {
                "persons": [
                    {
                        "id": "LATE-111",
                        "display": {"name": "New relative"},
                    }
                ]
            },
        )
        self.store.finish("LATE-111", [])

        self.plan.sync()

        items = self.plan.items(order="manual")

        self.assertEqual(items[0]["id"], "MOTH-111")
        self.assertEqual(items[-1]["id"], "LATE-111")
        self.assertEqual(len(items), 5)

    def test_invalid_manual_decisions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.plan.set_category("FATH-111", "imaginary")

        with self.assertRaises(ValueError):
            self.plan.set_category("SELF-111", "collateral")

        with self.assertRaises(ValueError):
            self.plan.set_branches("FATH-111", ["FAKE-111"])

        with self.assertRaises(KeyError):
            self.plan.set_category("FAKE-111", "ancestor")

        with self.assertRaises(KeyError):
            self.plan.move("FATH-111", before_id="FAKE-111")


if __name__ == "__main__":
    unittest.main()
