"""Regression tests for directional, generation-limited FamilySearch downloads."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from getmyfamilytree.cli import Checkpoint, assemble, main
from getmyfamilytree.reports import collect_reports
from getmyfamilytree.scopes import crawl_scope


def parent_child(parent, child):
    return {
        "id": f"rel-{parent}-{child}",
        "type": "http://gedcomx.org/ParentChild",
        "person1": {"resourceId": parent},
        "person2": {"resourceId": child},
    }


def couple(first, second):
    return {
        "id": f"rel-{first}-{second}",
        "type": "http://gedcomx.org/Couple",
        "person1": {"resourceId": first},
        "person2": {"resourceId": second},
    }


class FamilyGraphSession:
    """A connected family with in-laws, siblings and three generations."""

    ROOT = "AAAA-111"
    FATHER = "BBBB-222"
    MOTHER = "CCCC-333"
    SIBLING = "DDDD-444"
    SPOUSE = "EEEE-555"
    CHILD = "FFFF-666"
    GRANDCHILD = "HHHH-888"
    IN_LAW = "GGGG-777"
    GRANDFATHER = "KKKK-999"

    def __init__(self):
        self.calls = []
        self.lang = "en"
        self.display_name = "Mock person"
        self.relationships = {
            self.ROOT: [
                parent_child(self.FATHER, self.ROOT),
                parent_child(self.MOTHER, self.ROOT),
                parent_child(self.ROOT, self.CHILD),
                couple(self.ROOT, self.SPOUSE),
            ],
            self.FATHER: [
                parent_child(self.GRANDFATHER, self.FATHER),
                parent_child(self.FATHER, self.SIBLING),
            ],
            self.MOTHER: [],
            self.SIBLING: [],
            self.SPOUSE: [parent_child(self.IN_LAW, self.SPOUSE)],
            self.CHILD: [parent_child(self.CHILD, self.GRANDCHILD)],
            self.GRANDCHILD: [],
            self.IN_LAW: [],
            self.GRANDFATHER: [],
        }

    def _(self, value):
        return value

    def get_url(self, url, headers=None, no_api=False):
        self.calls.append(url)
        if url.startswith("/platform/tree/persons?pids="):
            identifier = url.split("pids=", 1)[1]
            if identifier not in self.relationships:
                return None
            return {
                "persons": [{
                    "id": identifier,
                    "display": {"name": "Person " + identifier},
                    "names": [],
                    "living": False,
                }],
                "relationships": self.relationships[identifier],
            }
        if url.endswith("/families"):
            return {"relationships": [], "persons": []}
        return None


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.checkpoint = Checkpoint(Path(self.tmp.name) / "family.sqlite")
        self.checkpoint.set_origin(FamilyGraphSession.ROOT)
        self.session = FamilyGraphSession()

    def tearDown(self):
        self.checkpoint.close()
        self.tmp.cleanup()

    def run_scope(self, mode, *, generations=None, distance=None,
                  max_people=0, offline=False):
        with redirect_stderr(io.StringIO()):
            return crawl_scope(
                self.checkpoint, None if offline else self.session,
                FamilyGraphSession.ROOT, scope=mode,
                generations=generations, distance=distance,
                max_people=max_people, progress=0,
            )

    def test_ancestors_have_only_paternal_and_maternal_lines(self):
        ids = set(self.run_scope("ancestors", generations=1).ids)
        self.assertEqual(ids, {"AAAA-111", "BBBB-222", "CCCC-333"})
        two = set(self.run_scope("ancestors", generations=2).ids)
        self.assertEqual(two, ids | {"KKKK-999"})
        self.assertNotIn("EEEE-555", two)
        self.assertNotIn("DDDD-444", two)

    def test_zero_generations_keeps_only_starting_person(self):
        ids = self.run_scope("ancestors", generations=0).ids
        self.assertEqual(ids, ["AAAA-111"])

    def test_descendants_do_not_include_spouses_or_in_laws(self):
        one = set(self.run_scope("descendants", generations=1).ids)
        self.assertEqual(one, {"AAAA-111", "FFFF-666"})
        two = set(self.run_scope("descendants", generations=2).ids)
        self.assertEqual(two, one | {"HHHH-888"})
        self.assertNotIn("EEEE-555", two)

    def test_extended_family_uses_relationship_distance(self):
        one = set(self.run_scope("family", distance=1).ids)
        self.assertEqual(
            one, {"AAAA-111", "BBBB-222", "CCCC-333", "EEEE-555", "FFFF-666"},
        )
        two = set(self.run_scope("family", distance=2).ids)
        self.assertEqual(two, set(self.session.relationships))
        self.assertNotIn("DDDD-444", one)  # sibling: two parent/child links
        self.assertIn("DDDD-444", two)
        self.assertIn("GGGG-777", two)  # partner's parent: two links

    def test_complete_tree_reaches_every_relative_without_depth_limit(self):
        result = self.run_scope("full")
        self.assertEqual(set(result.ids), set(self.session.relationships))
        self.assertTrue(result.exhausted)
        self.assertEqual(result.failed, 0)

    def test_switching_modes_reuses_successful_responses(self):
        self.run_scope("ancestors", generations=1)
        root_url = "/platform/tree/persons?pids=AAAA-111"
        calls_before = self.session.calls.count(root_url)
        descendants = self.run_scope("descendants", generations=2)
        self.assertEqual(set(descendants.ids), {
            "AAAA-111", "FFFF-666", "HHHH-888",
        })
        self.assertEqual(self.session.calls.count(root_url), calls_before)
        self.assertGreaterEqual(descendants.reused, 1)
        full = self.run_scope("full")
        self.assertEqual(set(full.ids), set(self.session.relationships))
        self.assertGreater(full.reused, 0)
        self.assertEqual(self.checkpoint.get_origin(), "AAAA-111")

    def test_max_people_is_per_selected_scope_and_can_increase(self):
        first = self.run_scope("ancestors", generations=2, max_people=2)
        self.assertEqual(first.ids, ["AAAA-111", "BBBB-222"])
        self.assertFalse(first.exhausted)
        later = self.run_scope("ancestors", generations=2, max_people=4)
        self.assertEqual(set(later.ids), {
            "AAAA-111", "BBBB-222", "CCCC-333", "KKKK-999",
        })
        self.assertTrue(later.exhausted)
        root = "/platform/tree/persons?pids=AAAA-111"
        self.assertEqual(self.session.calls.count(root), 1)

    def test_gedcom_export_includes_only_selected_ancestors(self):
        self.run_scope("full")
        selected = self.run_scope(
            "ancestors", generations=1, offline=True,
        ).ids
        output = Path(self.tmp.name) / "ancestors.ged"
        people, _ = assemble(
            self.checkpoint, self.session, output,
            no_sources=True, no_notes=True, no_memories=True,
            no_contributors=True, selected_ids=selected,
        )
        gedcom = output.read_text(encoding="utf-8")
        self.assertEqual(people, 3)
        self.assertEqual(
            sum(line.endswith(" INDI") for line in gedcom.splitlines()),
            3,
        )
        self.assertIn("1 _FSFTID AAAA-111", gedcom)
        self.assertIn("1 _FSFTID BBBB-222", gedcom)
        self.assertIn("1 _FSFTID CCCC-333", gedcom)
        self.assertNotIn("1 _FSFTID EEEE-555", gedcom)
        self.assertNotIn("1 _FSFTID FFFF-666", gedcom)

    def test_people_outside_scope_do_not_enter_report(self):
        self.run_scope("full")
        result = self.run_scope("ancestors", generations=1, offline=True)
        report = collect_reports(self.checkpoint, selected_ids=result.ids)
        self.assertEqual(len(report["people"]), 3)
        self.assertNotIn(
            "EEEE-555", {person["person_id"] for person in report["people"]}
        )
        couple_links = [
            relation for relation in report["relationships"]
            if relation["relationship_type"] == "Couple"
        ]
        self.assertFalse(couple_links)

    def test_offline_filter_does_not_download_new_people(self):
        self.run_scope("full")
        original_calls = list(self.session.calls)
        result = self.run_scope("descendants", generations=2, offline=True)
        self.assertEqual(set(result.ids), {
            "AAAA-111", "FFFF-666", "HHHH-888",
        })
        self.assertEqual(self.session.calls, original_calls)

    def test_spanish_help_lists_four_modes(self):
        output = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--language", "es", "--help"])
        self.assertIn("--scope", output.getvalue())
        self.assertIn("--generations", output.getvalue())
        self.assertIn("--distance", output.getvalue())

    def test_invalid_combinations_are_rejected_before_login(self):
        variants = [
            ["--scope", "ancestors"],
            ["--scope", "descendants", "--distance", "2"],
            ["--scope", "family", "--generations", "2"],
            ["--scope", "full", "--generations", "2"],
            ["--scope", "family", "--distance", "-2"],
        ]
        for options in variants:
            with self.subTest(options=options):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(options)
                self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
