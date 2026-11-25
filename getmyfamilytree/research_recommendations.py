"""Recommend a research order from verified cached relationships only"""

from collections import defaultdict, deque

from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.scopes import _edges, _person_name

_CATEGORY_ORDER = {
    "origin": 0,
    "ancestor": 1,
    "descendant": 2,
    "collateral": 3,
    "in_law": 4,
    "unclassified": 5,
}


def _distances(starts, neighbours):
    """Find shortest distances from one or more starting people"""
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
    """Read the recorded gender without inferring a missing value"""
    person = next(
        (person for person in document.get("persons", []) if person.get("id") == fid),
        {},
    )

    return ((person.get("gender") or {}).get("type") or "").rsplit("/", 1)[-1].lower()


class _RecommendationBuilder:
    """Calculate genealogy recommendations without modifying the checkpoint"""

    def __init__(self, checkpoint, identifiers):
        self.checkpoint = checkpoint
        self.identifiers = list(dict.fromkeys(identifiers))
        self.known = set(self.identifiers)
        self.origin = checkpoint.get_origin()

    def _load_graph(self):
        """Load cached people and relationships using batched SQLite reads"""
        request_keys = [PERSON_URL + fid for fid in self.identifiers]
        request_keys.extend(FAMILIES_URL.format(fid) for fid in self.identifiers)

        cached = self.checkpoint.get_responses(request_keys)

        documents = {
            fid: cached.get(PERSON_URL + fid) or {} for fid in self.identifiers
        }

        self.names = {
            fid: _person_name(documents[fid], fid) for fid in self.identifiers
        }
        self.sexes = {fid: _sex(documents[fid], fid) for fid in self.identifiers}

        self.parents = defaultdict(set)
        self.children = defaultdict(set)
        self.partners = defaultdict(set)

        for fid in self.identifiers:
            family_document = cached.get(FAMILIES_URL.format(fid)) or {}

            for document in (documents[fid], family_document):
                for kind, first, second in _edges(document):
                    if first not in self.known or second not in self.known:
                        continue

                    if kind == "parent_child":
                        self.parents[second].add(first)
                        self.children[first].add(second)
                    elif kind == "couple":
                        self.partners[first].add(second)
                        self.partners[second].add(first)

    def _build_branches(self):
        """Identify blood relatives and their starting-person branches"""
        origin_known = self.origin in self.known

        self.ancestor_depth = (
            _distances([self.origin], self.parents) if origin_known else {}
        )
        self.descendant_depth = (
            _distances([self.origin], self.children) if origin_known else {}
        )

        root_order = {"male": 0, "female": 1}

        self.roots = sorted(
            self.parents.get(self.origin, ()),
            key=lambda fid: (
                root_order.get(self.sexes[fid], 2),
                self.names[fid].casefold(),
                fid,
            ),
        )

        self.root_index = {fid: index for index, fid in enumerate(self.roots)}

        # Do not guess paternal or maternal labels when parentage
        # is incomplete, unknown or ambiguous.
        self.root_sides = dict.fromkeys(self.roots, "unknown")

        if len(self.roots) == 2 and {self.sexes[fid] for fid in self.roots} == {
            "male",
            "female",
        }:
            for fid in self.roots:
                self.root_sides[fid] = (
                    "paternal" if self.sexes[fid] == "male" else "maternal"
                )

        self.family = (
            _parentage_family(self.origin, self.parents, self.children)
            if origin_known
            else set()
        )

        self.memberships = defaultdict(set)

        for root in self.roots:
            for relative in _parentage_family(
                root,
                self.parents,
                self.children,
            ):
                self.memberships[relative].add(root)

    def _build_blood_distances(self):
        """Calculate shortest undirected distances between blood relatives"""
        blood_links = defaultdict(set)

        for child_id, parent_ids in self.parents.items():
            for parent_id in parent_ids:
                blood_links[child_id].add(parent_id)
                blood_links[parent_id].add(child_id)

        self.blood_links = blood_links
        self.blood_distance = (
            _distances([self.origin], blood_links) if self.origin in self.known else {}
        )

    def _build_affinities(self):
        """Find relatives connected through partners of blood relatives"""
        self.by_marriage = defaultdict(set)
        self.inlaw_distance = {}
        self.affinity_links = defaultdict(set)

        blood_relatives_by_partner = defaultdict(set)

        for blood_person in self.family:
            for spouse_id in self.partners.get(blood_person, ()):
                blood_relatives_by_partner[spouse_id].add(blood_person)

        # Process each partner separately without retaining full
        # secondary family graphs in memory.
        for spouse_id, blood_relatives in sorted(blood_relatives_by_partner.items()):
            self._add_partner_relatives(spouse_id, blood_relatives)

    def _add_partner_relatives(self, spouse_id, blood_relatives):
        """Merge one partner's external relatives and affinity links"""
        relatives = _parentage_family(
            spouse_id,
            self.parents,
            self.children,
        )

        external_relatives = relatives - self.family

        if not external_relatives:
            return

        restricted_links = {fid: self.blood_links[fid] & relatives for fid in relatives}
        spouse_distances = _distances(
            [spouse_id],
            restricted_links,
        )

        branch_ids = set()

        for blood_person in blood_relatives:
            branch_ids.update(self.memberships.get(blood_person, ()))

        closest_blood_distance = min(
            self.blood_distance.get(blood_person, 10_000)
            for blood_person in blood_relatives
        )

        links = {(blood_person, spouse_id) for blood_person in blood_relatives}

        for relative in external_relatives:
            self.by_marriage[relative].update(branch_ids)
            self.affinity_links[relative].update(links)

            distance = (
                closest_blood_distance + 1 + spouse_distances.get(relative, 10_000)
            )

            self.inlaw_distance[relative] = min(
                distance,
                self.inlaw_distance.get(relative, float("inf")),
            )

    def _classify(self, fid):
        """Choose a person's automatic research category and generation"""
        if fid == self.origin:
            return "origin", 0

        if fid in self.ancestor_depth:
            return "ancestor", self.ancestor_depth[fid]

        if fid in self.descendant_depth:
            return "descendant", self.descendant_depth[fid]

        if fid in self.family:
            return "collateral", None

        if fid in self.by_marriage:
            return "in_law", None

        return "unclassified", None

    def _suggest_person(self, fid):
        """Build one recommendation using the established output format"""
        category, generation = self._classify(fid)

        branch_ids = (
            self.by_marriage[fid] if category == "in_law" else self.memberships[fid]
        )

        branches = [
            {
                "id": root,
                "name": self.names[root],
                "side": self.root_sides[root],
            }
            for root in self.roots
            if root in branch_ids
        ]

        distance = (
            self.inlaw_distance.get(fid, 10_000)
            if category == "in_law"
            else self.blood_distance.get(fid, 10_000)
        )

        affinity_links = [
            {
                "relative_id": relative_id,
                "partner_id": partner_id,
            }
            for relative_id, partner_id in sorted(self.affinity_links.get(fid, ()))
        ]

        return {
            "name": self.names[fid],
            "category": category,
            "generation": generation,
            "branches": branches,
            "affinity_links": affinity_links,
            "recommendation_key": (
                _CATEGORY_ORDER[category],
                min(
                    (self.root_index[root] for root in branch_ids),
                    default=len(self.roots),
                ),
                distance,
            ),
        }

    def build(self):
        """Run all calculation stages and return one suggestion per person"""
        self._load_graph()
        self._build_branches()
        self._build_blood_distances()
        self._build_affinities()

        return {fid: self._suggest_person(fid) for fid in self.identifiers}


def build_recommendations(checkpoint, identifiers):
    """Return one recommendation per person without changing the cache"""
    return _RecommendationBuilder(checkpoint, identifiers).build()
