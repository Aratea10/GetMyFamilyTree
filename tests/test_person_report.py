"""The default review keeps everything with each person in one text document"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from getmyfamilytree.cli import Checkpoint, main, resolve_output_path
from getmyfamilytree.person_report import write_person_report


class PersonReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "Tree"
        self.output = self.folder / "GetMyFamilyTree.ged"
        self.store = Checkpoint(self.folder / "GetMyFamilyTree.sqlite")
        self.store.seed(["AAAA-111", "BBBB-222"])

        self.alice = {
            "id": "AAAA-111",
            "display": {"name": "María da Silva"},
            "living": False,
            "facts": [
                {
                    "id": "death",
                    "type": "http://gedcomx.org/Death",
                    "date": {"original": "12 OCT 1950", "formal": "+1950-10-12"},
                },
                {
                    "id": "birth",
                    "type": "http://gedcomx.org/Birth",
                    "date": {"original": "12 MAR 1892", "formal": "+1892-03-12"},
                    "place": {"original": "Cangas de Morrazo"},
                    "sources": [{"descriptionId": "SRC1"}],
                },
            ],
            "sources": [{"descriptionId": "SRC1"}],
            "evidence": [{"id": "M123-1"}],
        }
        self.store.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.alice]},
        )
        self.store.save_response(
            "/platform/tree/persons?pids=BBBB-222",
            {
                "persons": [
                    {
                        "id": "BBBB-222",
                        "display": {"name": "José"},
                        "facts": [],
                        "living": False,
                    }
                ]
            },
        )
        self.store.save_response(
            "/platform/tree/persons/AAAA-111/families",
            {
                "relationships": [
                    {
                        "id": "couple-1",
                        "type": "http://gedcomx.org/Couple",
                        "person1": {"resourceId": "AAAA-111"},
                        "person2": {"resourceId": "BBBB-222"},
                    }
                ]
            },
        )
        self.store.save_response(
            "0::/platform/tree/persons/AAAA-111/sources",
            {
                "sourceDescriptions": [
                    {
                        "id": "SRC1",
                        "titles": [{"value": "Libro de bautismos"}],
                        "citations": [{"value": "Parroquia de Darbo, folio 4"}],
                        "about": "https://www.familysearch.org/ark:/61903/1:1:ABCDE",
                    }
                ]
            },
        )
        self.store.save_response(
            "0::/platform/tree/persons/AAAA-111/notes",
            {
                "persons": [
                    {
                        "notes": [
                            {"id": "NOTE1", "text": "Comprobar asiento original"}
                        ],
                    }
                ]
            },
        )
        self.store.save_response(
            "0::/platform/memories/memories/M123",
            {
                "sourceDescriptions": [
                    {
                        "mediaType": "image/jpeg",
                        "titles": [{"value": "Fotografía de familia"}],
                        "about": "https://www.familysearch.org/photos/artifacts/123",
                    }
                ]
            },
        )
        self.store.finish("AAAA-111", [])
        self.store.finish("BBBB-222", [])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_single_document_contains_each_person_with_every_section(self):
        report = write_person_report(self.store, self.output, "es")
        path = report["path"]
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path, self.folder / "GetMyFamilyTree.md")
        self.assertEqual(report["people"], 2)
        self.assertTrue(path.exists())
        self.assertFalse(self.output.exists())
        self.assertIn("## Índice cronológico", text)
        self.assertIn("### María da Silva", text)
        self.assertIn("### José", text)
        self.assertIn("#### Parentescos", text)
        self.assertIn("Pareja: José", text)
        self.assertIn("#### Fuentes", text)
        self.assertIn("Libro de bautismos", text)
        self.assertIn("Parroquia de Darbo, folio 4", text)
        self.assertIn("#### Notas", text)
        self.assertIn("Comprobar asiento original", text)
        self.assertIn("#### Recuerdos", text)
        self.assertIn("Fotografía de familia", text)
        self.assertIn("#### Mi revisión", text)
        self.assertIn("<!-- review:start AAAA-111 -->", text)
        self.assertLess(text.index("1892 |"), text.index("1950 |"))
        self.assertEqual(text.count("### María da Silva"), 1)

    def test_review_checkboxes_and_notes_survive_reexport(self):
        path = write_person_report(self.store, self.output, "es")["path"]
        original = path.read_text(encoding="utf-8")
        original = original.replace(
            "<!-- review:start AAAA-111 -->\n",
            "<!-- review:start AAAA-111 -->\nComprobado con la partida original.\n",
        )
        original = original.replace(
            "- [ ] 1892",
            "- [x] 1892",
        )
        path.write_text(original, encoding="utf-8")

        self.alice["facts"].append(
            {
                "id": "residence",
                "type": "http://gedcomx.org/Residence",
                "date": {"original": "1900"},
            }
        )
        self.store.save_response(
            "/platform/tree/persons?pids=AAAA-111",
            {"persons": [self.alice]},
        )
        new_text = write_person_report(self.store, self.output, "es")["path"].read_text(
            encoding="utf-8"
        )
        self.assertIn("Comprobado con la partida original.", new_text)
        self.assertIn("- [x] 1892", new_text)
        self.assertIn("1900", new_text)
        self.assertEqual(new_text.count("Comprobado con la partida original."), 1)
        self.assertEqual(new_text.count("AAAA-111:residence"), 2)

    def test_review_notes_survive_switching_scopes(self):
        # Start with both people, then export one, then restore both.
        path = write_person_report(self.store, self.output, "es")["path"]
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- review:start BBBB-222 -->\n",
            "<!-- review:start BBBB-222 -->\nHe comprobado el registro de José.\n",
        )
        path.write_text(text, encoding="utf-8")

        write_person_report(self.store, self.output, "es", selected_ids=["AAAA-111"])
        limited = path.read_text(encoding="utf-8")
        self.assertNotIn("He comprobado el registro de José.", limited)

        write_person_report(
            self.store,
            self.output,
            "es",
            selected_ids=["AAAA-111", "BBBB-222"],
        )
        restored = path.read_text(encoding="utf-8")
        self.assertIn("He comprobado el registro de José.", restored)
        self.assertEqual(
            self.store.load_review_notes()["BBBB-222"].strip(),
            "He comprobado el registro de José.",
        )

    def test_english_language_does_not_change_person_data(self):
        text = write_person_report(self.store, self.output, "en")["path"].read_text(
            encoding="utf-8"
        )
        self.assertIn("## Chronological index", text)
        self.assertIn("#### Relationships", text)
        self.assertIn("María da Silva", text)
        self.assertIn("Cangas de Morrazo", text)

    def test_report_only_directory_does_not_download_or_create_gedcom(self):
        with patch("getmyfamilytree.cli.Session", side_effect=AssertionError):
            main(["--language", "es", "--report-only", "-o", str(self.folder)])
        self.assertTrue((self.folder / "GetMyFamilyTree.md").exists())
        self.assertFalse(self.output.exists())
        self.assertFalse((self.folder / "GetMyFamilyTree.events.csv").exists())

    def test_export_folder_and_explicit_file_paths(self):
        self.assertEqual(resolve_output_path(self.folder), self.output)
        self.assertEqual(resolve_output_path(self.output), self.output)
        with self.assertRaises(ValueError):
            resolve_output_path(self.folder / "tree.xlsx")


if __name__ == "__main__":
    unittest.main()
