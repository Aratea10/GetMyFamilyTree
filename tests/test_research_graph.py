
"""Regression tests for complex genealogy graphs"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_recommendations import build_recommendations


def parent_child(child, *parents):
    return {
        "child": {"resourceId": child},
        **{
            f"parent{index}": {"resourceId": parent}
            for index, parent in enumerate(parents, 1)
        },
    }


def couple(first, second):
    return {
        "type": "http://gedcomx.org/Couple",
        "person1": {"resourceId": first},
        "person2": {"resourceId": second},
    }


class ResearchGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.store = Checkpoint(
            Path(self.temp.name) / "tree.sqlite"
        )
        self.addCleanup(self.store.close)

        self.store.set_origin("SELF-111")

    def add_people(self, identifiers):
        self.store.seed(identifiers)

        for fid in identifiers:
            gender = {
                "FATH-111": "Male",
                "MOTH-111": "Female",
            }.get(fid, "Unknown")

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

            self.store.finish(fid, [])

    def test_parentage_cycle_does_not_repeat_people(self):
        identifiers = [
            "SELF-111",
            "AAAA-111",
            "BBBB-111",
        ]

        self.add_people(identifiers)

        self.store.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": [
                    parent_child("SELF-111", "AAAA-111"),
                    parent_child("AAAA-111", "BBBB-111"),
                    parent_child("BBBB-111", "SELF-111"),
                ]
            },
        )

        result = build_recommendations(
            self.store, identifiers
        )

        self.assertEqual(set(result), set(identifiers))
        self.assertEqual(
            result["SELF-111"]["category"], "origin"
        )
        self.assertEqual(
            result["SELF-111"]["generation"], 0
        )
        self.assertEqual(
            result["AAAA-111"]["generation"], 1
        )
        self.assertEqual(
            result["BBBB-111"]["generation"], 2
        )

    def test_shared_in_law_preserves_all_affinity_links(self):
        identifiers = [
            "SELF-111",
            "FATH-111",
            "MOTH-111",
            "MGRM-111",
            "MAUN-111",
            "SPSE-111",
            "INLW-111",
        ]

        self.add_people(identifiers)

        self.store.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": [
                    parent_child(
                        "SELF-111", "FATH-111", "MOTH-111"
                    ),
                    parent_child("MOTH-111", "MGRM-111"),
                    parent_child("MAUN-111", "MGRM-111"),
                    parent_child("SPSE-111", "INLW-111"),
                ],
                "relationships": [
                    couple("FATH-111", "SPSE-111"),
                    couple("MAUN-111", "SPSE-111"),
                ],
            },
        )

        result = build_recommendations(
            self.store, identifiers
        )

        relative = result["INLW-111"]

        self.assertEqual(relative["category"], "in_law")

        self.assertEqual(
            {branch["side"] for branch in relative["branches"]},
            {"paternal", "maternal"},
        )

        self.assertEqual(
            relative["affinity_links"],
            [
                {
                    "relative_id": "FATH-111",
                    "partner_id": "SPSE-111",
                },
                {
                    "relative_id": "MAUN-111",
                    "partner_id": "SPSE-111",
                },
            ],
        )

        self.assertEqual(
            relative["recommendation_key"][2], 3
        )

    def test_blood_relative_partner_can_have_external_relatives(self):
        identifiers = [
            "SELF-111",
            "FATH-111",
            "GRPA-111",
            "AUNT-111",
            "COUS-111",
            "OUTS-111",
        ]

        self.add_people(identifiers)

        self.store.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": [
                    parent_child("SELF-111", "FATH-111"),
                    parent_child("FATH-111", "GRPA-111"),
                    parent_child("AUNT-111", "GRPA-111"),
                    parent_child(
                        "COUS-111", "AUNT-111", "OUTS-111"
                    ),
                ],
                "relationships": [
                    couple("SELF-111", "COUS-111"),
                ],
            },
        )

        result = build_recommendations(
            self.store, identifiers
        )

        self.assertEqual(
            result["COUS-111"]["category"], "collateral"
        )
        self.assertEqual(
            result["OUTS-111"]["category"], "in_law"
        )
        self.assertEqual(
            result["OUTS-111"]["affinity_links"],
            [
                {
                    "relative_id": "SELF-111",
                    "partner_id": "COUS-111",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
