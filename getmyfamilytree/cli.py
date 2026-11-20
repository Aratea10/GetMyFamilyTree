"""Explore every FamilySearch person connected by parent, child or couple links.

The crawl keeps an SQLite checkpoint so that it can resume without revisiting
people already fetched. It deliberately imposes no *generation* boundaries.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from threading import RLock

from getmyancestors.classes.session import Session
from getmyancestors.classes.tree import Tree
from getmyfamilytree.credentials import CredentialStore, CredentialStoreError
from getmyfamilytree.i18n import resolve_language, translate

PERSON_URL = "/platform/tree/persons?pids="
FAMILIES_URL = "/platform/tree/persons/{}/families"
PERSON_ID = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{3}\Z")
RELATIONSHIP_FIELDS = ("childAndParentsRelationships", "relationships")
MEMBER_FIELDS = ("parent1", "parent2", "child", "person1", "person2")


def _member_id(relationship, name):
    member = relationship.get(name)
    return member.get("resourceId") if isinstance(member, dict) else None


def related_people(document):
    """Find endpoints in *all* parent/child and couple relationships."""
    found = set()
    for kind in RELATIONSHIP_FIELDS:
        for relationship in document.get(kind, []):
            for field in MEMBER_FIELDS:
                member = relationship.get(field)
                if isinstance(member, dict):
                    identifier = member.get("resourceId")
                    if identifier and PERSON_ID.fullmatch(identifier):
                        found.add(identifier)
    for person in document.get("persons", []):
        identifier = person.get("id")
        if identifier and PERSON_ID.fullmatch(identifier):
            found.add(identifier)
    return found


def person_display_name(document, fid):
    """Use the FamilySearch display name, falling back to the person ID."""
    for person in document.get("persons", []):
        if person.get("id") != fid:
            continue
        name = (person.get("display") or {}).get("name")
        if not name:
            forms = [
                form
                for entry in person.get("names", [])
                for form in entry.get("nameForms", [])
            ]
            name = next(
                (form.get("fullText") for form in forms if form.get("fullText")),
                None,
            )
        if name:
            return " ".join(str(name).split())
    return fid


class Checkpoint:
    """Persistent discovery queue and successful FamilySearch responses."""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        with self.lock, self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS people "
                "(fid TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending')"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS responses "
                "(request_key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS people_by_status ON people(status)"
            )

    def close(self):
        with self.lock:
            self.connection.close()

    def set_origin(self, identifier, language="en"):
        """Reject a checkpoint belonging to a different starting person."""
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = 'origin'"
            ).fetchone()
            if row and row[0] != identifier:
                raise ValueError(
                    translate(language, "error_checkpoint_origin",
                              existing=row[0], requested=identifier)
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('origin', ?)",
                (identifier,),
            )

    def seed(self, ids):
        with self.lock, self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO people (fid) VALUES (?)",
                ((fid,) for fid in ids),
            )

    def retry_failed(self):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE people SET status = 'pending' WHERE status = 'failed'"
            )

    def next_pending(self):
        with self.lock:
            row = self.connection.execute(
                "SELECT fid FROM people WHERE status = 'pending' "
                "ORDER BY rowid LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def count(self, status):
        with self.lock:
            return self.connection.execute(
                "SELECT COUNT(*) FROM people WHERE status = ?", (status,)
            ).fetchone()[0]

    def completed_ids(self):
        with self.lock:
            return [
                row[0]
                for row in self.connection.execute(
                    "SELECT fid FROM people WHERE status = 'done' ORDER BY rowid"
                )
            ]

    def get_response(self, key):
        with self.lock:
            row = self.connection.execute(
                "SELECT data FROM responses WHERE request_key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_response(self, key, document):
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO responses (request_key, data) VALUES (?, ?)",
                (key, json.dumps(document, ensure_ascii=False)),
            )

    def finish(self, fid, discovered):
        with self.lock, self.connection:
            added = self.connection.executemany(
                "INSERT OR IGNORE INTO people (fid) VALUES (?)",
                ((person,) for person in sorted(discovered) if person != fid),
            ).rowcount
            self.connection.execute(
                "UPDATE people SET status = 'done' WHERE fid = ?", (fid,)
            )
            return added

    def fail(self, fid):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE people SET status = 'failed' WHERE fid = ?", (fid,)
            )


def crawl(checkpoint, session, max_people=1000, progress=1, language="en"):
    """Explore reachable people and report each completed person by default.

    max_people=0 removes the person cap; progress=0 silences progress messages.
    """
    done = checkpoint.count("done")
    pending = checkpoint.count("pending")
    if progress:
        limit = str(max_people) if max_people else translate(language, "unlimited")
        print(
            translate(language, "start", done=done, pending=pending, limit=limit),
            file=sys.stderr, flush=True,
        )

    while max_people == 0 or done < max_people:
        fid = checkpoint.next_pending()
        if fid is None:
            break
        report = bool(progress and (done + 1) % progress == 0)
        if report:
            print(
                translate(language, "exploring", position=done + 1,
                          total=f"/{max_people}" if max_people else "", fid=fid),
                file=sys.stderr, flush=True,
            )

        key = PERSON_URL + fid
        document = checkpoint.get_response(key)
        if document is None:
            document = session.get_url(key)
            if document is not None:
                checkpoint.save_response(key, document)
        if not document or not any(
            person.get("id") == fid for person in document.get("persons", [])
        ):
            checkpoint.fail(fid)
            print(
                translate(language, "retrieval_failed", fid=fid),
                file=sys.stderr, flush=True,
            )
            continue
        # The families endpoint also includes siblings and other relatives
        # that the regular person response may not return.
        families_key = FAMILIES_URL.format(fid)
        families = checkpoint.get_response(families_key)
        if families is None:
            families = session.get_url(families_key)
            if families is not None:
                checkpoint.save_response(families_key, families)
        discovered = related_people(document)
        if families:
            discovered.update(related_people(families))
        added = checkpoint.finish(fid, discovered)
        done += 1
        if report:
            print(
                translate(language, "found", name=person_display_name(document, fid),
                          fid=fid, added=added,
                          pending=checkpoint.count("pending")),
                file=sys.stderr, flush=True,
            )
    pending = checkpoint.count("pending")
    failed = checkpoint.count("failed")
    complete = pending == 0 and failed == 0
    if progress:
        print(
            translate(language, "complete" if complete else "paused",
                      done=done, pending=pending, failed=failed),
            file=sys.stderr, flush=True,
        )
    return complete


def _combined_documents(documents):
    """Recreate the batch response expected by Tree.add_indis from cached pages."""
    combined = {key: [] for key in ("persons", *RELATIONSHIP_FIELDS, "places")}
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
    """Expose cached person responses to the original GEDCOM exporter."""

    def __init__(self, session, checkpoint):
        self.session = session
        self.checkpoint = checkpoint

    def __getattr__(self, name):
        return getattr(self.session, name)

    def get_url(self, url, headers=None, no_api=False):
        if url.startswith(PERSON_URL):
            ids = url[len(PERSON_URL):].split(",")
            documents = []
            for fid in ids:
                document = self.checkpoint.get_response(PERSON_URL + fid)
                if document is None:
                    raise RuntimeError(f"Missing cached person {fid}")
                documents.append(document)
            return _combined_documents(documents)

        accept = headers.get("Accept", "") if headers else ""
        key = f"{int(no_api)}:{accept}:{url}"
        document = self.checkpoint.get_response(key)
        if document is None:
            document = self.session.get_url(url, headers, no_api)
            if document is not None:
                self.checkpoint.save_response(key, document)
        return document


def assemble(
    checkpoint, session, output, workers=2, no_notes=False,
    no_sources=False, no_memories=False, no_contributors=False,
):
    """Build a GEDCOM snapshot from the fetched part of the connected graph."""
    ids = checkpoint.completed_ids()
    if not ids:
        return 0, 0
    tree = Tree(ReplaySession(session, checkpoint), no_sources=no_sources, no_memories=no_memories)
    tree.add_indis(ids)

    # Rebuild relationships from *all* cached responses, not just those
    # visible while Tree.add_indis was loading a particular batch.
    # Otherwise couples and children crossing a 200-person batch boundary
    # are silently omitted from the GEDCOM.
    parent_links = set()
    parent_child_edges = set()
    couple_links = {}
    for fid in ids:
        for document in (
            checkpoint.get_response(PERSON_URL + fid),
            checkpoint.get_response(FAMILIES_URL.format(fid)),
        ):
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
                elif kind == "http://gedcomx.org/Couple":
                    if first in tree.indi and second in tree.indi:
                        couple_links.setdefault(frozenset((first, second)), (
                            first, second, relationship.get("id")
                        ))

    for father, mother, child in sorted(
        parent_links, key=lambda link: tuple(item or "" for item in link)
    ):
        father = father if father in tree.indi else None
        mother = mother if mother in tree.indi else None
        if father or mother:
            tree.add_trio(father, mother, child)

    # Families may return a ParentChild link without a full
    # ChildAndParents relationship. Preserve such links if no matching
    # full relationship was found in the cached person responses.
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
        if gender not in {"M", "F"}:
            # The relationship is retained in SQLite; do not silently
            # invent a father/mother role for a person of unknown sex.
            continue
        role = "father" if gender == "M" else "mother"
        roles = fallback_families.setdefault(child, {})
        roles.setdefault(role, set()).add(parent)

    for child, roles in sorted(fallback_families.items()):
        fathers = roles.get("father", set())
        mothers = roles.get("mother", set())
        # Multiple partners cannot be safely paired without an explicit
        # ChildAndParents relationship. Keep those parents separate.
        if len(fathers) == 1 and len(mothers) == 1:
            tree.add_trio(next(iter(fathers)), next(iter(mothers)), child)
        else:
            for father in sorted(fathers):
                tree.add_trio(father, None, child)
            for mother in sorted(mothers):
                tree.add_trio(None, mother, child)

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

    if not no_notes or not no_contributors:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            if not no_notes:
                futures.extend(
                    executor.submit(person.get_notes)
                    for person in tree.indi.values()
                )
                futures.extend(
                    executor.submit(family.get_notes)
                    for family in tree.fam.values()
                )
            if not no_contributors:
                futures.extend(
                    executor.submit(person.get_contributors)
                    for person in tree.indi.values()
                )
                futures.extend(
                    executor.submit(family.get_contributors)
                    for family in tree.fam.values()
                )
            for future in futures:
                future.result()

    tree.reset_num()
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        tree.print(file)
    temporary.replace(output)
    return len(tree.indi), len(tree.fam)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explore all accessible FamilySearch relatives in every direction."
    )
    parser.add_argument("-i", "--individual", help="FamilySearch person ID")
    parser.add_argument("-o", "--output", type=Path, help="Output GEDCOM")
    parser.add_argument(
        "--database", type=Path,
        help="Persistent SQLite checkpoint (defaults to output with .sqlite suffix)"
    )
    parser.add_argument(
        "--max-people", type=int, default=1000,
        help="Pause after this many people; 0 means no cap (potentially enormous)"
    )
    parser.add_argument(
        "--rate-limit", type=int, default=2,
        help="Maximum HTTP requests per second, passed to FamilySearch session"
    )
    parser.add_argument(
        "--remember-login", action="store_true",
        help="After a successful login, save credentials in the system keyring"
    )
    parser.add_argument(
        "--forget-login", action="store_true",
        help="Remove saved FamilySearch credentials and exit"
    )
    parser.add_argument(
        "--progress-every", type=int, default=1, metavar="N",
        help="Show progress every N people (default: 1; 0 disables progress)"
    )
    parser.add_argument("--no-notes", action="store_true")
    parser.add_argument("--no-sources", action="store_true")
    parser.add_argument("--no-memories", action="store_true")
    parser.add_argument("--no-contributors", action="store_true")
    args = parser.parse_args(argv)
    credentials = CredentialStore()
    if args.forget_login:
        if args.remember_login:
            parser.error("--forget-login cannot be combined with --remember-login")
        try:
            removed = credentials.clear()
        except CredentialStoreError:
            parser.exit(2, "Cannot access the system keyring to remove credentials.\n")
        print("Saved FamilySearch login removed." if removed else "No saved FamilySearch login found.")
        return
    if not args.individual or not args.output:
        parser.error("both -i/--individual and -o/--output are required for a download")
    if not PERSON_ID.fullmatch(args.individual):
        parser.error("Invalid FamilySearch ID")
    if args.max_people < 0 or args.rate_limit < 1 or args.progress_every < 0:
        parser.error("max-people and progress-every must be >= 0; rate-limit must be >= 1")
    checkpoint_path = args.database or args.output.with_suffix(".sqlite")
    checkpoint = Checkpoint(checkpoint_path)
    try:
        try:
            checkpoint.set_origin(args.individual)
        except ValueError as error:
            parser.exit(2, f"{error}\n")
        checkpoint.seed([args.individual])
        checkpoint.retry_failed()
        try:
            saved_login = credentials.load()
        except CredentialStoreError:
            saved_login = None
            print(
                "System keyring unavailable; please enter FamilySearch credentials.",
                file=sys.stderr,
            )

        using_saved_login = saved_login is not None
        if saved_login:
            username, password = saved_login
            print("Using FamilySearch login from the system keyring.", file=sys.stderr)
        else:
            username = input("Enter FamilySearch username: ")
            password = getpass.getpass("Enter FamilySearch password: ")

        session = Session(
            username, password, rate_limit=args.rate_limit, delay=0.2
        )
        if not session.logged and using_saved_login:
            print(
                "Saved login was not accepted; enter your current credentials.",
                file=sys.stderr,
            )
            username = input("Enter FamilySearch username: ")
            password = getpass.getpass("Enter FamilySearch password: ")
            session = Session(
                username, password, rate_limit=args.rate_limit, delay=0.2
            )
        if not session.logged:
            parser.exit(2, "Could not log in to FamilySearch.\n")

        if args.remember_login or (using_saved_login and saved_login != (username, password)):
            try:
                credentials.save(username, password)
                print("FamilySearch login saved in the system keyring.", file=sys.stderr)
            except CredentialStoreError:
                print(
                    "Could not save login in the system keyring. "
                    "You will be prompted next time.",
                    file=sys.stderr,
                )
        del password
        print(f"Checkpoint: {checkpoint.path}", file=sys.stderr)
        try:
            exhausted = crawl(
                checkpoint, session, max_people=args.max_people,
                progress=args.progress_every,
            )
        except KeyboardInterrupt:
            exhausted = False
            print("\nPaused. Writing GEDCOM snapshot...", file=sys.stderr)
        print(
            f"Building GEDCOM from {checkpoint.count('done')} retrieved people...",
            file=sys.stderr, flush=True,
        )
        n, families = assemble(
            checkpoint, session, args.output,
            no_notes=args.no_notes, no_sources=args.no_sources,
            no_memories=args.no_memories,
            no_contributors=args.no_contributors,
        )
        pending = checkpoint.count("pending")
        failed = checkpoint.count("failed")
        print(
            f"Saved {n} people and {families} families to {args.output}. "
            f"Pending: {pending}; failed: {failed}.",
            file=sys.stderr,
        )
        if not exhausted:
            print(
                "Not yet exhaustive. Run the SAME command again to resume "
                "from the SQLite checkpoint.",
                file=sys.stderr,
            )
    finally:
        checkpoint.close()


if __name__ == "__main__":
    main()
