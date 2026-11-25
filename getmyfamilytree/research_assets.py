"""Managed local research attachments with private, non-executable storage."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
MAX_ASSET_BYTES = 20 * 1024 * 1024
ASSET_ID = re.compile(r"[0-9a-f]{32}\Z")


class ResearchAssets:
    """Import and retrieve board documents without exposing arbitrary paths."""

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.directory = checkpoint.path.with_suffix(".assets")
        with checkpoint.lock, checkpoint.connection:
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_assets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _validate_id(self, asset_id):
        if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
            raise ValueError("Invalid asset identifier")

    def _disk_path(self, asset_id, filename):
        """Reject invalid stored filenames before accessing the filesystem."""
        suffix = Path(filename).suffix.lower()
        if suffix not in MEDIA_TYPES or filename != asset_id + suffix:
            raise ValueError("Invalid stored attachment filename")
        return self.directory / filename

    def get(self, asset_id):
        """Return safe metadata without disclosing its private disk path."""
        self._validate_id(asset_id)
        with self.checkpoint.lock:
            row = self.checkpoint.connection.execute(
                """
                SELECT id, name, filename, media_type, size, checksum, created_at
                FROM workspace_assets WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return {
            "id": row[0],
            "name": row[1],
            "media_type": row[3],
            "size": row[4],
            "checksum": row[5],
            "created_at": row[6],
        }

    def list(self):
        """List imported attachments without reading their file contents."""
        with self.checkpoint.lock:
            rows = self.checkpoint.connection.execute(
                "SELECT id FROM workspace_assets ORDER BY created_at, id"
            ).fetchall()
        return [self.get(row[0]) for row in rows]

    def read(self, asset_id):
        """Return bytes and media type for a previously imported attachment."""
        self._validate_id(asset_id)
        with self.checkpoint.lock:
            row = self.checkpoint.connection.execute(
                "SELECT filename, media_type FROM workspace_assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        filename, media_type = row
        # Disk names are generated internally, never supplied by the caller.
        return self._disk_path(asset_id, filename).read_bytes(), media_type

    def import_bytes(self, name, data):
        """Copy a PDF or image into the private workspace assets directory."""
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise ValueError("Attachment name must be a short non-empty string")
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ASSET_BYTES:
            raise ValueError("Attachment size is invalid")

        display_name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        suffix = Path(display_name).suffix.lower()
        if not display_name or suffix not in MEDIA_TYPES:
            raise ValueError("Only PDF and supported image files can be imported")

        asset_id = uuid4().hex
        disk_name = asset_id + suffix
        timestamp = datetime.now(timezone.utc).isoformat()
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)

        path = self.directory / disk_name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            with self.checkpoint.lock, self.checkpoint.connection:
                self.checkpoint.connection.execute(
                    """
                    INSERT INTO workspace_assets (
                        id, name, filename, media_type, size, checksum, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        display_name,
                        disk_name,
                        MEDIA_TYPES[suffix],
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                        timestamp,
                    ),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.get(asset_id)

    def delete(self, asset_id):
        """Delete an unattached asset without affecting GEDCOM or source data."""
        self._validate_id(asset_id)
        with self.checkpoint.lock, self.checkpoint.connection:
            row = self.checkpoint.connection.execute(
                "SELECT filename FROM workspace_assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise KeyError(asset_id)
            path = self._disk_path(asset_id, row[0])
            attached = self.checkpoint.connection.execute(
                "SELECT 1 FROM workspace_cards WHERE asset_id = ? LIMIT 1",
                (asset_id,),
            ).fetchone()
            if attached is not None:
                raise ValueError("Remove the attachment from board cards first")
            self.checkpoint.connection.execute(
                "DELETE FROM workspace_assets WHERE id = ?", (asset_id,)
            )
        path.unlink(missing_ok=True)
