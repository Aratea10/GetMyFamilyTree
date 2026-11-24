
"""Regression tests for GEDCOM tree worker management"""

import unittest
from unittest.mock import Mock, patch

from getmyfamilytree.gedcom_tree import ExportTree


class ExportTreeTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.lang = "en"

        self.tree = ExportTree(
            self.session,
            no_sources=True,
            no_memories=True,
        )

        self.person = {"id": "AAAA-111"}

        self.session.get_url.return_value = {
            "persons": [self.person]
        }

    def test_loads_people_without_creating_event_loop(self):
        individual = Mock()

        with (
            patch(
                "getmyfamilytree.gedcom_tree.Indi",
                return_value=individual,
            ),
            patch(
                "asyncio.new_event_loop",
                side_effect=AssertionError(
                    "Unexpected event loop creation"
                ),
            ),
        ):
            self.tree.add_indis(["AAAA-111"])

        self.assertIs(
            self.tree.indi["AAAA-111"],
            individual,
        )

        individual.add_data.assert_called_once_with(
            self.person
        )

        self.session.get_url.assert_called_once_with(
            "/platform/tree/persons?pids=AAAA-111"
        )

    def test_existing_people_are_not_loaded_again(self):
        self.tree.indi["AAAA-111"] = Mock()

        self.tree.add_indis(["AAAA-111"])

        self.session.get_url.assert_not_called()

    def test_worker_errors_are_propagated(self):
        individual = Mock()

        individual.add_data.side_effect = RuntimeError(
            "Person loading failed"
        )

        with patch(
            "getmyfamilytree.gedcom_tree.Indi",
            return_value=individual,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Person loading failed",
            ):
                self.tree.add_indis(["AAAA-111"])

    def test_empty_input_does_not_request_data(self):
        self.tree.add_indis([])

        self.session.get_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
