"""Recovery tests for consistent private database and attachment backups."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.research_backups import ResearchBackups
from getmyfamilytree.research_workspace import ResearchWorkspace


class ResearchBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "tree.sqlite"
        self.checkpoint = Checkpoint(self.path)
        self.addCleanup(self.checkpoint.close)
        self.checkpoint.set_origin("SELF-111")
        self.checkpoint.seed(["SELF-111"])
        self.checkpoint.save_response(
            PERSON_URL + "SELF-111",
            {"persons": [{"id": "SELF-111", "display": {"name": "Family"}}]},
        )
        self.checkpoint.finish("SELF-111", [])
        self.workspace = ResearchWorkspace(self.checkpoint)
        self.backups = ResearchBackups(self.checkpoint)

    def test_backup_contains_valid_sqlite_and_imported_attachments(self):
        attachment = self.workspace.assets.import_bytes(
            "photo.png", b"fake-but-verifiable-image"
        )
        self.workspace.create_card(
            {"kind": "photo", "title": "Photo", "asset_id": attachment["id"]},
            people=["SELF-111"],
        )
        self.workspace.set_person_note("SELF-111", "Find more records")

        metadata = self.backups.create()
        self.assertGreater(metadata["size"], 0)
        self.assertEqual(self.backups.list()[0]["id"], metadata["id"])
        self.assertEqual(self.backups.status(), {"ok": True, "issues": []})

        with zipfile.ZipFile(io.BytesIO(self.backups.read(metadata["id"]))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(
                archive.read("assets/" + attachment["id"] + ".png"),
                b"fake-but-verifiable-image",
            )
            restored = Path(self.temporary.name) / "restored.sqlite"
            restored.write_bytes(archive.read("research.sqlite"))

        with closing(sqlite3.connect(restored)) as connection:
            origin = connection.execute(
                "SELECT value FROM metadata WHERE key='origin'"
            ).fetchone()[0]
            note = connection.execute(
                "SELECT content FROM workspace_person_notes WHERE fid='SELF-111'"
            ).fetchone()[0]
            card_count = connection.execute(
                "SELECT COUNT(*) FROM workspace_cards"
            ).fetchone()[0]
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
        self.assertEqual(origin, "SELF-111")
        self.assertEqual(note, "Find more records")
        self.assertEqual(card_count, 1)
        self.assertEqual(check, "ok")

        self.backups.delete(metadata["id"])
        self.assertEqual(self.backups.list(), [])
        with self.assertRaises(KeyError):
            self.backups.read(metadata["id"])

    def test_backup_rejects_missing_or_modified_documents(self):
        attachment = self.workspace.assets.import_bytes("image.png", b"original")
        self.workspace.assets._disk_path(
            attachment["id"], attachment["id"] + ".png"
        ).write_bytes(b"tampered")

        with self.assertRaisesRegex(ValueError, "checksum"):
            self.backups.create()
        self.assertEqual(self.backups.list(), [])

    def test_backup_rejects_invalid_identifiers(self):
        with self.assertRaises(ValueError):
            self.backups.read("../tree.sqlite")
        with self.assertRaises(ValueError):
            self.backups.delete("missing")


if __name__ == "__main__":
    unittest.main()
