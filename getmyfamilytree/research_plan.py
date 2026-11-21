"""Persistent research tracking, independent of FamilySearch downloads"""

from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.scopes import _person_name


VALID_STATUSES = {"pending", "in_progress", "done", "postponed"}


class ResearchPlan:
    """Track research progress without changing the download checkpoint"""

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint

        with checkpoint.lock, checkpoint.connection:
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_tasks (
                    fid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN (
                            'pending', 'in_progress', 'done', 'postponed'
                        )),
                    priority INTEGER NOT NULL DEFAULT 1
                        CHECK(priority BETWEEN 0 AND 2),
                    position INTEGER NOT NULL
                )
                """
            )

    def sync(self):
        """Add downloaded people without resetting existing progress"""
        identifiers = self.checkpoint.completed_ids()

        with self.checkpoint.lock, self.checkpoint.connection:
            self.checkpoint.connection.executemany(
                """
                INSERT OR IGNORE INTO research_tasks (fid, position)
                VALUES (?, ?)
                """,
                enumerate_ids(identifiers),
            )

        return len(identifiers)

    def set_status(self, fid, status):
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported research status: {status}")

        with self.checkpoint.lock, self.checkpoint.connection:
            cursor = self.checkpoint.connection.execute(
                "UPDATE research_tasks SET status = ? WHERE fid = ?",
                (status, fid),
            )
            if cursor.rowcount != 1:
                raise KeyError(fid)

    def set_priority(self, fid, priority):
        """Use 0 for high, 1 for normal, and 2 for low priority."""
        if priority not in (0, 1, 2):
            raise ValueError(f"Unsupported priority: {priority}")

        with self.checkpoint.lock, self.checkpoint.connection:
            cursor = self.checkpoint.connection.execute(
                "UPDATE research_tasks SET priority = ? WHERE fid = ?",
                (priority, fid),
            )
            if cursor.rowcount != 1:
                raise KeyError(fid)

    def items(self):
        with self.checkpoint.lock:
            rows = self.checkpoint.connection.execute(
                """
                SELECT fid, status, priority
                FROM research_tasks
                ORDER BY
                    CASE WHEN status = 'postponed' THEN 1 ELSE 0 END,
                    priority,
                    position,
                    fid
                """
            ).fetchall()

        result = []

        for fid, status, priority in rows:
            document = self.checkpoint.get_response(PERSON_URL + fid) or {}

            result.append(
                {
                    "id": fid,
                    "name": _person_name(document, fid),
                    "status": status,
                    "priority": priority,
                }
            )

        return result


def enumerate_ids(identifiers):
    for position, fid in enumerate(identifiers):
        yield fid, position
