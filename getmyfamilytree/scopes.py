"""Relationship-aware traversal for ancestors, descendants and extended family.

The SQLite cache is shared between scopes. Each traversal recomputes membership
from the starting person, reuses cached responses and only downloads missing
records. Limiting a scope does not silently change the underlying genealogy.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass

from getmyfamilytree.i18n import translate

PERSON_URL = "/platform/tree/persons?pids="
FAMILIES_URL = "/platform/tree/persons/{}/families"
PERSON_ID = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{3}\Z")


@dataclass
class ScopeResult:
    ids: list[str]
    pending: int
    failed: int
    exhausted: bool
    reused: int
    interrupted: bool = False


@dataclass(frozen=True)
class _TraversalOptions:
    """Configuration shared by the internal traversal operations"""

    scope: str
    max_depth: int | None
    max_people: int
    progress: int
    language: str


def _id(relationship, field):
    member = relationship.get(field)
    value = member.get("resourceId") if isinstance(member, dict) else None
    return value if isinstance(value, str) and PERSON_ID.fullmatch(value) else None


def _edges(document):
    """Return directed parent-child edges and undirected couple edges"""
    edges = set()

    for item in document.get("childAndParentsRelationships") or []:
        child = _id(item, "child")

        if child:
            for role in ("parent1", "parent2"):
                parent = _id(item, role)

                if parent and parent != child:
                    edges.add(("parent_child", parent, child))

    for item in document.get("relationships") or []:
        kind = item.get("type", "").rsplit("/", 1)[-1]
        first = _id(item, "person1")
        second = _id(item, "person2")

        if not first or not second or first == second:
            continue

        if kind == "ParentChild":
            edges.add(("parent_child", first, second))
        elif kind == "Couple":
            edges.add(("couple", *sorted((first, second))))

    return edges


def _all_referenced_ids(document):
    """Collect valid person identifiers from a FamilySearch response"""
    found = set()

    for item in document.get("persons") or []:
        identifier = item.get("id")

        if isinstance(identifier, str) and PERSON_ID.fullmatch(identifier):
            found.add(identifier)

    for kind in ("relationships", "childAndParentsRelationships"):
        for item in document.get(kind) or []:
            for role in ("person1", "person2", "parent1", "parent2", "child"):
                identifier = _id(item, role)

                if identifier:
                    found.add(identifier)

    return found


def _neighbours(identifier, edges, scope):
    """Return the reachable people according to the selected scope"""
    neighbours = set()

    for kind, first, second in edges:
        if kind == "couple" and scope not in ("family", "full"):
            continue

        allow_first = kind == "couple" or scope != "ancestors"
        allow_second = kind == "couple" or scope != "descendants"

        if identifier == first and allow_first:
            neighbours.add(second)
        elif identifier == second and allow_second:
            neighbours.add(first)

    return neighbours


def _person_name(document, identifier):
    """Find a readable person name without changing genealogical data"""
    for item in document.get("persons") or []:
        if item.get("id") != identifier:
            continue

        name = (item.get("display") or {}).get("name")

        if not name:
            name = next(
                (
                    form.get("fullText")
                    for entry in item.get("names") or []
                    for form in entry.get("nameForms") or []
                    if form.get("fullText")
                ),
                None,
            )

        return " ".join(str(name).split()) if name else identifier

    return identifier


class _ScopeTraversal:
    """Manage one relationship-aware traversal and its temporary state"""

    def __init__(self, checkpoint, session, origin, options):
        self.checkpoint = checkpoint
        self.session = session
        self.options = options

        self.edges = set()
        self.graph = defaultdict(set)
        self.depths = {origin: 0}
        self.waiting = deque([(origin, 0)])
        self.expanded = {}
        self.selected = {}
        self.attempts_failed = set()

        self.reused = 0
        self.interrupted = False

    def _offer(self, identifier, depth):
        """Queue a person when a shorter valid path is discovered"""
        if not identifier or not PERSON_ID.fullmatch(identifier):
            return

        if self.options.max_depth is not None and depth > self.options.max_depth:
            return

        if depth < self.depths.get(identifier, float("inf")):
            self.depths[identifier] = depth
            self.waiting.append((identifier, depth))

    def _propagate(self):
        """Propagate improved distances through already discovered links"""
        # Newly encountered relationships can refer to people whose own
        # records were examined earlier. Relax distances until stable.
        items = list(self.depths.items())

        for identifier, depth in items:
            if self.options.max_depth is not None and depth >= self.options.max_depth:
                continue

            neighbours = _neighbours(
                identifier,
                self.graph[identifier],
                self.options.scope,
            )

            for neighbour in sorted(neighbours):
                self._offer(neighbour, depth + 1)

    def _queue_neighbours(self, identifier, depth, propagate=False):
        """Schedule reachable relatives within the selected depth limit"""
        if self.options.max_depth is not None and depth >= self.options.max_depth:
            return

        neighbours = _neighbours(
            identifier,
            self.graph[identifier],
            self.options.scope,
        )

        for neighbour in sorted(neighbours):
            self._offer(neighbour, depth + 1)

        if propagate:
            self._propagate()

    def _load_response(self, key):
        """Read a cached response or download it when online"""
        document = self.checkpoint.get_response(key)
        cached = document is not None

        if document is None and self.session is not None:
            document = self.session.get_url(key)

            if document is not None:
                self.checkpoint.save_response(key, document)

        return document, cached

    def _load_person(self, identifier):
        """Load and validate a requested person's record"""
        document, cached = self._load_response(PERSON_URL + identifier)

        valid = document and any(
            person.get("id") == identifier for person in document.get("persons") or []
        )

        if not valid:
            self.attempts_failed.add(identifier)

            if self.session is not None:
                self.checkpoint.fail(identifier)
                print(
                    translate(
                        self.options.language,
                        "retrieval_failed",
                        fid=identifier,
                    ),
                    file=sys.stderr,
                    flush=True,
                )

            return None

        if cached:
            self.reused += 1

        return document

    def _collect_document(self, document, discovered):
        """Merge people and relationships into the traversal graph"""
        discovered.update(_all_referenced_ids(document))

        for edge in _edges(document):
            if edge in self.edges:
                continue

            self.edges.add(edge)

            _, first, second = edge
            self.graph[first].add(edge)
            self.graph[second].add(edge)

    def _discover(self, identifier, document):
        """Collect available relatives from person and family responses"""
        families, _ = self._load_response(FAMILIES_URL.format(identifier))

        documents = [document]

        if families:
            documents.append(families)

        discovered = set()

        for item in documents:
            self._collect_document(item, discovered)

        if self.options.scope == "full":
            # Preserve broad discovery from FamilySearch person lists.
            # Bounded modes use actual relationship links instead.
            for other in discovered:
                if other != identifier:
                    self.graph[identifier].add(("couple", *sorted((identifier, other))))

        return discovered

    def _report_start(self):
        """Show the initial scope and person limit."""
        options = self.options

        limit = (
            str(options.max_people)
            if options.max_people
            else translate(options.language, "unlimited")
        )

        print(
            translate(
                options.language,
                "scope_start",
                scope=translate(options.language, "scope_" + options.scope),
                limit=limit,
            ),
            file=sys.stderr,
            flush=True,
        )

    def _report_person(self, identifier, depth, document, added):
        """Show progress after successfully selecting a person"""
        options = self.options

        if not options.progress:
            return

        if len(self.selected) % options.progress != 0:
            return

        print(
            translate(
                options.language,
                "scope_person",
                index=len(self.selected),
                limit=f"/{options.max_people}" if options.max_people else "",
                name=_person_name(document, identifier),
                fid=identifier,
                depth=depth,
                added=added,
                queued=len(self.waiting),
            ),
            file=sys.stderr,
            flush=True,
        )

    def _visit(self, identifier, depth):
        """Process one queued person without expanding stale entries"""
        if self.depths.get(identifier) != depth:
            return

        if self.expanded.get(identifier, float("inf")) <= depth:
            return

        self.expanded[identifier] = depth

        if identifier in self.selected:
            self._queue_neighbours(identifier, depth)
            return

        document = self._load_person(identifier)

        if document is None:
            return

        discovered = self._discover(identifier, document)

        if self.session is not None:
            added = self.checkpoint.finish(identifier, discovered)
        else:
            added = 0

        self.selected[identifier] = depth
        self._queue_neighbours(identifier, depth, propagate=True)
        self._report_person(identifier, depth, document, added)

    def _result(self):
        """Build the final result without changing the persistent cache"""
        pending_ids = {
            identifier
            for identifier, depth in self.waiting
            if self.depths.get(identifier) == depth
            and identifier not in self.selected
            and identifier not in self.attempts_failed
        }

        finished = not self.interrupted and not pending_ids and not self.attempts_failed

        result = ScopeResult(
            ids=list(self.selected),
            pending=len(pending_ids),
            failed=len(self.attempts_failed),
            exhausted=finished,
            reused=self.reused,
            interrupted=self.interrupted,
        )

        if self.options.progress:
            print(
                translate(
                    self.options.language,
                    "scope_finish",
                    selected=len(self.selected),
                    pending=result.pending,
                    failed=result.failed,
                    reused=self.reused,
                ),
                file=sys.stderr,
                flush=True,
            )

        return result

    def run(self):
        """Explore the requested scope and return its selected people"""
        if self.options.progress:
            self._report_start()

        try:
            while self.waiting and (
                self.options.max_people == 0
                or len(self.selected) < self.options.max_people
            ):
                identifier, depth = self.waiting.popleft()
                self._visit(identifier, depth)

        except KeyboardInterrupt:
            self.interrupted = True

        return self._result()


# Existing callers rely on these public scope options.
def crawl_scope(
    checkpoint,
    session,
    origin,
    *,
    scope="full",
    generations=None,
    distance=None,
    max_people=1000,
    progress=1,
    language="en",
):
    """Traverse one scope, returning exactly the downloaded members of it.

    session=None performs an offline selection from cached records only. The
    original queue/status table remains compatible with previous downloads.
    The person limit applies to this scope, not other cached modes.
    """
    if scope not in ("full", "ancestors", "descendants", "family"):
        raise ValueError(f"Unsupported scope: {scope}")

    max_depth = (
        generations
        if scope in ("ancestors", "descendants")
        else distance
        if scope == "family"
        else None
    )

    options = _TraversalOptions(
        scope=scope,
        max_depth=max_depth,
        max_people=max_people,
        progress=progress,
        language=language,
    )

    traversal = _ScopeTraversal(
        checkpoint,
        session,
        origin,
        options,
    )

    return traversal.run()
