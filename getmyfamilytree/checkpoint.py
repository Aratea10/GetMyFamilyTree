"""Persistent SQLite storage for genealogy discovery and research"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock

from getmyfamilytree.i18n import translate


class Checkpoint:
    """Persistent discovery queue and successful FamilySearch responses"""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        os.chmod(self.path, 0o600)
        with self.lock, self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS people "
                "(fid TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending')"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS responses "
                "(request_key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS people_by_status ON people(status)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS review_notes "
                "(fid TEXT PRIMARY KEY, content TEXT NOT NULL)"
            )

    def load_review_notes(self):
        """Keep manually verified notes when switching download modes."""
        with self.lock:
            return dict(
                self.connection.execute(
                    "SELECT fid, content FROM review_notes"
                ).fetchall()
            )

    def save_review_notes(self, notes):
        with self.lock, self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO review_notes (fid, content) VALUES (?, ?)",
                ((fid, text) for fid, text in notes.items()),
            )

    def close(self):
        with self.lock:
            self.connection.close()

    def set_origin(self, identifier, language="en"):
        """Reject a checkpoint belonging to a different starting person."""
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = 'origin'"
            ).fetchone()
            if row and row[0] != identifier:
                raise ValueError(
                    translate(
                        language,
                        "error_checkpoint_origin",
                        existing=row[0],
                        requested=identifier,
                    )
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('origin', ?)",
                (identifier,),
            )

    def get_origin(self):
        """Return the starting FamilySearch ID stored in this checkpoint."""
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = 'origin'"
            ).fetchone()
        return row[0] if row else None

    def seed(self, ids):
        with self.lock, self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO people (fid) VALUES (?)",
                ((fid,) for fid in ids),
            )

    def retry_failed(self):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE people SET status = 'pending' WHERE status = 'failed'"
            )

    def next_pending(self):
        with self.lock:
            row = self.connection.execute(
                "SELECT fid FROM people WHERE status = 'pending' ORDER BY rowid LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def count(self, status):
        with self.lock:
            return self.connection.execute(
                "SELECT COUNT(*) FROM people WHERE status = ?",
                (status,),
            ).fetchone()[0]

    def completed_ids(self):
        with self.lock:
            return [
                row[0]
                for row in self.connection.execute(
                    "SELECT fid FROM people WHERE status = 'done' ORDER BY rowid"
                )
            ]

    def get_response(self, key):
        with self.lock:
            row = self.connection.execute(
                "SELECT data FROM responses WHERE request_key = ?",
                (key,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_responses(self, keys):
        """Read cached JSON responses in bounded SQLite batches"""
        keys = list(dict.fromkeys(keys))

        if not keys:
            return {}

        found = {}
        batch_size = 400

        with self.lock:
            for start in range(0, len(keys), batch_size):
                batch = keys[start : start + batch_size]

                placeholders = ",".join("?" for _ in batch)

                rows = self.connection.execute(
                    f"""
                    SELECT request_key, data
                    FROM responses
                    WHERE request_key IN ({placeholders})
                    """,
                    batch,
                ).fetchall()

                for request_key, data in rows:
                    found[request_key] = json.loads(data)

        return found

    def save_response(self, key, document):
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO responses (request_key, data) VALUES (?, ?)",
                (
                    key,
                    json.dumps(
                        document,
                        ensure_ascii=False,
                    ),
                ),
            )

    def finish(self, fid, discovered):
        with self.lock, self.connection:
            added = self.connection.executemany(
                "INSERT OR IGNORE INTO people (fid) VALUES (?)",
                ((person,) for person in sorted(discovered) if person != fid),
            ).rowcount
            self.connection.execute(
                "UPDATE people SET status = 'done' WHERE fid = ?",
                (fid,),
            )
            return added

    def fail(self, fid):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE people SET status = 'failed' WHERE fid = ?",
                (fid,),
            )
