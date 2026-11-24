import tempfile
import unittest
from pathlib import Path

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_recommendations import build_recommendations


class ResearchPerformanceTests(unittest.TestCase):
    """Regression test: large linear genealogy tree"""

    def setUp(self):
        # Create a temporary database
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "tree.sqlite"
        self.store = Checkpoint(db_path)
        self.addCleanup(self.store.close)

        # Create a linear chain of 200 individuals (P000-000 as origin)
        origin = "P000-000"
        self.store.set_origin(origin)
        # Seed all IDs in advance
        ids = [origin] + [f"P{i:03d}-{i:03d}" for i in range(1, 200)]
        self.store.seed(ids)

        # Save origin person without parents
        self.store.save_response(
            PERSON_URL + origin,
            {"persons": [{"id": origin, "display": {"name": origin}}]},
        )
        self.store.finish(origin, [])

        # Create a single-descendant chain
        prev = origin
        for i in range(1, 200):
            pid = f"P{i:03d}-{i:03d}"
            self.store.save_response(
                PERSON_URL + pid,
                {"persons": [{"id": pid, "display": {"name": pid}}]},
            )
            # Link this person as child of the previous
            self.store.save_response(
                FAMILIES_URL.format(pid),
                {
                    "childAndParentsRelationships": [
                        {
                            "child": {"resourceId": pid},
                            "parent1": {"resourceId": prev},
                        }
                    ]
                },
            )
            self.store.finish(pid, [])
            prev = pid

    def test_last_descendant(self):
        # Compute recommendations for all seeded people
        recos = build_recommendations(self.store, self.store.completed_ids())
        total = len(recos)
        # Expect 200 people in total
        self.assertEqual(total, 200)
        # Check the last person (P199-199)
        last = "P199-199"
        self.assertIn(last, recos)
        self.assertEqual(recos[last]["category"], "descendant")
        self.assertEqual(recos[last]["generation"], 199)


if __name__ == "__main__":
    unittest.main()
