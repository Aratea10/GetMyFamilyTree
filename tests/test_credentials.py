"""Tests for opt-in FamilySearch credential storage and reuse"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from getmyfamilytree.cli import main
from getmyfamilytree.credentials import (
    SERVICE,
    USERNAME_ENTRY,
    CredentialStore,
)
from getmyfamilytree.scopes import ScopeResult


class MemoryKeyring:
    """In-memory test backend: no real passwords or operating-system access."""

    def __init__(self):
        self.entries = {}

    def get_password(self, service, username):
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        self.entries[service, username] = password

    def delete_password(self, service, username):
        del self.entries[service, username]


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryKeyring()
        self.store = CredentialStore(self.backend)

    def test_does_not_have_credentials_before_explicit_save(self):
        self.assertIsNone(self.store.load())

    def test_saves_and_reuses_both_username_and_password(self):
        self.store.save("example-user", "example-password")
        self.assertEqual(self.store.load(), ("example-user", "example-password"))
        self.assertEqual(self.backend.entries[SERVICE, USERNAME_ENTRY], "example-user")
        self.assertEqual(
            self.backend.entries[SERVICE, "example-user"], "example-password"
        )

    def test_switching_accounts_removes_previous_password(self):
        self.store.save("first-user", "first-password")
        self.store.save("second-user", "second-password")
        self.assertEqual(self.store.load(), ("second-user", "second-password"))
        self.assertNotIn((SERVICE, "first-user"), self.backend.entries)

    def test_removes_both_saved_entries(self):
        self.store.save("example-user", "example-password")
        self.assertTrue(self.store.clear())
        self.assertIsNone(self.store.load())
        self.assertFalse(self.store.clear())

    def test_missing_password_falls_back_to_login_prompt(self):
        self.backend.set_password(SERVICE, USERNAME_ENTRY, "example-user")
        self.assertIsNone(self.store.load())


class LoginFlowTests(unittest.TestCase):
    def run_command(
        self, options, store, *, username_prompt=None, password_prompt=None
    ):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.ged"
            argv = ["-i", "AAAA-111", "-o", str(output), *list(options)]
            session = Mock(logged=True)
            with (
                patch("getmyfamilytree.cli.CredentialStore", return_value=store),
                patch(
                    "getmyfamilytree.cli.Session", return_value=session
                ) as session_factory,
                patch(
                    "getmyfamilytree.cli.crawl_scope",
                    return_value=ScopeResult(
                        ids=[], pending=0, failed=0, exhausted=True, reused=0
                    ),
                ),
                patch("getmyfamilytree.cli.assemble", return_value=(1, 0)),
                patch(
                    "builtins.input",
                    side_effect=username_prompt
                    or AssertionError("Username prompt not expected"),
                ),
                patch(
                    "getmyfamilytree.cli.getpass.getpass",
                    side_effect=password_prompt
                    or AssertionError("Password prompt not expected"),
                ),
            ):
                main(argv)
            return session_factory

    def test_existing_keyring_login_skips_both_prompts(self):
        store = Mock()
        store.load.return_value = ("example-user", "example-password")
        factory = self.run_command([], store)
        self.assertEqual(
            factory.call_args.args[:2], ("example-user", "example-password")
        )
        store.save.assert_not_called()

    def test_first_login_is_not_saved_without_opt_in(self):
        store = Mock()
        store.load.return_value = None
        self.run_command(
            [],
            store,
            username_prompt=["example-user"],
            password_prompt=["example-password"],
        )
        store.save.assert_not_called()

    def test_remember_login_saves_only_after_successful_authentication(self):
        store = Mock()
        store.load.return_value = None
        self.run_command(
            ["--remember-login"],
            store,
            username_prompt=["example-user"],
            password_prompt=["example-password"],
        )
        store.save.assert_called_once_with("example-user", "example-password")

    def test_forget_login_does_not_require_tree_id_or_output(self):
        store = Mock()
        store.clear.return_value = True
        with patch("getmyfamilytree.cli.CredentialStore", return_value=store):
            main(["--forget-login"])
        store.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
