"""Recommend a research order from verified cached relationships only"""

from collections import defaultdict, deque

from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.scopes import _edges, _person_name


def _distances(starts, neighbours):
    result = {}
    queue = deque()

    for fid in sorted(starts):
        if fid not in result:
            result[fid] = 0
            queue.append(fid)

    while queue:
        fid = queue.popleft()
        for other in sorted(neighbours.get(fid, ())):
            if other not in result:
                result[other] = result[fid] + 1
                queue.append(other)

    return result


def _sex(document, fid):
    person = next(
        (person for person in document.get("persons", []) if person.get("id") == fid),
        {},
    )
    return ((person.get("gender") or {}).get("type") or "").rsplit("/", 1)[-1].lower()


def build_recommendations(checkpoint, identifiers):
    """Return one recommendation per person without changing the cache"""
    identifiers = list(dict.fromkeys(identifiers))
    known = set(identifiers)
    origin = checkpoint.get_origin()

    documents = {
        fid: checkpoint.get_response(PERSON_URL + fid) or {} for fid in identifiers
    }
    names = {fid: _person_name(documents[fid], fid) for fid in identifiers}
    sexes = {fid: _sex(documents[fid], fid) for fid in identifiers}

    parents = defaultdict(set)
    children = defaultdict(set)
    partners = defaultdict(set)

    for fid in identifiers:
        family_document = checkpoint.get_response(FAMILIES_URL.format(fid)) or {}

        for document in (documents[fid], family_document):
            for kind, first, second in _edges(document):
                if first not in known or second not in known:
                    continue

                if kind == "parent_child":
                    parents[second].add(first)
                    children[first].add(second)
                elif kind == "couple":
                    partners[first].add(second)
                    partners[second].add(first)

    ancestor_depth = _distances([origin], parents) if origin in known else {}
    descendant_depth = _distances([origin], children) if origin in known else {}

    root_order = {"male": 0, "female": 1}
    roots = sorted(
        parents.get(origin, ()),
        key=lambda fid: (
            root_order.get(sexes[fid], 2),
            names[fid].casefold(),
            fid,
        ),
    )
    root_index = {fid: index for index, fid in enumerate(roots)}

    memberships = defaultdict(set)

    for root in roots:
        older_generations = _distances([root], parents)
        blood_relatives = _distances(older_generations, children)

        for relative in blood_relatives:
            memberships[relative].add(root)

    by_marriage = defaultdict(set)

    for person_id, branch_ids in memberships.items():
        for spouse_id in partners.get(person_id, ()):
            by_marriage[spouse_id].update(branch_ids)

    blood_links = defaultdict(set)

    for child_id, parent_ids in parents.items():
        for parent_id in parent_ids:
            blood_links[child_id].add(parent_id)
            blood_links[parent_id].add(child_id)

    blood_distance = _distances([origin], blood_links) if origin in known else {}
    core = set(ancestor_depth) | set(descendant_depth)

    category_order = {
        "origin": 0,
        "ancestor": 1,
        "descendant": 2,
        "collateral": 3,
        "in_law": 4,
        "unclassified": 5,
    }

    suggestions = {}

    for fid in identifiers:
        if fid == origin:
            category = "origin"
            generation = 0
        elif fid in ancestor_depth:
            category = "ancestor"
            generation = ancestor_depth[fid]
        elif fid in descendant_depth:
            category = "descendant"
            generation = descendant_depth[fid]
        elif memberships[fid]:
            category = "collateral"
            generation = None
        elif by_marriage[fid] or partners[fid].intersection(core):
            category = "in_law"
            generation = None
        else:
            category = "unclassified"
            generation = None

        branch_ids = memberships[fid]

        if category == "in_law":
            branch_ids = by_marriage[fid]

        branches = [
            {
                "id": root,
                "name": names[root],
                "side": {
                    "male": "paternal",
                    "female": "maternal",
                }.get(sexes[root], "unknown"),
            }
            for root in roots
            if root in branch_ids
        ]

        distance = blood_distance.get(fid, 10_000)

        if category == "in_law":
            distance = min(
                (
                    blood_distance[relative] + 1
                    for relative in partners[fid]
                    if relative in blood_distance
                ),
                default=10_000,
            )

        suggestions[fid] = {
            "name": names[fid],
            "category": category,
            "generation": generation,
            "branches": branches,
            "recommendation_key": (
                category_order[category],
                min(
                    (root_index[root] for root in branch_ids),
                    default=len(roots),
                ),
                distance,
            ),
        }

    return suggestions
