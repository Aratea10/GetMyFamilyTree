"""Tests for family relationship recommendations"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_plan import ResearchPlan
from getmyfamilytree.research_recommendations import build_recommendations


def parent_child(child, *parents):
    return {
        "child": {"resourceId": child},
        **{
            f"parent{index}": {"resourceId": parent}
            for index, parent in enumerate(parents, 1)
        },
    }


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Checkpoint(Path(self.temp.name) / "tree.sqlite")
        self.store.set_origin("SELF-111")

        self.ids = [
            "SELF-111",
            "FATH-111",
            "MOTH-111",
            "GRPA-111",
            "SIBL-111",
            "AUNT-111",
            "COUS-111",
            "SPSE-111",
            "INLW-111",
            "CHLD-111",
            "OTHR-111",
        ]

        self.store.seed(self.ids)

        for fid in self.ids:
            gender = (
                "Male"
                if fid == "FATH-111"
                else "Female" if fid == "MOTH-111" else "Unknown"
            )

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
                    parent_child("SELF-111", "FATH-111", "MOTH-111"),
                    parent_child("SIBL-111", "FATH-111", "MOTH-111"),
                    parent_child("CHLD-111", "SELF-111", "SPSE-111"),
                ],
                "relationships": [
                    {
                        "type": "http://gedcomx.org/Couple",
                        "person1": {"resourceId": "SELF-111"},
                        "person2": {"resourceId": "SPSE-111"},
                    }
                ],
            },
        )
        self.store.save_response(
            FAMILIES_URL.format("FATH-111"),
            {
                "childAndParentsRelationships": [
                    parent_child("FATH-111", "GRPA-111"),
                    parent_child("AUNT-111", "GRPA-111"),
                ]
            },
        )
        self.store.save_response(
            FAMILIES_URL.format("AUNT-111"),
            {
                "childAndParentsRelationships": [
                    parent_child("COUS-111", "AUNT-111"),
                ]
            },
        )
        self.store.save_response(
            FAMILIES_URL.format("SPSE-111"),
            {
                "childAndParentsRelationships": [
                    parent_child("SPSE-111", "INLW-111"),
                ]
            },
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_classifies_family_relationships(self):
        result = build_recommendations(self.store, self.ids)

        expected = {
            "SELF-111": "origin",
            "FATH-111": "ancestor",
            "MOTH-111": "ancestor",
            "GRPA-111": "ancestor",
            "SIBL-111": "collateral",
            "AUNT-111": "collateral",
            "COUS-111": "collateral",
            "SPSE-111": "in_law",
            "CHLD-111": "descendant",
            "INLW-111": "unclassified",
            "OTHR-111": "unclassified",
        }

        for fid, category in expected.items():
            with self.subTest(person=fid):
                self.assertEqual(result[fid]["category"], category)

        self.assertEqual(result["FATH-111"]["branches"][0]["side"], "paternal")
        self.assertEqual(result["MOTH-111"]["branches"][0]["side"], "maternal")
        self.assertEqual(len(result["SIBL-111"]["branches"]), 2)

    def test_manual_priority_and_status_override_recommendations(self):
        plan = ResearchPlan(self.store)
        self.assertEqual(plan.sync(), len(self.ids))

        plan.set_priority("COUS-111", 0)
        self.assertEqual(plan.items()[0]["id"], "COUS-111")

        plan.set_status("COUS-111", "done")
        self.assertNotEqual(plan.items()[0]["id"], "COUS-111")

        plan.set_status("SPSE-111", "postponed")
        self.assertEqual(plan.items()[-1]["id"], "SPSE-111")

    def test_new_people_keep_existing_positions(self):
        other = Checkpoint(Path(self.temp.name) / "later.sqlite")

        try:
            other.seed(["AAAA-111", "BBBB-222"])
            other.finish("BBBB-222", [])

            plan = ResearchPlan(other)
            plan.sync()

            other.finish("AAAA-111", [])
            plan.sync()

            positions = dict(
                other.connection.execute(
                    "SELECT fid, position FROM research_tasks"
                ).fetchall()
            )

            self.assertEqual(positions["BBBB-222"], 0)
            self.assertEqual(positions["AAAA-111"], 1)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
