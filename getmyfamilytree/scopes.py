"""Relationship-aware traversal for ancestors, descendants and extended family.

The SQLite cache is shared between scopes. Each traversal recomputes membership
from the starting person, reuses cached responses and only downloads missing
records. Limiting a scope does not silently change the underlying genealogy.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import re
import sys

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


def _id(relationship, field):
    member = relationship.get(field)
    value = member.get("resourceId") if isinstance(member, dict) else None
    return value if isinstance(value, str) and PERSON_ID.fullmatch(value) else None


def _edges(document):
    """Return directed parent-child edges and undirected couple edges."""
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
    neighbours = set()
    for kind, first, second in edges:
        if kind == "couple":
            if scope in ("family", "full"):
                if identifier == first:
                    neighbours.add(second)
                elif identifier == second:
                    neighbours.add(first)
        elif scope == "ancestors":
            if identifier == second:
                neighbours.add(first)
        elif scope == "descendants":
            if identifier == first:
                neighbours.add(second)
        else:
            if identifier == first:
                neighbours.add(second)
            elif identifier == second:
                neighbours.add(first)
    return neighbours


def _person_name(document, identifier):
    for item in document.get("persons") or []:
        if item.get("id") != identifier:
            continue
        name = (item.get("display") or {}).get("name")
        if not name:
            name = next((
                form.get("fullText")
                for entry in item.get("names") or []
                for form in entry.get("nameForms") or []
                if form.get("fullText")
            ), None)
        return " ".join(str(name).split()) if name else identifier
    return identifier


def crawl_scope(
    checkpoint, session, origin, *, scope="full", generations=None,
    distance=None, max_people=1000, progress=1, language="en",
):
    """Traverse one scope, returning exactly the downloaded members of it.

    session=None performs an offline selection from cached records only. The
    original queue/status table remains compatible with previous downloads.
    The person limit applies to this scope, not other cached modes.
    """
    if scope not in ("full", "ancestors", "descendants", "family"):
        raise ValueError(f"Unsupported scope: {scope}")
    max_depth = (
        generations if scope in ("ancestors", "descendants")
        else distance if scope == "family" else None
    )
    edges = set()
    graph = defaultdict(set)
    depths = {origin: 0}
    waiting = deque([(origin, 0)])
    expanded = {}
    selected = {}
    attempts_failed = set()
    reused = 0
    interrupted = False

    def offer(identifier, depth):
        if not identifier or not PERSON_ID.fullmatch(identifier):
            return
        if max_depth is not None and depth > max_depth:
            return
        if depth < depths.get(identifier, float("inf")):
            depths[identifier] = depth
            waiting.append((identifier, depth))

    def propagate():
        # Newly encountered relationships can refer to people whose own
        # records were examined earlier. Relax distances until stable.
        items = list(depths.items())
        for identifier, depth in items:
            if max_depth is not None and depth >= max_depth:
                continue
            for neighbour in _neighbours(identifier, graph[identifier], scope):
                offer(neighbour, depth + 1)

    if progress:
        print(
            translate(language, "scope_start", scope=translate(
                language, "scope_" + scope
            ), limit=str(max_people) if max_people else translate(
                language, "unlimited"
            )),
            file=sys.stderr, flush=True,
        )

    try:
        while waiting and (max_people == 0 or len(selected) < max_people):
            fid, depth = waiting.popleft()
            if depths.get(fid) != depth:
                continue
            if expanded.get(fid, float("inf")) <= depth:
                continue
            expanded[fid] = depth

            if fid in selected:
                if max_depth is None or depth < max_depth:
                    for other in _neighbours(fid, graph[fid], scope):
                        offer(other, depth + 1)
                continue

            key = PERSON_URL + fid
            document = checkpoint.get_response(key)
            cached = document is not None
            if document is None and session is not None:
                document = session.get_url(key)
                if document is not None:
                    checkpoint.save_response(key, document)
            if not document or not any(
                p.get("id") == fid for p in document.get("persons") or []
            ):
                attempts_failed.add(fid)
                if session is not None:
                    checkpoint.fail(fid)
                    print(
                        translate(language, "retrieval_failed", fid=fid),
                        file=sys.stderr, flush=True,
                    )
                continue

            if cached:
                reused += 1

            families_key = FAMILIES_URL.format(fid)
            families = checkpoint.get_response(families_key)
            if families is None and session is not None:
                families = session.get_url(families_key)
                if families is not None:
                    checkpoint.save_response(families_key, families)

            docs = [document]
            if families:
                docs.append(families)
            discovered = set()
            for doc in docs:
                discovered.update(_all_referenced_ids(doc))
                for edge in _edges(doc):
                    if edge in edges:
                        continue
                    edges.add(edge)
                    _, first, second = edge
                    graph[first].add(edge)
                    graph[second].add(edge)

            if scope == "full":
                # Retain the original tool's wider discovery from API person
                # lists while all bounded modes use actual relationship links.
                for other in discovered:
                    if other != fid:
                        graph[fid].add(("couple", *sorted((fid, other))))

            if session is not None:
                new_relatives = checkpoint.finish(fid, discovered)
            else:
                new_relatives = 0

            selected[fid] = depth
            if max_depth is None or depth < max_depth:
                for other in _neighbours(fid, graph[fid], scope):
                    offer(other, depth + 1)
                propagate()

            if progress and len(selected) % progress == 0:
                print(
                    translate(
                        language, "scope_person",
                        index=len(selected),
                        limit=f"/{max_people}" if max_people else "",
                        name=_person_name(document, fid),
                        fid=fid, depth=depth, added=new_relatives,
                        queued=len(waiting),
                    ),
                    file=sys.stderr, flush=True,
                )
    except KeyboardInterrupt:
        interrupted = True

    pending_ids = {
        fid for fid, depth in waiting
        if depths.get(fid) == depth and fid not in selected
        and fid not in attempts_failed
    }
    finished = not interrupted and not pending_ids and not attempts_failed
    result = ScopeResult(
        ids=list(selected), pending=len(pending_ids),
        failed=len(attempts_failed), exhausted=finished,
        reused=reused, interrupted=interrupted,
    )
    if progress:
        print(
            translate(
                language, "scope_finish", selected=len(selected),
                pending=result.pending, failed=result.failed,
                reused=reused,
            ),
            file=sys.stderr, flush=True,
        )
    return result
