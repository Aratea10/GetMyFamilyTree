"""GEDCOM tree adapter with deterministic worker cleanup."""

from concurrent.futures import ThreadPoolExecutor

from getmyancestors.classes.tree import MAX_PERSONS, Indi, Tree


class ExportTree(Tree):
    """Extend the upstream tree with managed worker resources."""

    def _save_places(self, data):
        """Retain place coordinates from the person response."""
        for place in data.get("places", []):
            if place["id"] not in self.places:
                self.places[place["id"]] = (
                    str(place["latitude"]),
                    str(place["longitude"]),
                )

    def _add_people(self, data, executor):
        """Initialize individuals and wait for their worker tasks."""
        futures = []

        for person in data["persons"]:
            fid = person["id"]
            individual = Indi(fid, self)
            self.indi[fid] = individual
            futures.append(executor.submit(individual.add_data, person))

        for future in futures:
            future.result()

    def _add_parent_relationships(self, data):
        """Preserve upstream parent and child links for loaded individuals."""
        for relationship in data.get("childAndParentsRelationships", []):
            father = (
                relationship["parent1"]["resourceId"]
                if "parent1" in relationship
                else None
            )
            mother = (
                relationship["parent2"]["resourceId"]
                if "parent2" in relationship
                else None
            )
            child = (
                relationship["child"]["resourceId"]
                if "child" in relationship
                else None
            )

            if child in self.indi:
                self.indi[child].parents.add((father, mother))
            if father in self.indi:
                self.indi[father].children.add((father, mother, child))
            if mother in self.indi:
                self.indi[mother].children.add((father, mother, child))

    def _add_couple_relationships(self, data):
        """Preserve upstream couples and their relationship identifiers."""
        for relationship in data.get("relationships", []):
            if relationship["type"] != "http://gedcomx.org/Couple":
                continue

            person1 = relationship["person1"]["resourceId"]
            person2 = relationship["person2"]["resourceId"]
            relfid = relationship["id"]
            couple = (person1, person2, relfid)

            if person1 in self.indi:
                self.indi[person1].spouses.add(couple)
            if person2 in self.indi:
                self.indi[person2].spouses.add(couple)

    def add_indis(self, fids):
        """Load individuals without creating an asyncio event loop.

        Adapted from getmyancestors 1.2.0, Tree.add_indis.
        Preserve the upstream person and relationship handling while
        ensuring that worker threads are properly released.
        """
        new_fids = [fid for fid in fids if fid and fid not in self.indi]

        if not new_fids:
            return

        with ThreadPoolExecutor() as executor:
            while new_fids:
                batch = new_fids[:MAX_PERSONS]
                data = self.fs.get_url("/platform/tree/persons?pids=" + ",".join(batch))

                if data:
                    self._save_places(data)
                    self._add_people(data, executor)
                    self._add_parent_relationships(data)
                    self._add_couple_relationships(data)

                new_fids = new_fids[MAX_PERSONS:]
