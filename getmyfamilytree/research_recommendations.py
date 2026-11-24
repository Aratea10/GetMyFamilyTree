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


def _parentage_family(person_id, parents, children):
    """Find relatives through parent-child links, not through partners"""
    ancestors = _distances([person_id], parents)
    return set(_distances(ancestors, children))


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

    request_keys = [PERSON_URL + fid for fid in identifiers]

    request_keys.extend(FAMILIES_URL.format(fid) for fid in identifiers)

    cached = checkpoint.get_responses(request_keys)

    documents = {fid: cached.get(PERSON_URL + fid) or {} for fid in identifiers}

    names = {fid: _person_name(documents[fid], fid) for fid in identifiers}
    sexes = {fid: _sex(documents[fid], fid) for fid in identifiers}

    parents = defaultdict(set)
    children = defaultdict(set)
    partners = defaultdict(set)

    for fid in identifiers:
        family_document = cached.get(FAMILIES_URL.format(fid)) or {}

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

    # Do not guess paternal/maternal labels when parentage
    # is incomplete or ambiguous.
    root_sides = dict.fromkeys(roots, "unknown")

    if len(roots) == 2 and {sexes[fid] for fid in roots} == {"male", "female"}:
        for fid in roots:
            root_sides[fid] = "paternal" if sexes[fid] == "male" else "maternal"

    family = _parentage_family(origin, parents, children) if origin in known else set()

    memberships = defaultdict(set)

    for root in roots:
        for relative in _parentage_family(root, parents, children):
            memberships[relative].add(root)

    blood_links = defaultdict(set)

    for child_id, parent_ids in parents.items():
        for parent_id in parent_ids:
            blood_links[child_id].add(parent_id)
            blood_links[parent_id].add(child_id)

    blood_distance = _distances([origin], blood_links) if origin in known else {}

    by_marriage = defaultdict(set)
    inlaw_distance = {}
    affinity_links = defaultdict(set)

    # Group blood relatives by their shared partners.
    blood_relatives_by_partner = defaultdict(set)

    for blood_person in family:
        for spouse_id in partners.get(blood_person, ()):
            blood_relatives_by_partner[spouse_id].add(blood_person)

    # Process each partner without retaining every family graph.
    for spouse_id, blood_relatives in sorted(blood_relatives_by_partner.items()):
        relatives = _parentage_family(spouse_id, parents, children)

        external_relatives = relatives - family

        if not external_relatives:
            continue

        restricted_links = {fid: blood_links[fid] & relatives for fid in relatives}

        spouse_distances = _distances([spouse_id], restricted_links)

        branch_ids = set()

        for blood_person in blood_relatives:
            branch_ids.update(memberships.get(blood_person, ()))

        closest_blood_distance = min(
            blood_distance.get(blood_person, 10_000) for blood_person in blood_relatives
        )

        links = {(blood_person, spouse_id) for blood_person in blood_relatives}

        for relative in external_relatives:
            by_marriage[relative].update(branch_ids)
            affinity_links[relative].update(links)

            distance = (
                closest_blood_distance + 1 + spouse_distances.get(relative, 10_000)
            )

            inlaw_distance[relative] = min(
                distance,
                inlaw_distance.get(relative, float("inf")),
            )

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
        elif fid in family:
            category = "collateral"
            generation = None
        elif fid in by_marriage:
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
                "side": root_sides[root],
            }
            for root in roots
            if root in branch_ids
        ]

        distance = blood_distance.get(fid, 10_000)

        if category == "in_law":
            distance = inlaw_distance.get(fid, 10_000)

        suggestions[fid] = {
            "name": names[fid],
            "category": category,
            "generation": generation,
            "branches": branches,
            "affinity_links": [
                {
                    "relative_id": relative_id,
                    "partner_id": partner_id,
                }
                for relative_id, partner_id in sorted(affinity_links.get(fid, ()))
            ],
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
