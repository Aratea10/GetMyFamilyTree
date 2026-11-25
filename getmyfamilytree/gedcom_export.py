"""GEDCOM export helpers for cached FamilySearch data"""

from __future__ import annotations

import json

from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL

RELATIONSHIP_FIELDS = (
    "childAndParentsRelationships",
    "relationships",
)


def _member_id(relationship, name):
    """Return the person ID referenced by a relationship member"""
    member = relationship.get(name)
    return member.get("resourceId") if isinstance(member, dict) else None


def _combined_documents(documents):
    """Recreate the batch response expected by Tree.add_indis"""
    combined = {
        key: []
        for key in (
            "persons",
            *RELATIONSHIP_FIELDS,
            "places",
        )
    }
    seen = {key: set() for key in combined}

    for document in documents:
        for key in combined:
            for item in document.get(key, []):
                marker = item.get("id") or json.dumps(item, sort_keys=True)

                if marker not in seen[key]:
                    seen[key].add(marker)
                    combined[key].append(item)

    return combined


class ReplaySession:
    """Expose cached person responses to the GEDCOM exporter"""

    def __init__(self, session, checkpoint):
        self.session = session
        self.checkpoint = checkpoint

    def __getattr__(self, name):
        return getattr(self.session, name)

    def get_url(self, url, headers=None, no_api=False):
        if url.startswith(PERSON_URL):
            ids = url[len(PERSON_URL) :].split(",")
            documents = []

            for fid in ids:
                document = self.checkpoint.get_response(PERSON_URL + fid)

                if document is None:
                    raise RuntimeError(f"Missing cached person {fid}")

                documents.append(document)

            combined = _combined_documents(documents)

            # Do not export extra people returned by FamilySearch
            # when the user selected a bounded family scope.
            chosen = set(ids)
            combined["persons"] = [
                person for person in combined["persons"] if person.get("id") in chosen
            ]

            return combined

        accept = headers.get("Accept", "") if headers else ""
        key = f"{int(no_api)}:{accept}:{url}"
        document = self.checkpoint.get_response(key)

        if document is None:
            document = self.session.get_url(url, headers, no_api)

            if document is not None:
                self.checkpoint.save_response(key, document)

        return document


def _collect_relationships(checkpoint, tree, ids):
    """Collect parentage and couple links from cached responses"""
    parent_links = set()
    parent_child_edges = set()
    couple_links = {}

    for fid in ids:
        documents = (
            checkpoint.get_response(PERSON_URL + fid),
            checkpoint.get_response(FAMILIES_URL.format(fid)),
        )

        for document in documents:
            if not document:
                continue

            for relationship in document.get("childAndParentsRelationships", []):
                child = _member_id(relationship, "child")
                father = _member_id(relationship, "parent1")
                mother = _member_id(relationship, "parent2")

                if child in tree.indi:
                    parent_links.add((father, mother, child))

            for relationship in document.get("relationships", []):
                kind = relationship.get("type")
                first = _member_id(relationship, "person1")
                second = _member_id(relationship, "person2")

                if kind == "http://gedcomx.org/ParentChild":
                    if first in tree.indi and second in tree.indi:
                        parent_child_edges.add((first, second))

                elif kind == "http://gedcomx.org/Couple" and (
                    first in tree.indi and second in tree.indi
                ):
                    couple_links.setdefault(
                        frozenset((first, second)),
                        (first, second, relationship.get("id")),
                    )

    return parent_links, parent_child_edges, couple_links


def _rebuild_parentage(tree, parent_links, parent_child_edges):
    """Restore parent-child relationships without guessing missing roles"""
    for father, mother, child in sorted(
        parent_links,
        key=lambda link: tuple(item or "" for item in link),
    ):
        father = father if father in tree.indi else None
        mother = mother if mother in tree.indi else None

        if father or mother:
            tree.add_trio(father, mother, child)

    # A ParentChild relationship may exist without a complete
    # ChildAndParents relationship.
    full_parent_edges = {
        (parent, child)
        for father, mother, child in parent_links
        for parent in (father, mother)
        if parent
    }

    fallback_families = {}

    for parent, child in sorted(parent_child_edges):
        if (parent, child) in full_parent_edges:
            continue

        gender = tree.indi[parent].gender

        # Preserve unknown roles in the cache instead of guessing.
        if gender not in {"M", "F"}:
            continue

        role = "father" if gender == "M" else "mother"
        roles = fallback_families.setdefault(child, {})
        roles.setdefault(role, set()).add(parent)

    for child, roles in sorted(fallback_families.items()):
        fathers = roles.get("father", set())
        mothers = roles.get("mother", set())

        # Pair parents only when each role has one known candidate.
        if len(fathers) == 1 and len(mothers) == 1:
            tree.add_trio(
                next(iter(fathers)),
                next(iter(mothers)),
                child,
            )
        else:
            # Multiple candidates cannot be paired without evidence.
            for father in sorted(fathers):
                tree.add_trio(father, None, child)

            for mother in sorted(mothers):
                tree.add_trio(None, mother, child)


def _rebuild_couples(tree, couple_links):
    """Restore couples and their marriage references"""
    for first, second, relationship_id in couple_links.values():
        if (first, second) in tree.fam:
            pair = (first, second)

        elif (second, first) in tree.fam:
            pair = (second, first)

        else:
            pair = (first, second)
            tree.add_fam(*pair)

        tree.indi[first].add_fams(pair)
        tree.indi[second].add_fams(pair)

        if relationship_id:
            tree.fam[pair].add_marriage(relationship_id)


def rebuild_relationships(checkpoint, tree, ids):
    """Rebuild family relationships across all loaded GEDCOM batches"""
    parent_links, parent_child_edges, couple_links = _collect_relationships(
        checkpoint,
        tree,
        ids,
    )

    _rebuild_parentage(
        tree,
        parent_links,
        parent_child_edges,
    )

    _rebuild_couples(tree, couple_links)
