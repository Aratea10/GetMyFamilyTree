"""Persistent genealogy research planning and manual organization"""

import json

from getmyfamilytree.research_recommendations import build_recommendations

VALID_STATUSES = {"pending", "in_progress", "done", "postponed"}

VALID_CATEGORIES = {
    "origin",
    "ancestor",
    "descendant",
    "collateral",
    "in_law",
    "unclassified",
}

CATEGORY_ORDER = {
    "origin": 0,
    "ancestor": 1,
    "descendant": 2,
    "collateral": 3,
    "in_law": 4,
    "unclassified": 5,
}


class ResearchPlan:
    """Keep research decisions separate from downloaded genealogy"""

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

            # Separate table: existing databases need no destructive
            # schema migration, and original tasks remain untouched.
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_overrides (
                    fid TEXT PRIMARY KEY,
                    category TEXT,
                    branches TEXT
                )
                """
            )

    def sync(self):
        """Append new completed people without resetting existing work"""
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

    def _require_task(self, fid):
        """Check that a person already belongs to the research plan"""
        row = self.checkpoint.connection.execute(
            "SELECT 1 FROM research_tasks WHERE fid = ?",
            (fid,),
        ).fetchone()

        if row is None:
            raise KeyError(fid)

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
        """Priority: 0 high, 1 normal, 2 low."""
        if priority not in (0, 1, 2):
            raise ValueError(f"Unsupported priority: {priority}")

        with self.checkpoint.lock, self.checkpoint.connection:
            cursor = self.checkpoint.connection.execute(
                "UPDATE research_tasks SET priority = ? WHERE fid = ?",
                (priority, fid),
            )
            if cursor.rowcount != 1:
                raise KeyError(fid)

    def set_category(self, fid, category):
        """Override a category, or pass None to restore automatic"""
        if category is not None and category not in VALID_CATEGORIES:
            raise ValueError(f"Unsupported category: {category}")

        origin = self.checkpoint.get_origin()

        if category == "origin" and fid != origin:
            raise ValueError("Only the starting person can be origin")

        if fid == origin and category not in (None, "origin"):
            raise ValueError("Cannot reclassify the starting person")

        with self.checkpoint.lock, self.checkpoint.connection:
            self._require_task(fid)

            self.checkpoint.connection.execute(
                """
                INSERT INTO research_overrides (fid, category)
                VALUES (?, ?)
                ON CONFLICT(fid)
                DO UPDATE SET category = excluded.category
                """,
                (fid, category),
            )

    def set_branches(self, fid, branch_ids):
        """Override branches using starting-person parent IDs

        None restores automatic branches.
        An empty list explicitly leaves the person unassigned
        """
        encoded = None

        if branch_ids is not None:
            if not isinstance(branch_ids, (list, tuple)):
                raise ValueError("Branches must be a list or None")

            if not all(isinstance(value, str) for value in branch_ids):
                raise ValueError("Branch identifiers must be strings")

            branch_ids = list(dict.fromkeys(branch_ids))

            origin = self.checkpoint.get_origin()
            recommendations = build_recommendations(
                self.checkpoint,
                self.checkpoint.completed_ids(),
            )

            if origin not in recommendations:
                raise ValueError("Starting person is not available")

            valid_roots = {
                branch["id"] for branch in recommendations[origin]["branches"]
            }

            if any(root not in valid_roots for root in branch_ids):
                raise ValueError("Unknown research branch")

            encoded = json.dumps(branch_ids)

        with self.checkpoint.lock, self.checkpoint.connection:
            self._require_task(fid)

            self.checkpoint.connection.execute(
                """
                INSERT INTO research_overrides (fid, branches)
                VALUES (?, ?)
                ON CONFLICT(fid)
                DO UPDATE SET branches = excluded.branches
                """,
                (fid, encoded),
            )

    def move(self, fid, before_id=None):
        """Move a person before another, or to the end if omitted"""
        with self.checkpoint.lock, self.checkpoint.connection:
            identifiers = [
                row[0]
                for row in self.checkpoint.connection.execute(
                    """
                    SELECT fid FROM research_tasks
                    ORDER BY position, fid
                    """
                )
            ]

            if fid not in identifiers:
                raise KeyError(fid)

            if before_id is not None and before_id not in identifiers:
                raise KeyError(before_id)

            if fid == before_id:
                return

            identifiers.remove(fid)

            if before_id is None:
                identifiers.append(fid)
            else:
                identifiers.insert(identifiers.index(before_id), fid)

            self.checkpoint.connection.executemany(
                """
                UPDATE research_tasks
                SET position = ?
                WHERE fid = ?
                """,
                (
                    (position, identifier)
                    for position, identifier in enumerate(identifiers)
                ),
            )

    def items(self, order="recommended"):
        """Return research tasks in automatic or custom order"""
        if order not in ("recommended", "manual"):
            raise ValueError(f"Unsupported research order: {order}")

        with self.checkpoint.lock:
            rows = self.checkpoint.connection.execute(
                """
                SELECT
                    t.fid,
                    t.status,
                    t.priority,
                    t.position,
                    o.category,
                    o.branches
                FROM research_tasks AS t
                LEFT JOIN research_overrides AS o
                    ON o.fid = t.fid
                """
            ).fetchall()

        recommendations = build_recommendations(
            self.checkpoint,
            [row[0] for row in rows],
        )

        origin = self.checkpoint.get_origin()

        root_branches = recommendations.get(origin, {}).get("branches", [])
        roots_by_id = {branch["id"]: branch for branch in root_branches}
        root_index = {branch["id"]: index for index, branch in enumerate(root_branches)}

        status_order = {
            "in_progress": 0,
            "pending": 1,
            "done": 2,
            "postponed": 3,
        }

        result = []

        for (
            fid,
            status,
            priority,
            position,
            manual_category,
            manual_branches,
        ) in rows:
            suggestion = recommendations[fid]

            category = (
                manual_category
                if manual_category is not None
                else suggestion["category"]
            )

            branches = suggestion["branches"]

            if manual_branches is not None:
                saved_ids = json.loads(manual_branches)
                branches = [
                    roots_by_id[root] for root in saved_ids if root in roots_by_id
                ]

            branch_rank = min(
                (
                    root_index[branch["id"]]
                    for branch in branches
                    if branch["id"] in root_index
                ),
                default=len(root_index),
            )

            recommendation_sort = (
                status_order[status],
                priority,
                CATEGORY_ORDER[category],
                branch_rank,
                suggestion["recommendation_key"][2],
                position,
                fid,
            )

            manual_sort = (position, fid)

            result.append(
                {
                    "id": fid,
                    "name": suggestion["name"],
                    "status": status,
                    "priority": priority,
                    "position": position,
                    "category": category,
                    "recommended_category": suggestion["category"],
                    "category_is_manual": manual_category is not None,
                    "generation": suggestion["generation"],
                    "branches": branches,
                    "recommended_branches": suggestion["branches"],
                    "branches_are_manual": manual_branches is not None,
                    "affinity_links": suggestion["affinity_links"],
                    "_sort": (
                        manual_sort if order == "manual" else recommendation_sort
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
