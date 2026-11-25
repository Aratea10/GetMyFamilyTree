"""Local research workspace for person sheets and a persistent evidence board.

This module never contacts FamilySearch. Research decisions and board positions
are kept separately from the downloaded GEDCOM and FamilySearch response cache.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL, collect_reports
from getmyfamilytree.research_assets import ASSET_ID, ResearchAssets
from getmyfamilytree.research_plan import ResearchPlan, VALID_CATEGORIES, VALID_STATUSES
from getmyfamilytree.scopes import _edges, _person_name

CARD_KINDS = frozenset({"document", "photo", "person", "note"})
CARD_FIELDS = frozenset(
    {"kind", "title", "description", "url", "asset_id", "x", "y", "width", "rotation", "z"}
)
CARD_DEFAULTS = {
    "kind": "note",
    "title": "",
    "description": "",
    "url": "",
    "asset_id": None,
    "x": 0.0,
    "y": 0.0,
    "width": 280.0,
    "rotation": 0.0,
    "z": 0,
}


@dataclass(frozen=True)
class TaskQuery:
    """Search and pagination options for the master research list."""

    search: str = ""
    status: str | None = None
    category: str | None = None
    branch: str | None = None
    order: str = "recommended"
    limit: int = 50
    offset: int = 0


def _updated_at():
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value, field, maximum=10000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} must be text of at most {maximum} characters")
    return value


def _validate_card(data):
    """Return normalized card data without accepting unknown fields."""
    if not isinstance(data, dict):
        raise ValueError("Card data must be an object")
    unknown = set(data) - CARD_FIELDS
    if unknown:
        raise ValueError(f"Unsupported card fields: {', '.join(sorted(unknown))}")

    result = {**CARD_DEFAULTS, **data}
    if result["kind"] not in CARD_KINDS:
        raise ValueError("Unsupported board card type")
    result["title"] = _validate_text(result["title"], "title", 300)
    if not result["title"].strip():
        raise ValueError("Card title cannot be empty")
    result["description"] = _validate_text(result["description"], "description")
    result["url"] = _validate_text(result["url"], "url", 2048)
    if result["url"]:
        url = urlsplit(result["url"])
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("Card URLs must use http or https")
    if result["asset_id"] is not None:
        if not isinstance(result["asset_id"], str):
            raise ValueError("Invalid card attachment")
        if not ASSET_ID.fullmatch(result["asset_id"]):
            raise ValueError("Invalid card attachment")
        if result["url"]:
            raise ValueError("A card cannot use both a URL and a local attachment")

    for field in ("x", "y", "width", "rotation"):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a finite number")
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        result[field] = float(value)

    if result["width"] <= 0 or result["width"] > 5000:
        raise ValueError("Card width must be between 0 and 5000")
    if isinstance(result["z"], bool) or not isinstance(result["z"], int):
        raise ValueError("Card z-order must be an integer")
    return result


class ResearchWorkspace:
    """Expose research tasks, person details and board cards to a future UI."""

    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.plan = ResearchPlan(checkpoint)
        self.plan.sync()

        with checkpoint.lock, checkpoint.connection:
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_person_notes (
                    fid TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_cards (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    asset_id TEXT,
                    x REAL NOT NULL DEFAULT 0,
                    y REAL NOT NULL DEFAULT 0,
                    width REAL NOT NULL DEFAULT 280,
                    rotation REAL NOT NULL DEFAULT 0,
                    z INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                column[1] for column in checkpoint.connection.execute(
                    "PRAGMA table_info(workspace_cards)"
                )
            }
            if "asset_id" not in columns:
                checkpoint.connection.execute(
                    "ALTER TABLE workspace_cards ADD COLUMN asset_id TEXT"
                )
            checkpoint.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_card_people (
                    card_id TEXT NOT NULL,
                    fid TEXT NOT NULL,
                    PRIMARY KEY (card_id, fid)
                )
                """
            )
            checkpoint.connection.execute(
                "CREATE INDEX IF NOT EXISTS workspace_card_people_fid "
                "ON workspace_card_people(fid)"
            )
        self.assets = ResearchAssets(checkpoint)

    def list_tasks(self, query=None):
        """Return a filtered, paginated list without modifying manual order."""
        query = TaskQuery() if query is None else query
        if not isinstance(query, TaskQuery):
            raise ValueError("query must be a TaskQuery")
        if query.order not in ("recommended", "manual"):
            raise ValueError("Unsupported research order")
        if query.status is not None and query.status not in VALID_STATUSES:
            raise ValueError("Unsupported research status")
        if query.category is not None and query.category not in VALID_CATEGORIES:
            raise ValueError("Unsupported research category")
        if query.limit < 0 or query.offset < 0:
            raise ValueError("Limit and offset cannot be negative")
        search = _validate_text(query.search, "search", 300).casefold().strip()
        tasks = self.plan.items(order=query.order)

        if search:
            tasks = [
                task for task in tasks
                if search in task["name"].casefold()
                or search in task["id"].casefold()
            ]
        if query.status is not None:
            tasks = [task for task in tasks if task["status"] == query.status]
        if query.category is not None:
            tasks = [
                task for task in tasks if task["category"] == query.category
            ]
        if query.branch is not None:
            tasks = [
                task for task in tasks
                if any(branch["id"] == query.branch for branch in task["branches"])
            ]

        total = len(tasks)
        page = tasks[query.offset:] if query.limit == 0 else tasks[
            query.offset:query.offset + query.limit
        ]
        return {
            "total": total,
            "offset": query.offset,
            "count": len(page),
            "items": page,
        }

    def summary(self):
        """Return counters for the dashboard and research progress."""
        tasks = self.plan.items()
        statuses = Counter(task["status"] for task in tasks)
        categories = Counter(task["category"] for task in tasks)
        with self.checkpoint.lock:
            cards = self.checkpoint.connection.execute(
                "SELECT COUNT(*) FROM workspace_cards"
            ).fetchone()[0]
        return {
            "people": len(tasks),
            "statuses": {
                status: statuses[status] for status in sorted(VALID_STATUSES)
            },
            "categories": {
                category: categories[category] for category in sorted(VALID_CATEGORIES)
            },
            "cards": cards,
        }

    def _require_person(self, fid):
        """Ensure that UI modifications refer to a downloaded person."""
        with self.checkpoint.lock:
            exists = self.checkpoint.connection.execute(
                "SELECT 1 FROM research_tasks WHERE fid = ?", (fid,)
            ).fetchone()
        if exists is None:
            raise KeyError(fid)

    def get_person_note(self, fid):
        self._require_person(fid)
        with self.checkpoint.lock:
            row = self.checkpoint.connection.execute(
                "SELECT content FROM workspace_person_notes WHERE fid = ?", (fid,)
            ).fetchone()
        return row[0] if row else ""

    def set_person_note(self, fid, content):
        """Save a private research note without overwriting Markdown reviews."""
        self._require_person(fid)
        if content is not None:
            _validate_text(content, "content", 100000)
        with self.checkpoint.lock, self.checkpoint.connection:
            if content is None:
                self.checkpoint.connection.execute(
                    "DELETE FROM workspace_person_notes WHERE fid = ?", (fid,)
                )
            else:
                self.checkpoint.connection.execute(
                    """
                    INSERT INTO workspace_person_notes (fid, content, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(fid) DO UPDATE SET
                        content = excluded.content,
                        updated_at = excluded.updated_at
                    """,
                    (fid, content, _updated_at()),
                )

    def _person_relationships(self, fid):
        """Collect direct links even when the other person is outside a scope."""
        cached = self.checkpoint.get_responses(
            [PERSON_URL + fid, FAMILIES_URL.format(fid)]
        )
        edges = set()
        for key in (PERSON_URL + fid, FAMILIES_URL.format(fid)):
            edges.update(_edges(cached.get(key) or {}))

        related = set()
        relationships = []
        for kind, first, second in sorted(edges):
            if fid not in (first, second):
                continue
            other = second if fid == first else first
            related.add(other)
            relationships.append(
                {
                    "type": kind,
                    "first_id": first,
                    "second_id": second,
                    "relative_id": other,
                }
            )

        people = self.checkpoint.get_responses(
            [PERSON_URL + identifier for identifier in sorted(related)]
        )
        for relationship in relationships:
            other = relationship["relative_id"]
            relationship["relative_name"] = _person_name(
                people.get(PERSON_URL + other) or {}, other
            )
        return relationships

    def person_detail(self, fid):
        """Build one UI-ready person sheet using only the downloaded cache."""
        self._require_person(fid)
        task = next(item for item in self.plan.items() if item["id"] == fid)
        data = collect_reports(self.checkpoint, selected_ids=[fid])
        return {
            "task": task,
            "person": data["people"][0] if data["people"] else None,
            "events": data["events"],
            "relationships": self._person_relationships(fid),
            "sources": data["sources"],
            "notes": data["notes"],
            "memories": data["memories"],
            "review": self.checkpoint.load_review_notes().get(fid, ""),
            "research_note": self.get_person_note(fid),
            "cards": [
                card for card in self.list_cards() if fid in card["people"]
            ],
        }

    def _validate_people(self, identifiers):
        """Deduplicate person links and reject unknown downloaded people."""
        if not isinstance(identifiers, (list, tuple)):
            raise ValueError("Card people must be a list")
        if not all(isinstance(fid, str) for fid in identifiers):
            raise ValueError("Person identifiers must be strings")
        people = list(dict.fromkeys(identifiers))
        for fid in people:
            self._require_person(fid)
        return people

    def _replace_card_people(self, card_id, people):
        self.checkpoint.connection.execute(
            "DELETE FROM workspace_card_people WHERE card_id = ?", (card_id,)
        )
        self.checkpoint.connection.executemany(
            "INSERT INTO workspace_card_people (card_id, fid) VALUES (?, ?)",
            ((card_id, fid) for fid in people),
        )

    def list_cards(self):
        """List positioned board cards and their linked people."""
        with self.checkpoint.lock:
            rows = self.checkpoint.connection.execute(
                """
                SELECT id, kind, title, description, url, asset_id, x, y,
                       width, rotation, z, created_at, updated_at
                FROM workspace_cards
                ORDER BY z, created_at, id
                """
            ).fetchall()
            links = self.checkpoint.connection.execute(
                """
                SELECT card_id, fid FROM workspace_card_people
                ORDER BY card_id, fid
                """
            ).fetchall()
        by_card = defaultdict(list)
        for card_id, fid in links:
            by_card[card_id].append(fid)

        columns = (
            "id", "kind", "title", "description", "url", "asset_id",
            "x", "y", "width", "rotation", "z", "created_at", "updated_at",
        )
        return [
            {**dict(zip(columns, row)), "people": by_card[row[0]]}
            for row in rows
        ]

    def get_card(self, card_id):
        """Return a specific board card or raise KeyError."""
        for card in self.list_cards():
            if card["id"] == card_id:
                return card
        raise KeyError(card_id)

    def create_card(self, data, people=None):
        """Persist a new board card, with optional person links."""
        data = _validate_card(data)
        if data["asset_id"] is not None:
            self.assets.get(data["asset_id"])
        people = self._validate_people([] if people is None else people)
        card_id = uuid4().hex
        timestamp = _updated_at()
        with self.checkpoint.lock, self.checkpoint.connection:
            self.checkpoint.connection.execute(
                """
                INSERT INTO workspace_cards (
                    id, kind, title, description, url, asset_id, x, y, width,
                    rotation, z, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id, data["kind"], data["title"], data["description"],
                    data["url"], data["asset_id"], data["x"], data["y"],
                    data["width"], data["rotation"], data["z"],
                    timestamp, timestamp,
                ),
            )
            self._replace_card_people(card_id, people)
        return self.get_card(card_id)

    def update_card(self, card_id, changes, people=None):
        """Change a card without losing unmodified fields or linked people."""
        existing = self.get_card(card_id)
        if not isinstance(changes, dict):
            raise ValueError("Card changes must be an object")
        data = _validate_card({
            field: existing[field] for field in CARD_FIELDS
        } | changes)
        if data["asset_id"] is not None:
            self.assets.get(data["asset_id"])
        linked_people = (
            existing["people"] if people is None else self._validate_people(people)
        )
        with self.checkpoint.lock, self.checkpoint.connection:
            self.checkpoint.connection.execute(
                """
                UPDATE workspace_cards SET kind=?, title=?, description=?,
                    url=?, asset_id=?, x=?, y=?, width=?, rotation=?, z=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    data["kind"], data["title"], data["description"], data["url"],
                    data["asset_id"], data["x"], data["y"], data["width"],
                    data["rotation"], data["z"], _updated_at(), card_id,
                ),
            )
            self._replace_card_people(card_id, linked_people)
        return self.get_card(card_id)

    def delete_card(self, card_id):
        """Remove one card and its links without deleting genealogical data."""
        with self.checkpoint.lock, self.checkpoint.connection:
            cursor = self.checkpoint.connection.execute(
                "DELETE FROM workspace_cards WHERE id = ?", (card_id,)
            )
            if not cursor.rowcount:
                raise KeyError(card_id)
            self.checkpoint.connection.execute(
                "DELETE FROM workspace_card_people WHERE card_id = ?", (card_id,)
            )
