"""Store FamilySearch login details in the operating system's credential vault.

Credentials are only saved when the user explicitly selects --remember-login.
Nothing is written to a project file, shell history, or GEDCOM checkpoint.
"""
from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "GetMyFamilyTree / FamilySearch"
USERNAME_ENTRY = "__saved_username__"


class CredentialStoreError(Exception):
    """The system credential store could not complete an operation."""


class CredentialStore:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else keyring

    def load(self):
        """Return (username, password) or None if no account is saved."""
        try:
            username = self.backend.get_password(SERVICE, USERNAME_ENTRY)
            if not username:
                return None
            password = self.backend.get_password(SERVICE, username)
        except KeyringError as exc:
            raise CredentialStoreError("Cannot access the system keyring.") from exc
        if not password:
            return None
        return username, password

    def save(self, username, password):
        """Save credentials only after a successful login and explicit opt-in."""
        try:
            # Write the secret first. A missing account pointer is harmless;
            # a pointer to a missing secret triggers the normal login prompt.
            self.backend.set_password(SERVICE, username, password)
            self.backend.set_password(SERVICE, USERNAME_ENTRY, username)
        except KeyringError as exc:
            raise CredentialStoreError("Cannot save credentials in the system keyring.") from exc

    def clear(self):
        """Remove the account this tool last saved; return whether it existed."""
        try:
            username = self.backend.get_password(SERVICE, USERNAME_ENTRY)
            if not username:
                return False
            try:
                self.backend.delete_password(SERVICE, username)
            except PasswordDeleteError:
                pass
            self.backend.delete_password(SERVICE, USERNAME_ENTRY)
        except KeyringError as exc:
            raise CredentialStoreError("Cannot remove credentials from the system keyring.") from exc
        return True
