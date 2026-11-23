
"""Tests for conservative paternal and maternal branch labels"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_recommendations import build_recommendations


class BranchLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Checkpoint(
            Path(self.temp.name) / "branches.sqlite"
        )
        self.store.set_origin("SELF-111")

        self.ids = [
            "SELF-111",
            "FATH-111",
            "MOTH-111",
            "EXTR-111",
        ]

        genders = {
            "SELF-111": "Unknown",
            "FATH-111": "Male",
            "MOTH-111": "Unknown",
            "EXTR-111": "Female",
        }

        self.store.seed(self.ids)

        for fid in self.ids:
            self.save_gender(fid, genders[fid])
            self.store.finish(fid, [])

        self.save_parentage(include_extra=False)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save_gender(self, fid, gender):
        self.store.save_response(
            PERSON_URL + fid,
            {
                "persons": [
                    {
                        "id": fid,
                        "display": {"name": fid},
                        "gender": {
                            "type": f"http://gedcomx.org/{gender}"
                        },
                    }
                ]
            },
        )

    def save_parentage(self, include_extra):
        relationships = [
            {
                "child": {"resourceId": "SELF-111"},
                "parent1": {"resourceId": "FATH-111"},
                "parent2": {"resourceId": "MOTH-111"},
            }
        ]

        if include_extra:
            relationships.append(
                {
                    "child": {"resourceId": "SELF-111"},
                    "parent1": {"resourceId": "EXTR-111"},
                }
            )

        self.store.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": relationships
            },
        )

    def test_unknown_parent_gender_is_not_guessed(self):
        result = build_recommendations(self.store, self.ids)

        sides = {
            branch["side"]
            for branch in result["SELF-111"]["branches"]
        }

        self.assertEqual(sides, {"unknown"})

        self.save_gender("MOTH-111", "Female")

        result = build_recommendations(self.store, self.ids)

        sides_by_id = {
            branch["id"]: branch["side"]
            for branch in result["SELF-111"]["branches"]
        }

        self.assertEqual(sides_by_id["FATH-111"], "paternal")
        self.assertEqual(sides_by_id["MOTH-111"], "maternal")

    def test_multiple_parent_links_remain_unclassified(self):
        self.save_gender("MOTH-111", "Female")
        self.save_parentage(include_extra=True)

        result = build_recommendations(self.store, self.ids)

        branches = result["SELF-111"]["branches"]

        self.assertEqual(len(branches), 3)
        self.assertTrue(
            all(branch["side"] == "unknown" for branch in branches)
        )


if __name__ == "__main__":
    unittest.main()
