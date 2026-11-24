"""Tests for the offline research CLI"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from getmyfamilytree.cli import Checkpoint, main
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL


class ResearchCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "research.sqlite"

        store = Checkpoint(self.path)
        store.set_origin("SELF-111")

        genders = {
            "SELF-111": "Unknown",
            "FATH-111": "Male",
            "MOTH-111": "Female",
        }

        store.seed(genders)

        for fid, gender in genders.items():
            store.save_response(
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
            store.finish(fid, [])

        store.save_response(
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

        store.close()

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *arguments):
        output = io.StringIO()

        with redirect_stdout(output):
            main(
                [
                    "research",
                    "--database",
                    str(self.path),
                    "--language",
                    "es",
                    *arguments,
                ]
            )

        return output.getvalue()

    def test_list_supports_json_and_pagination(self):
        result = json.loads(
            self.invoke(
                "list",
                "--json",
                "--limit",
                "1",
                "--offset",
                "1",
            )
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["items"]), 1)

    def test_summary_counts_research_statuses(self):
        self.invoke("status", "FATH-111", "done")

        result = json.loads(self.invoke("summary", "--json"))

        self.assertEqual(result["people"], 3)
        self.assertEqual(result["statuses"]["done"], 1)
        self.assertEqual(result["statuses"]["pending"], 2)

    def test_manual_changes_survive_separate_commands(self):
        self.invoke("priority", "FATH-111", "0")
        self.invoke("category", "FATH-111", "collateral")
        self.invoke("branches", "FATH-111", "MOTH-111")
        self.invoke(
            "move",
            "MOTH-111",
            "--before",
            "SELF-111",
        )

        result = json.loads(
            self.invoke(
                "list",
                "--order",
                "manual",
                "--json",
            )
        )

        self.assertEqual(
            result["items"][0]["id"],
            "MOTH-111",
        )

        father = next(item for item in result["items"] if item["id"] == "FATH-111")

        self.assertEqual(father["priority"], 0)
        self.assertEqual(father["category"], "collateral")
        self.assertTrue(father["category_is_manual"])
        self.assertEqual(
            [branch["id"] for branch in father["branches"]],
            ["MOTH-111"],
        )

        self.invoke("category", "FATH-111", "auto")
        self.invoke("branches", "FATH-111", "auto")

        restored = json.loads(self.invoke("list", "--json"))

        father = next(item for item in restored["items"] if item["id"] == "FATH-111")

        self.assertEqual(father["category"], "ancestor")
        self.assertFalse(father["category_is_manual"])
        self.assertFalse(father["branches_are_manual"])

    def test_missing_database_is_rejected(self):
        missing = Path(self.temp.name) / "missing.sqlite"

        with self.assertRaises(SystemExit) as caught:
            main(
                [
                    "research",
                    "--database",
                    str(missing),
                    "list",
                ]
            )

        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
