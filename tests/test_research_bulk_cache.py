
"""Tests for efficient bulk loading of cached genealogy records"""

import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.research_recommendations import build_recommendations


class ResearchBulkCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Checkpoint(
            Path(self.temp.name) / "bulk.sqlite"
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_bulk_loading_handles_missing_and_duplicate_keys(self):
        key = PERSON_URL + "AAAA-111"

        self.store.save_response(
            key,
            {"persons": [{"id": "AAAA-111"}]},
        )

        result = self.store.get_responses([
            key,
            key,
            PERSON_URL + "BBBB-222",
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[key]["persons"][0]["id"],
            "AAAA-111",
        )
        self.assertEqual(self.store.get_responses([]), {})

    def test_large_recommendations_use_batched_reads(self):
        identifiers = [
            f"P{index:03d}-{index:03d}"
            for index in range(620)
        ]

        self.store.set_origin(identifiers[0])
        self.store.seed(identifiers)

        for fid in identifiers:
            self.store.save_response(
                PERSON_URL + fid,
                {
                    "persons": [{
                        "id": fid,
                        "display": {"name": fid},
                    }]
                },
            )
            self.store.finish(fid, [])

        queries = []

        def trace(statement):
            sql = statement.strip().upper()

            if (
                sql.startswith("SELECT")
                and "FROM RESPONSES" in sql
            ):
                queries.append(statement)

        self.store.connection.set_trace_callback(trace)

        try:
            result = build_recommendations(
                self.store,
                identifiers,
            )
        finally:
            self.store.connection.set_trace_callback(None)

        self.assertEqual(len(result), 620)
        self.assertEqual(
            result[identifiers[0]]["category"],
            "origin",
        )

        # 620 person keys + 620 family keys = 1240 keys.
        # With batches of 400, at most four response queries.
        self.assertGreaterEqual(len(queries), 1)
        self.assertLessEqual(len(queries), 4)


if __name__ == "__main__":
    unittest.main()
