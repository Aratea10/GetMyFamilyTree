
"""GEDCOM tree adapter with deterministic worker cleanup"""

from concurrent.futures import ThreadPoolExecutor

from getmyancestors.classes.tree import MAX_PERSONS, Indi, Tree


class ExportTree(Tree):
    """Extend the upstream tree with managed worker resources"""

    def add_indis(self, fids):
        """Load individuals without creating an asyncio event loop.

        Adapted from getmyancestors 1.2.0, Tree.add_indis.
        Preserve the upstream person and relationship handling while
        ensuring that worker threads are properly released.
        """
        new_fids = [
            fid for fid in fids
            if fid and fid not in self.indi
        ]

        if not new_fids:
            return

        with ThreadPoolExecutor() as executor:
            while new_fids:
                batch = new_fids[:MAX_PERSONS]

                data = self.fs.get_url(
                    "/platform/tree/persons?pids="
                    + ",".join(batch)
                )

                if data:
                    for place in data.get("places", []):
                        if place["id"] not in self.places:
                            self.places[place["id"]] = (
                                str(place["latitude"]),
                                str(place["longitude"]),
                            )

                    futures = []

                    for person in data["persons"]:
                        fid = person["id"]
                        individual = Indi(fid, self)
                        self.indi[fid] = individual

                        futures.append(
                            executor.submit(
                                individual.add_data,
                                person,
                            )
                        )

                    for future in futures:
                        future.result()

                    for rel in data.get(
                        "childAndParentsRelationships", []
                    ):
                        father = (
                            rel["parent1"]["resourceId"]
                            if "parent1" in rel
                            else None
                        )

                        mother = (
                            rel["parent2"]["resourceId"]
                            if "parent2" in rel
                            else None
                        )

                        child = (
                            rel["child"]["resourceId"]
                            if "child" in rel
                            else None
                        )

                        if child in self.indi:
                            self.indi[child].parents.add(
                                (father, mother)
                            )

                        if father in self.indi:
                            self.indi[father].children.add(
                                (father, mother, child)
                            )

                        if mother in self.indi:
                            self.indi[mother].children.add(
                                (father, mother, child)
                            )

                    for rel in data.get("relationships", []):
                        if rel["type"] != "http://gedcomx.org/Couple":
                            continue

                        person1 = rel["person1"]["resourceId"]
                        person2 = rel["person2"]["resourceId"]
                        relfid = rel["id"]

                        relationship = (
                            person1, person2, relfid
                        )

                        if person1 in self.indi:
                            self.indi[person1].spouses.add(
                                relationship
                            )

                        if person2 in self.indi:
                            self.indi[person2].spouses.add(
                                relationship
                            )

                new_fids = new_fids[MAX_PERSONS:]
