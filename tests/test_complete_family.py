"""Regression tests for complete family traversal (no FamilySearch login required)."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.cli import (
    ReplaySession,
    assemble,
    crawl,
    person_display_name,
    related_people,
)


class FakeSession:
    def __init__(self):
        self.calls = []
        self.lang = "en"
        self.display_name = "Test user"
        self.records = {
            "AAAA-111": {
                "persons": [{"id": "AAAA-111", "living": False, "names": []}],
                "relationships": [
                    {
                        "id": "couple-1",
                        "type": "http://gedcomx.org/Couple",
                        "person1": {"resourceId": "AAAA-111"},
                        "person2": {"resourceId": "BBBB-222"},
                    }
                ],
            },
            "BBBB-222": {
                "persons": [{"id": "BBBB-222", "living": False, "names": []}],
                "childAndParentsRelationships": [
                    {
                        "id": "parent-1",
                        "parent1": {"resourceId": "CCCC-333"},
                        "child": {"resourceId": "BBBB-222"},
                    }
                ],
            },
            "CCCC-333": {
                "persons": [{"id": "CCCC-333", "living": False, "names": []}],
                "childAndParentsRelationships": [
                    {
                        "id": "parent-2",
                        "parent1": {"resourceId": "CCCC-333"},
                        "child": {"resourceId": "DDDD-444"},
                    }
                ],
            },
            "DDDD-444": {"persons": [{"id": "DDDD-444", "living": False, "names": []}]},
        }

    def _(self, text):
        return text

    def get_url(self, url, headers=None, no_api=False):
        self.calls.append(url)
        if url.startswith("/platform/tree/couple-relationships/"):
            return {"relationships": [{"facts": []}]}
        return self.records.get(url.split("=")[-1])


class CompleteFamilyTests(unittest.TestCase):
    def test_person_display_name_prefers_familysearch_display(self):
        document = {
            "persons": [
                {"id": "AAAA-111", "display": {"name": "Maria   da   Silva"}},
            ]
        }
        self.assertEqual(person_display_name(document, "AAAA-111"), "Maria da Silva")

    def test_person_display_name_accepts_gedcomx_full_text(self):
        document = {
            "persons": [
                {
                    "id": "AAAA-111",
                    "names": [{"nameForms": [{"fullText": "Ana Goncalves"}]}],
                }
            ]
        }
        self.assertEqual(person_display_name(document, "AAAA-111"), "Ana Goncalves")
        self.assertEqual(person_display_name(document, "BBBB-222"), "BBBB-222")

    def test_every_person_is_reported_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Checkpoint(Path(directory) / "family.sqlite")
            session = FakeSession()
            session.records["AAAA-111"]["persons"][0]["display"] = {
                "name": "Maria Example"
            }
            try:
                checkpoint.seed(["AAAA-111"])
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertFalse(crawl(checkpoint, session, max_people=2))
                progress = output.getvalue()
                self.assertIn("Starting exploration: 0 already explored", progress)
                self.assertIn("Exploring 1/2: AAAA-111...", progress)
                self.assertIn(
                    "Found: Maria Example (AAAA-111) | +1 new relatives",
                    progress,
                )
                self.assertIn("Exploring 2/2: BBBB-222...", progress)
                self.assertIn("2 explored", progress)
                self.assertIn("1 queued", progress)
            finally:
                checkpoint.close()

    def test_progress_interval_can_be_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Checkpoint(Path(directory) / "family.sqlite")
            try:
                checkpoint.seed(["AAAA-111"])
                output = io.StringIO()
                with redirect_stderr(output):
                    crawl(checkpoint, FakeSession(), max_people=2, progress=2)
                progress = output.getvalue()
                self.assertNotIn("Exploring 1/2", progress)
                self.assertIn("Exploring 2/2", progress)
            finally:
                checkpoint.close()

    def test_discovers_parents_children_and_spouses(self):
        self.assertEqual(
            related_people(
                {
                    "childAndParentsRelationships": [
                        {
                            "parent1": {"resourceId": "BBBB-222"},
                            "parent2": {"resourceId": "CCCC-333"},
                            "child": {"resourceId": "AAAA-111"},
                        }
                    ],
                    "relationships": [
                        {
                            "person1": {"resourceId": "AAAA-111"},
                            "person2": {"resourceId": "DDDD-444"},
                        }
                    ],
                }
            ),
            {"AAAA-111", "BBBB-222", "CCCC-333", "DDDD-444"},
        )

    def test_traverses_through_spouse_to_in_laws_and_other_children(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            session = FakeSession()
            try:
                store.seed(["AAAA-111"])
                self.assertTrue(crawl(store, session, max_people=0, progress=0))
                self.assertEqual(store.count("done"), 4)
                self.assertEqual(store.count("pending"), 0)
                self.assertEqual(len(session.calls), 8)
            finally:
                store.close()

    def test_resumes_without_fetching_previous_people(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "family.sqlite"
            store = Checkpoint(path)
            try:
                store.seed(["AAAA-111"])
                self.assertFalse(crawl(store, FakeSession(), max_people=2, progress=0))
                self.assertEqual(store.count("done"), 2)
            finally:
                store.close()

            store = Checkpoint(path)
            try:
                session = FakeSession()
                self.assertTrue(crawl(store, session, max_people=0, progress=0))
                self.assertEqual(store.count("done"), 4)
                self.assertEqual(
                    session.calls,
                    [
                        "/platform/tree/persons?pids=CCCC-333",
                        "/platform/tree/persons/CCCC-333/families",
                        "/platform/tree/persons?pids=DDDD-444",
                        "/platform/tree/persons/DDDD-444/families",
                    ],
                )
            finally:
                store.close()

    def test_replay_does_not_include_extra_unrequested_people(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            try:
                document = {
                    "persons": [
                        {"id": "AAAA-111", "living": False, "names": []},
                        {"id": "EEEE-555", "living": False, "names": []},
                    ],
                    "relationships": [],
                }
                store.save_response("/platform/tree/persons?pids=AAAA-111", document)
                replay = ReplaySession(FakeSession(), store)
                result = replay.get_url("/platform/tree/persons?pids=AAAA-111")
                self.assertEqual(
                    [person["id"] for person in result["persons"]],
                    ["AAAA-111"],
                )
            finally:
                store.close()

    def test_replay_can_combine_cached_person_responses(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            try:
                session = FakeSession()
                for identifier in ("AAAA-111", "BBBB-222"):
                    store.save_response(
                        "/platform/tree/persons?pids=" + identifier,
                        session.records[identifier],
                    )
                replay = ReplaySession(session, store)
                response = replay.get_url(
                    "/platform/tree/persons?pids=AAAA-111,BBBB-222"
                )
                self.assertEqual(
                    {p["id"] for p in response["persons"]},
                    {"AAAA-111", "BBBB-222"},
                )
                self.assertEqual(len(response["relationships"]), 1)
                self.assertEqual(len(response["childAndParentsRelationships"]), 1)
                self.assertEqual(session.calls, [])
            finally:
                store.close()

    def test_gedcom_contains_relatives_from_the_spouses_family(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            session = FakeSession()
            try:
                store.seed(["AAAA-111"])
                self.assertTrue(crawl(store, session, max_people=0, progress=0))
                output = Path(folder) / "family.ged"
                people, families = assemble(
                    store,
                    session,
                    output,
                    no_sources=True,
                    no_notes=True,
                    no_contributors=True,
                )
                gedcom = output.read_text(encoding="utf-8")
                self.assertEqual(people, 4)
                self.assertGreaterEqual(families, 2)
                self.assertEqual(
                    sum(line.endswith(" INDI") for line in gedcom.splitlines()), 4
                )
                self.assertIn("1 _FSFTID CCCC-333", gedcom)
                self.assertIn("1 _FSFTID DDDD-444", gedcom)
                self.assertIn("1 CHIL @I", gedcom)
                self.assertIn("1 HUSB @I", gedcom)
                self.assertEqual(gedcom.splitlines()[-1], "0 TRLR")
            finally:
                store.close()

    def test_family_endpoint_finds_siblings_and_preserves_link(self):
        class SessionWithSibling(FakeSession):
            def __init__(self):
                super().__init__()
                self.records["AAAA-111"]["persons"][0]["gender"] = {
                    "type": "http://gedcomx.org/Male"
                }
                self.records["EEEE-555"] = {
                    "persons": [{"id": "EEEE-555", "living": False, "names": []}]
                }

            def get_url(self, url, headers=None, no_api=False):
                if url == "/platform/tree/persons/AAAA-111/families":
                    self.calls.append(url)
                    return {
                        "persons": [{"id": "EEEE-555", "living": False, "names": []}],
                        "relationships": [
                            {
                                "id": "parent-child-extra",
                                "type": "http://gedcomx.org/ParentChild",
                                "person1": {"resourceId": "AAAA-111"},
                                "person2": {"resourceId": "EEEE-555"},
                            }
                        ],
                    }
                return super().get_url(url, headers, no_api)

        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            session = SessionWithSibling()
            try:
                store.seed(["AAAA-111"])
                self.assertTrue(crawl(store, session, max_people=0, progress=0))
                self.assertEqual(store.count("done"), 5)
                output = Path(folder) / "family.ged"
                people, families = assemble(
                    store,
                    session,
                    output,
                    no_notes=True,
                    no_sources=True,
                    no_contributors=True,
                )
                gedcom = output.read_text(encoding="utf-8")
                self.assertEqual(people, 5)
                self.assertGreaterEqual(families, 3)
                self.assertIn("1 _FSFTID EEEE-555", gedcom)
                self.assertIn("1 CHIL @I", gedcom)
            finally:
                store.close()

    def test_export_includes_events_sources_notes_and_memory_links(self):
        class SessionWithDetails(FakeSession):
            def __init__(self):
                super().__init__()
                self.records["AAAA-111"] = {
                    "persons": [
                        {
                            "id": "AAAA-111",
                            "living": False,
                            "names": [],
                            "facts": [
                                {
                                    "type": "http://gedcomx.org/Birth",
                                    "date": {"original": "1 JAN 1900"},
                                    "place": {"original": "Galicia"},
                                    "attribution": {},
                                }
                            ],
                            "sources": [{"descriptionId": "S01"}],
                            "evidence": [{"id": "M1234-1"}],
                        }
                    ]
                }

            def get_url(self, url, headers=None, no_api=False):
                if url == "/platform/tree/persons/AAAA-111/sources":
                    return {
                        "persons": [
                            {
                                "sources": [
                                    {
                                        "descriptionId": "S01",
                                        "attribution": {},
                                    }
                                ]
                            }
                        ],
                        "sourceDescriptions": [
                            {
                                "id": "S01",
                                "titles": [{"value": "Birth certificate"}],
                                "citations": [{"value": "Registry office"}],
                            }
                        ],
                    }
                if url == "/platform/tree/persons/AAAA-111/notes":
                    return {
                        "persons": [
                            {
                                "notes": [
                                    {
                                        "subject": "Research note",
                                        "text": "Original note text",
                                    }
                                ]
                            }
                        ]
                    }
                if url == "/platform/memories/memories/M1234":
                    return {
                        "sourceDescriptions": [
                            {
                                "mediaType": "image/jpeg",
                                "links": {},
                                "about": "https://example.org/memory",
                                "titles": [{"value": "Historical photo"}],
                            }
                        ]
                    }
                return super().get_url(url, headers, no_api)

        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            session = SessionWithDetails()
            try:
                store.seed(["AAAA-111"])
                self.assertTrue(crawl(store, session, max_people=1, progress=0))
                output = Path(folder) / "details.ged"
                assemble(store, session, output, workers=1, no_contributors=True)
                gedcom = output.read_text(encoding="utf-8")
                for field in (
                    "1 BIRT",
                    "2 DATE 1 JAN 1900",
                    "2 PLAC Galicia",
                    "Birth certificate",
                    "Registry office",
                    "Original note text",
                    "Historical photo",
                    "https://example.org/memory",
                ):
                    self.assertIn(field, gedcom)
                self.assertIsNotNone(
                    store.get_response("0::/platform/tree/persons/AAAA-111/sources")
                )
                self.assertIsNotNone(
                    store.get_response("0::/platform/tree/persons/AAAA-111/notes")
                )
            finally:
                store.close()

    def test_gedcom_relationships_across_large_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            session = FakeSession()
            ids = [f"F000-{number:03d}" for number in range(1, 203)]
            try:
                for fid in ids:
                    document = {"persons": [{"id": fid, "living": False, "names": []}]}
                    if fid == ids[0]:
                        document["childAndParentsRelationships"] = [
                            {
                                "id": "cross-batch",
                                "child": {"resourceId": ids[0]},
                                "parent1": {"resourceId": ids[-1]},
                            }
                        ]
                    store.save_response("/platform/tree/persons?pids=" + fid, document)
                store.seed(ids)
                for fid in ids:
                    store.finish(fid, [])
                output = Path(folder) / "large.ged"
                people, families = assemble(
                    store,
                    session,
                    output,
                    no_sources=True,
                    no_notes=True,
                    no_contributors=True,
                )
                data = output.read_text(encoding="utf-8")
                self.assertEqual(people, len(ids))
                self.assertEqual(families, 1)
                self.assertIn("1 _FSFTID " + ids[0], data)
                self.assertIn("1 _FSFTID " + ids[-1], data)
                self.assertEqual(
                    sum(line.endswith(" INDI") for line in data.splitlines()), len(ids)
                )
                self.assertIn("1 CHIL @I", data)
            finally:
                store.close()

    def test_checkpoint_rejects_other_starting_person(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "family.sqlite"
            store = Checkpoint(path)
            try:
                store.set_origin("AAAA-111")
            finally:
                store.close()

            reopened = Checkpoint(path)
            try:
                reopened.set_origin("AAAA-111")
                with self.assertRaisesRegex(ValueError, "different output|belongs"):
                    reopened.set_origin("BBBB-222")
            finally:
                reopened.close()

    def test_unavailable_people_are_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            try:
                store.seed(["ZZZZ-999"])
                self.assertFalse(crawl(store, FakeSession(), max_people=0, progress=0))
                self.assertEqual(store.count("failed"), 1)
                self.assertEqual(store.count("pending"), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
