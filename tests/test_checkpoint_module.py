"""Regression tests for the standalone SQLite checkpoint module"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.cli import Checkpoint as LegacyCheckpoint
from getmyfamilytree.reports import PERSON_URL


class CheckpointModuleTests(unittest.TestCase):
    def test_legacy_import_uses_same_class(self):
        self.assertIs(Checkpoint, LegacyCheckpoint)

    def test_persisted_data_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint.sqlite"
            person_id = "AAAA-111"
            request_key = PERSON_URL + person_id

            checkpoint = Checkpoint(database)

            try:
                checkpoint.set_origin(person_id)
                checkpoint.seed([person_id])
                checkpoint.save_response(
                    request_key,
                    {
                        "persons": [
                            {
                                "id": person_id,
                                "display": {"name": "Test Person"},
                            }
                        ]
                    },
                )
                checkpoint.finish(person_id, [])
                checkpoint.save_review_notes(
                    {person_id: "Verified birth record"}
                )
            finally:
                checkpoint.close()

            reopened = Checkpoint(database)

            try:
                self.assertEqual(
                    reopened.get_origin(),
                    person_id,
                )

                self.assertEqual(
                    reopened.completed_ids(),
                    [person_id],
                )

                self.assertEqual(
                    reopened.get_response(request_key)["persons"][0]["id"],
                    person_id,
                )

                self.assertEqual(
                    reopened.load_review_notes()[person_id],
                    "Verified birth record",
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()

