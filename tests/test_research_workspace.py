"""Tests for the persistent workspace backing the future graphical interface."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_workspace import ResearchWorkspace, TaskQuery


class ResearchWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "tree.sqlite"
        self.checkpoint = Checkpoint(self.database)
        self.addCleanup(self.checkpoint.close)
        self.checkpoint.set_origin("SELF-111")

        names = {
            "SELF-111": "Ana de Castro",
            "FATH-111": "Manuel de Castro",
            "SIBL-111": "Rosa de Castro",
        }
        self.checkpoint.seed(names)

        for fid, name in names.items():
            person = {"id": fid, "display": {"name": name}}
            if fid == "SELF-111":
                person["facts"] = [
                    {
                        "id": "birth",
                        "type": "http://gedcomx.org/Birth",
                        "date": {"original": "1920"},
                    }
                ]
            self.checkpoint.save_response(
                PERSON_URL + fid, {"persons": [person]}
            )
            self.checkpoint.finish(fid, [])

        self.checkpoint.save_response(
            FAMILIES_URL.format("SELF-111"),
            {
                "childAndParentsRelationships": [
                    {
                        "child": {"resourceId": "SELF-111"},
                        "parent1": {"resourceId": "FATH-111"},
                    },
                    {
                        "child": {"resourceId": "SIBL-111"},
                        "parent1": {"resourceId": "FATH-111"},
                    },
                ]
            },
        )
        self.workspace = ResearchWorkspace(self.checkpoint)

    def test_search_filter_and_manual_decisions(self):
        self.workspace.plan.set_priority("SIBL-111", 0)
        self.workspace.plan.set_status("FATH-111", "done")
        self.workspace.plan.move("SIBL-111", before_id="SELF-111")

        page = self.workspace.list_tasks(TaskQuery(search="castro", limit=2))
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["count"], 2)

        manual = self.workspace.list_tasks(TaskQuery(order="manual"))
        self.assertEqual(manual["items"][0]["id"], "SIBL-111")

        finished = self.workspace.list_tasks(TaskQuery(status="done"))
        self.assertEqual(finished["total"], 1)
        self.assertEqual(finished["items"][0]["id"], "FATH-111")

        relatives = self.workspace.list_tasks(TaskQuery(category="collateral"))
        self.assertEqual(relatives["items"][0]["id"], "SIBL-111")

        self.assertEqual(
            self.workspace.list_tasks(TaskQuery(search="not found"))["items"],
            [],
        )
        with self.assertRaises(ValueError):
            self.workspace.list_tasks(TaskQuery(limit=-1))

    def test_person_sheet_includes_direct_relatives_and_original_events(self):
        self.checkpoint.save_review_notes({"SELF-111": "Checked birth date"})
        self.workspace.set_person_note("SELF-111", "Find parish record.")

        detail = self.workspace.person_detail("SELF-111")
        self.assertEqual(detail["task"]["name"], "Ana de Castro")
        self.assertEqual(detail["person"]["person_id"], "SELF-111")
        self.assertEqual(len(detail["events"]), 1)
        self.assertEqual(detail["events"][0]["date_original"], "1920")
        self.assertEqual(detail["review"], "Checked birth date")
        self.assertEqual(detail["research_note"], "Find parish record.")
        self.assertEqual(
            [
                item["relative_name"] for item in detail["relationships"]
            ],
            ["Manuel de Castro"],
        )

        with self.assertRaises(KeyError):
            self.workspace.person_detail("FAKE-111")

    def test_private_notes_are_independent_of_markdown_reviews(self):
        self.checkpoint.save_review_notes({"SELF-111": "Manual checkbox"})
        self.workspace.set_person_note("SELF-111", "Keep this note.")

        other = ResearchWorkspace(self.checkpoint)
        self.assertEqual(other.get_person_note("SELF-111"), "Keep this note.")
        self.assertEqual(
            self.checkpoint.load_review_notes()["SELF-111"], "Manual checkbox"
        )

        other.set_person_note("SELF-111", None)
        self.assertEqual(self.workspace.get_person_note("SELF-111"), "")
        with self.assertRaises(KeyError):
            self.workspace.set_person_note("FAKE-111", "unknown")

    def test_board_cards_survive_reopening_and_preserve_links(self):
        first = self.workspace.create_card(
            {
                "kind": "document",
                "title": "Marriage record",
                "url": "https://example.org/doc.pdf",
                "x": 115,
                "y": 210,
            },
            people=["SELF-111", "FATH-111", "SELF-111"],
        )
        self.assertEqual(first["people"], ["FATH-111", "SELF-111"])
        self.assertEqual(first["x"], 115.0)

        updated = self.workspace.update_card(
            first["id"], {"x": 250, "rotation": -4, "z": 3},
            people=["SELF-111"],
        )
        self.assertEqual(updated["title"], "Marriage record")
        self.assertEqual(updated["people"], ["SELF-111"])
        self.assertEqual(updated["rotation"], -4.0)

        self.checkpoint.close()
        self.checkpoint = Checkpoint(self.database)
        reopened = ResearchWorkspace(self.checkpoint)
        self.assertEqual(reopened.get_card(first["id"])["x"], 250.0)
        self.assertEqual(
            reopened.person_detail("SELF-111")["cards"][0]["id"], first["id"]
        )
        reopened.delete_card(first["id"])
        self.assertEqual(reopened.list_cards(), [])
        with self.assertRaises(KeyError):
            reopened.get_card(first["id"])

    def test_invalid_cards_do_not_change_board_or_genealogy(self):
        before = self.checkpoint.get_response(PERSON_URL + "SELF-111")
        with self.assertRaises(ValueError):
            self.workspace.create_card({"title": "Bad", "url": "file:///secret"})
        with self.assertRaises(ValueError):
            self.workspace.create_card({"title": "Bad", "x": float("nan")})
        with self.assertRaises(ValueError):
            self.workspace.create_card({"title": "Bad", "unexpected": True})
        with self.assertRaises(KeyError):
            self.workspace.create_card({"title": "Bad"}, people=["FAKE-111"])

        self.assertEqual(self.workspace.list_cards(), [])
        self.assertEqual(
            self.checkpoint.get_response(PERSON_URL + "SELF-111"), before
        )

    def test_summary_includes_research_progress_and_board_size(self):
        self.workspace.plan.set_status("FATH-111", "in_progress")
        self.workspace.create_card({"kind": "note", "title": "Parish archives"})
        summary = self.workspace.summary()
        self.assertEqual(summary["people"], 3)
        self.assertEqual(summary["statuses"]["in_progress"], 1)
        self.assertEqual(summary["cards"], 1)


if __name__ == "__main__":
    unittest.main()
