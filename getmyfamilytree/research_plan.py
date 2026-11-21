"""Persistent research tracking, independent of FamilySearch downloads"""

from getmyfamilytree.research_recommendations import build_recommendations


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
        """Add new people without changing existing task positions"""
        identifiers = self.checkpoint.completed_ids()

        with self.checkpoint.lock, self.checkpoint.connection:
            existing = {
                row[0]
                for row in self.checkpoint.connection.execute(
                    "SELECT fid FROM research_tasks"
                )
            }

            maximum = self.checkpoint.connection.execute(
                "SELECT MAX(position) FROM research_tasks"
            ).fetchone()[0]

            next_position = 0 if maximum is None else maximum + 1

            new_people = [fid for fid in identifiers if fid not in existing]

            self.checkpoint.connection.executemany(
                """
                INSERT INTO research_tasks (fid, position)
                VALUES (?, ?)
                """,
                ((fid, next_position + index) for index, fid in enumerate(new_people)),
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
        """Use 0 for high, 1 for normal, and 2 for low priority"""
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
                SELECT fid, status, priority, position
                FROM research_tasks
                """
            ).fetchall()

        recommendations = build_recommendations(
            self.checkpoint,
            [fid for fid, _, _, _ in rows],
        )

        status_order = {
            "in_progress": 0,
            "pending": 1,
            "done": 2,
            "postponed": 3,
        }

        result = []

        for fid, status, priority, position in rows:
            suggestion = recommendations[fid]

            result.append(
                {
                    "id": fid,
                    "name": suggestion["name"],
                    "status": status,
                    "priority": priority,
                    "category": suggestion["category"],
                    "generation": suggestion["generation"],
                    "branches": suggestion["branches"],
                    "_sort": (
                        status_order[status],
                        priority,
                        suggestion["recommendation_key"],
                        position,
                        fid,
                    ),
                }
            )

        result.sort(key=lambda item: item["_sort"])

        for item in result:
            del item["_sort"]

        return result

def enumerate_ids(identifiers):
    for position, fid in enumerate(identifiers):
        yield fid, position
