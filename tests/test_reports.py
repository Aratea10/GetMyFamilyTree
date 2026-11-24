"""Tests for chronological, editable genealogy review tables"""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from getmyfamilytree.cli import Checkpoint, main
from getmyfamilytree.reports import export_reports, year_for_sorting


def read_table(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_table(path, fields, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class ReviewExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "family.ged"
        self.checkpoint = Checkpoint(Path(self.temp.name) / "family.sqlite")
        self.checkpoint.set_origin("AAAA-111")
        self.checkpoint.seed(["AAAA-111", "BBBB-222"])

        self.person = {
            "id": "AAAA-111",
            "display": {"name": "María da Silva"},
            "gender": {"type": "http://gedcomx.org/Female"},
            "living": False,
            "facts": [
                {
                    "id": "birth1",
                    "type": "http://gedcomx.org/Birth",
                    "date": {"original": "12 MAR 1892", "formal": "+1892-03-12"},
                    "place": {"original": "Cangas, Pontevedra"},
                    "sources": [{"description": "#source123"}],
                },
                {
                    "id": "residence1",
                    "type": "http://gedcomx.org/Residence",
                    "date": {"original": "3/4/1888"},
                },
                {
                    "id": "approx1",
                    "type": "http://gedcomx.org/Occupation",
                    "date": {"original": "circa 1901"},
                    "value": "Carpintero",
                },
                {
                    "id": "range1",
                    "type": "http://gedcomx.org/Residence",
                    "date": {"original": "between 1893 and 1903"},
                },
                {"id": "unknown", "type": "http://gedcomx.org/Religion"},
            ],
        }
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.person]},
        )
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=BBBB-222",
            {"persons": [{"id": "BBBB-222", "display": {"name": "José"}}]},
        )
        self.checkpoint.save_response(
            "/platform/tree/persons/AAAA-111/families",
            {
                "relationships": [
                    {
                        "id": "couple1",
                        "type": "http://gedcomx.org/Couple",
                        "person1": {"resourceId": "AAAA-111"},
                        "person2": {"resourceId": "BBBB-222"},
                    }
                ],
            },
        )
        self.checkpoint.finish("AAAA-111", [])
        self.checkpoint.finish("BBBB-222", [])

    def tearDown(self):
        self.checkpoint.close()
        self.temp.cleanup()

    def test_source_citations_notes_and_memories_from_cache(self):
        """Source details must be linked to the correct person, not guessed."""
        self.person["sources"] = [{"descriptionId": "S1"}]
        self.person["evidence"] = [{"id": "M1234-1"}]
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.person]},
        )
        self.checkpoint.save_response(
            "0::/platform/tree/persons/AAAA-111/sources",
            {
                "persons": [
                    {
                        "sources": [
                            {
                                "descriptionId": "S1",
                                "attribution": {"changeMessage": "Checked in archive"},
                            }
                        ]
                    }
                ],
                "sourceDescriptions": [
                    {
                        "id": "S1",
                        "titles": [{"value": "Libro de bautismos"}],
                        "citations": [{"value": "Parroquia de Darbo, folio 12"}],
                        "about": "https://www.familysearch.org/ark:/61903/1:1:ABCDE",
                        "notes": [{"id": "source-note-1", "text": "Original scan"}],
                    }
                ],
            },
        )
        self.checkpoint.save_response(
            "0::/platform/tree/persons/AAAA-111/notes",
            {
                "persons": [
                    {
                        "notes": [
                            {
                                "id": "person-note-1",
                                "subject": "Research",
                                "text": "Compare with parish register",
                            }
                        ]
                    }
                ]
            },
        )
        self.checkpoint.save_response(
            "0::/platform/memories/memories/M1234",
            {
                "sourceDescriptions": [
                    {
                        "id": "mem-1",
                        "mediaType": "image/jpeg",
                        "titles": [{"value": "Old family photograph"}],
                        "about": "https://www.familysearch.org/photos/artifacts/123",
                    }
                ]
            },
        )
        result = export_reports(self.checkpoint, self.output)
        sources = read_table(result["paths"]["sources"])
        notes = read_table(result["paths"]["notes"])
        memories = read_table(result["paths"]["memories"])

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["owner_id"], "AAAA-111")
        self.assertEqual(sources[0]["title"], "Libro de bautismos")
        self.assertIn("folio 12", sources[0]["citation"])
        self.assertEqual(sources[0]["attribution_note"], "Checked in archive")
        self.assertEqual(sources[0]["details_available"], "True")
        self.assertTrue(sources[0]["url"].startswith("https://"))

        self.assertEqual(
            {note["text"] for note in notes},
            {"Original scan", "Compare with parish register"},
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["person_id"], "AAAA-111")
        self.assertEqual(memories[0]["memory_id"], "M1234")
        self.assertEqual(memories[0]["title"], "Old family photograph")
        self.assertEqual(memories[0]["media_type"], "image/jpeg")

    def test_source_reference_without_details_is_kept(self):
        self.person["sources"] = [{"descriptionId": "missing-source"}]
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.person]},
        )
        paths = export_reports(self.checkpoint, self.output)["paths"]
        sources = read_table(paths["sources"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_id"], "missing-source")
        self.assertEqual(sources[0]["details_available"], "False")
        self.assertEqual(sources[0]["title"], "")

    def test_relationship_source_and_note_are_kept(self):
        self.checkpoint.save_response(
            "0::/platform/tree/couple-relationships/couple1",
            {
                "relationships": [
                    {
                        "id": "couple1",
                        "sources": [{"descriptionId": "marriage-source"}],
                    }
                ]
            },
        )
        self.checkpoint.save_response(
            "0::/platform/tree/couple-relationships/couple1/sources",
            {
                "sourceDescriptions": [
                    {
                        "id": "marriage-source",
                        "titles": [{"value": "Marriage register"}],
                        "citations": [{"value": "Parish book 1903"}],
                    }
                ]
            },
        )
        self.checkpoint.save_response(
            "0::/platform/tree/couple-relationships/couple1/notes",
            {
                "relationships": [
                    {
                        "notes": [
                            {
                                "id": "marriage-note",
                                "text": "Verify witness names",
                            }
                        ]
                    }
                ]
            },
        )
        report = export_reports(self.checkpoint, self.output)
        sources = read_table(report["paths"]["sources"])
        notes = read_table(report["paths"]["notes"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["owner_type"], "couple")
        self.assertEqual(sources[0]["owner_id"], "couple1")
        self.assertEqual(sources[0]["title"], "Marriage register")
        self.assertEqual(notes[0]["text"], "Verify witness names")

    def test_source_text_is_safe_for_spreadsheets(self):
        self.person["sources"] = [{"descriptionId": "S2"}]
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.person]},
        )
        self.checkpoint.save_response(
            "0::/platform/tree/persons/AAAA-111/sources",
            {
                "sourceDescriptions": [
                    {
                        "id": "S2",
                        "titles": [{"value": '=HYPERLINK("https://example.org")'}],
                    }
                ]
            },
        )
        report = export_reports(self.checkpoint, self.output)
        sources = read_table(report["paths"]["sources"])
        self.assertTrue(sources[0]["title"].startswith("'=HYPERLINK"))

    def test_chronological_order_and_original_dates(self):
        result = export_reports(self.checkpoint, self.output)
        events = read_table(result["paths"]["events"])
        self.assertEqual(
            [row["year"] for row in events],
            ["1888", "1892", "1901", "", ""],
        )
        self.assertEqual(events[0]["date_status"], "ambiguous_day_month")
        self.assertEqual(events[0]["date_original"], "3/4/1888")
        self.assertEqual(events[1]["date_formal"], "'+1892-03-12")
        self.assertEqual(events[1]["person_name"], "María da Silva")
        self.assertEqual(events[1]["source_references"], "#source123")
        self.assertEqual(events[2]["date_status"], "approximate")
        self.assertCountEqual(
            [event["date_status"] for event in events[3:]],
            ["range_or_relative", "undated_or_unparsed"],
        )

    def test_person_and_relationship_tables(self):
        result = export_reports(self.checkpoint, self.output)
        people = read_table(result["paths"]["people"])
        relations = read_table(result["paths"]["relationships"])
        self.assertEqual(len(people), 2)
        self.assertEqual(people[0]["name"], "José")
        self.assertEqual(people[1]["name"], "María da Silva")
        self.assertEqual(relations[0]["relationship_type"], "Couple")
        self.assertEqual(relations[0]["person1_name"], "María da Silva")
        self.assertEqual(relations[0]["person2_name"], "José")

    def test_review_notes_survive_regeneration(self):
        first = export_reports(self.checkpoint, self.output)
        review_path = first["paths"]["review"]
        rows = read_table(review_path)
        rows[0]["verified"] = "yes"
        rows[0]["notes"] = "Checked the original register"
        write_table(review_path, ["event_key", "verified", "notes"], rows)

        self.person["facts"].append(
            {
                "id": "newfact",
                "type": "http://gedcomx.org/Death",
                "date": {"original": "1912"},
            }
        )
        self.checkpoint.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.person]},
        )
        second = export_reports(self.checkpoint, self.output)
        updated = read_table(second["paths"]["review"])
        by_key = {item["event_key"]: item for item in updated}
        self.assertEqual(len(updated), len(rows) + 1)
        key = rows[0]["event_key"]
        self.assertEqual(by_key[key]["verified"], "yes")
        self.assertEqual(by_key[key]["notes"], "Checked the original register")

    def test_does_not_overwrite_unrecognized_manual_review_file(self):
        review_path = self.output.with_suffix(".review.csv")
        review_path.write_text("personal notes,not a checklist\nkeep me\n")
        with self.assertRaisesRegex(ValueError, "unexpected columns"):
            export_reports(self.checkpoint, self.output)
        self.assertIn("keep me", review_path.read_text())

    def test_does_not_modify_gedcom_or_checkpoint_records(self):
        self.output.write_text("original GEDCOM", encoding="utf-8")
        before = self.checkpoint.get_response("/platform/tree/persons?pids=AAAA-111")
        export_reports(self.checkpoint, self.output)
        self.assertEqual(self.output.read_text(), "original GEDCOM")
        self.assertEqual(
            before,
            self.checkpoint.get_response("/platform/tree/persons?pids=AAAA-111"),
        )

    def test_report_only_works_without_login(self):
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--language", "es", "--report-only", "-o", str(self.output)])
        self.assertIn("Creado un único documento de revisión", output.getvalue())
        report_path = self.output.with_suffix(".md")
        self.assertTrue(report_path.exists())
        self.assertFalse(self.output.with_suffix(".events.csv").exists())
        self.assertFalse(self.output.exists())

    def test_uncertain_year_handling(self):
        examples = [
            ({"original": "circa 1900"}, ("1900", "approximate")),
            ({"original": "about 1850"}, ("1850", "approximate")),
            ({"original": "BET 1900 AND 1910"}, ("", "range_or_relative")),
            ({"original": "1900/1901"}, ("", "range_or_relative")),
            ({"original": "02/03/1900"}, ("1900", "ambiguous_day_month")),
            ({"formal": "+1892"}, ("1892", "formal_year")),
            ({"original": "Unknown"}, ("", "undated_or_unparsed")),
        ]
        for date, expected in examples:
            with self.subTest(date=date):
                self.assertEqual(year_for_sorting(date), expected)


if __name__ == "__main__":
    unittest.main()
