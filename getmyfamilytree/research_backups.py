"""Atomic private backups of SQLite research data and imported documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BACKUP_ID = re.compile(r"[0-9a-f]{32}\Z")
BACKUP_VERSION = 1


class ResearchBackups:
    """Create portable SQLite and attachments backups without destructive restore."""

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.directory = checkpoint.path.with_suffix(".backups")

    @staticmethod
    def _check_id(identifier):
        if not isinstance(identifier, str) or not BACKUP_ID.fullmatch(identifier):
            raise ValueError("Invalid backup identifier")

    def _path(self, identifier):
        self._check_id(identifier)
        return self.directory / f"{identifier}.zip"

    def status(self):
        """Check that the active SQLite database passes its quick integrity check."""
        with self.checkpoint.lock:
            rows = self.checkpoint.connection.execute("PRAGMA quick_check").fetchall()
        issues = [item[0] for item in rows if item[0] != "ok"]
        return {"ok": not issues, "issues": issues}

    def _assets_in_snapshot(self, snapshot):
        """List only internally generated, checksum-verified attachment paths."""
        tables = {
            row[0]
            for row in snapshot.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "workspace_assets" not in tables:
            return []

        items = []
        for asset_id, filename, checksum, size in snapshot.execute(
            "SELECT id, filename, checksum, size FROM workspace_assets"
        ):
            if (
                not isinstance(asset_id, str)
                or not BACKUP_ID.fullmatch(asset_id)
                or not isinstance(filename, str)
                or filename != asset_id + Path(filename).suffix
                or "/" in filename
                or "\\" in filename
            ):
                raise ValueError("Invalid stored asset metadata")

            path = self.checkpoint.path.with_suffix(".assets") / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError("An imported document is missing or unsafe")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if path.stat().st_size != size or digest.hexdigest() != checksum:
                raise ValueError("An imported document failed checksum validation")
            items.append((filename, path))
        return items

    def create(self):
        """Build a consistent ZIP archive and atomically publish it."""
        backup_id = uuid4().hex
        target = self._path(backup_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        temporary = target.with_suffix(".zip.tmp")

        with tempfile.TemporaryDirectory() as folder:
            snapshot_path = Path(folder) / "research.sqlite"
            try:
                with self.checkpoint.lock:
                    with closing(sqlite3.connect(snapshot_path)) as snapshot:
                        self.checkpoint.connection.backup(snapshot)
                        issues = [
                            row[0]
                            for row in snapshot.execute("PRAGMA quick_check")
                            if row[0] != "ok"
                        ]
                        if issues:
                            raise ValueError("SQLite integrity check failed")
                        assets = self._assets_in_snapshot(snapshot)

                    metadata = {
                        "format": "GetMyFamilyTree research backup",
                        "version": BACKUP_VERSION,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "database": self.checkpoint.path.name,
                        "assets": [filename for filename, _ in assets],
                    }
                    with zipfile.ZipFile(
                        temporary, mode="x", compression=zipfile.ZIP_DEFLATED
                    ) as archive:
                        archive.write(snapshot_path, arcname="research.sqlite")
                        for filename, path in assets:
                            archive.write(path, arcname=f"assets/{filename}")
                        archive.writestr(
                            "manifest.json",
                            json.dumps(metadata, ensure_ascii=False, indent=2),
                        )

                    os.chmod(temporary, 0o600)
                    temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

        return self.get(backup_id)

    def get(self, identifier):
        """Return metadata for a backup without revealing its private path."""
        path = self._path(identifier)
        if not path.is_file():
            raise KeyError(identifier)
        return {
            "id": identifier,
            "size": path.stat().st_size,
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(),
        }

    def list(self):
        """Return backup metadata, most recently created first."""
        if not self.directory.is_dir():
            return []
        result = []
        for path in self.directory.glob("*.zip"):
            identifier = path.stem
            if BACKUP_ID.fullmatch(identifier) and not path.is_symlink():
                result.append(self.get(identifier))
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def read(self, identifier):
        """Return the complete ZIP archive for authenticated download."""
        path = self._path(identifier)
        if not path.is_file() or path.is_symlink():
            raise KeyError(identifier)
        return path.read_bytes()

    def delete(self, identifier):
        """Remove a previously created backup, leaving the checkpoint untouched."""
        path = self._path(identifier)
        if not path.is_file() or path.is_symlink():
            raise KeyError(identifier)
        path.unlink()
