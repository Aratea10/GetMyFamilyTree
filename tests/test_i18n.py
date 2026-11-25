"""Regression tests for English/Spanish terminal messages and help"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from string import Formatter
from unittest.mock import Mock, patch

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.cli import crawl, main
from getmyfamilytree.i18n import MESSAGES, resolve_language, translate
from getmyfamilytree.scopes import ScopeResult


class TranslationTests(unittest.TestCase):
    def test_explicit_languages(self):
        self.assertEqual(resolve_language("es"), "es")
        self.assertEqual(resolve_language("en"), "en")

    def test_automatic_system_language_and_fallback(self):
        self.assertEqual(resolve_language("auto", "es_ES.UTF-8"), "es")
        self.assertEqual(resolve_language("auto", "en_US.UTF-8"), "en")
        self.assertEqual(resolve_language("auto", "fr_FR.UTF-8"), "en")
        self.assertEqual(resolve_language("auto", "C.UTF-8"), "en")

    def test_message_keys_and_format_placeholders_match(self):
        self.assertEqual(set(MESSAGES["en"]), set(MESSAGES["es"]))
        parser = Formatter()
        for key, english in MESSAGES["en"].items():
            with self.subTest(message=key):
                spanish = MESSAGES["es"][key]

                def variables(text):
                    return {field for _, field, _, _ in parser.parse(text) if field}

                self.assertEqual(variables(english), variables(spanish))

    def test_genealogical_names_remain_unchanged(self):
        line = translate(
            "es",
            "found",
            name="María da Silva",
            fid="AAAA-111",
            added=2,
            pending=8,
        )
        self.assertIn("María da Silva", line)
        self.assertIn("AAAA-111", line)
        self.assertIn("+2 familiares nuevos", line)

    def test_spanish_cli_help(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exit_info:
            main(["--language", "es", "--help"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("Idioma de la interfaz", output.getvalue())
        self.assertIn("ID de la persona en FamilySearch", output.getvalue())

    def test_english_cli_help(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--language", "en", "--help"])
        self.assertIn("Interface language", output.getvalue())

    def test_language_switch_alias(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--lang", "es", "--help"])
        self.assertIn("Idioma de la interfaz", output.getvalue())

    def test_spanish_progress_does_not_change_person_names(self):
        class OnePersonSession:
            def get_url(self, url, headers=None, no_api=False):
                if "pids=" in url:
                    return {
                        "persons": [
                            {"id": "AAAA-111", "display": {"name": "José García"}},
                        ]
                    }
                return {"persons": [], "relationships": []}

        with tempfile.TemporaryDirectory() as folder:
            store = Checkpoint(Path(folder) / "family.sqlite")
            try:
                store.seed(["AAAA-111"])
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertTrue(
                        crawl(
                            store,
                            OnePersonSession(),
                            max_people=1,
                            language="es",
                        )
                    )
                self.assertIn("Explorando 1/1: AAAA-111", output.getvalue())
                self.assertIn("José García", output.getvalue())
                self.assertIn("Exploración terminada", output.getvalue())
            finally:
                store.close()

    def test_saved_login_and_messages_use_requested_language(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            fake_credentials = Mock()
            fake_credentials.load.return_value = ("example-user", "example-password")
            fake_session = Mock(logged=True)
            with (
                patch(
                    "getmyfamilytree.cli.CredentialStore", return_value=fake_credentials
                ),
                patch("getmyfamilytree.cli.Session", return_value=fake_session),
                patch(
                    "getmyfamilytree.cli.crawl_scope",
                    return_value=ScopeResult(
                        ids=[], pending=0, failed=0, exhausted=True, reused=0
                    ),
                ) as fake_crawl,
                patch("getmyfamilytree.cli.assemble", return_value=(1, 0)),
                redirect_stderr(output),
            ):
                main(
                    [
                        "--language",
                        "es",
                        "-i",
                        "AAAA-111",
                        "-o",
                        str(Path(folder) / "family.ged"),
                    ]
                )
            self.assertIn("Se está usando el inicio de sesión", output.getvalue())
            self.assertIn("Guardadas 1 personas", output.getvalue())
            self.assertEqual(fake_crawl.call_args.kwargs["language"], "es")


if __name__ == "__main__":
    unittest.main()
