"""Explore every FamilySearch person connected by parent, child or couple links.

The crawl keeps an SQLite checkpoint so that it can resume without revisiting
people already fetched. It deliberately imposes no *generation* boundaries.
"""

from __future__ import annotations

import argparse
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from threading import RLock

from getmyancestors.classes.session import (
    Session,
)
from getmyancestors.classes.tree import (
    Tree,
)
from getmyfamilytree.credentials import (
    CredentialStore,
    CredentialStoreError,
)
from getmyfamilytree.i18n import (
    resolve_language,
    translate,
)
from getmyfamilytree.reports import (
    export_reports,
)
from getmyfamilytree.person_report import (
    write_person_report,
)
from getmyfamilytree.scopes import (
    crawl_scope,
)

PERSON_URL = (
    "/platform/tree/persons?pids="
)
FAMILIES_URL = (
    "/platform/tree/persons/{}/families"
)
PERSON_ID = re.compile(
    r"[A-Z0-9]{4}-[A-Z0-9]{3}\Z"
)
RELATIONSHIP_FIELDS = (
    "childAndParentsRelationships",
    "relationships",
)
MEMBER_FIELDS = (
    "parent1",
    "parent2",
    "child",
    "person1",
    "person2",
)


def _member_id(relationship, name):
    member = relationship.get(name)
    return (
        member.get("resourceId")
        if isinstance(member, dict)
        else None
    )


def related_people(document):
    """Find endpoints in *all* parent/child and couple relationships."""
    found = set()
    for kind in RELATIONSHIP_FIELDS:
        for (
            relationship
        ) in document.get(kind, []):
            for field in MEMBER_FIELDS:
                member = (
                    relationship.get(
                        field
                    )
                )
                if isinstance(
                    member, dict
                ):
                    identifier = (
                        member.get(
                            "resourceId"
                        )
                    )
                    if (
                        identifier
                        and PERSON_ID.fullmatch(
                            identifier
                        )
                    ):
                        found.add(
                            identifier
                        )
    for person in document.get(
        "persons", []
    ):
        identifier = person.get("id")
        if (
            identifier
            and PERSON_ID.fullmatch(
                identifier
            )
        ):
            found.add(identifier)
    return found


def person_display_name(document, fid):
    """Use the FamilySearch display name, falling back to the person ID."""
    for person in document.get(
        "persons", []
    ):
        if person.get("id") != fid:
            continue
        name = (
            person.get("display") or {}
        ).get("name")
        if not name:
            forms = [
                form
                for entry in person.get(
                    "names", []
                )
                for form in entry.get(
                    "nameForms", []
                )
            ]
            name = next(
                (
                    form.get("fullText")
                    for form in forms
                    if form.get(
                        "fullText"
                    )
                ),
                None,
            )
        if name:
            return " ".join(
                str(name).split()
            )
    return fid


class Checkpoint:
    """Persistent discovery queue and successful FamilySearch responses."""

    def __init__(self, path):
        self.path = Path(
            path
        ).expanduser()
        self.path.parent.mkdir(
            parents=True, exist_ok=True
        )
        self.lock = RLock()
        self.connection = (
            sqlite3.connect(
                self.path,
                check_same_thread=False,
            )
        )
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
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS review_notes "
                "(fid TEXT PRIMARY KEY, content TEXT NOT NULL)"
            )

    def load_review_notes(self):
        """Keep manually verified notes when switching download modes."""
        with self.lock:
            return dict(
                self.connection.execute(
                    "SELECT fid, content FROM review_notes"
                ).fetchall()
            )

    def save_review_notes(self, notes):
        with self.lock, self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO review_notes (fid, content) "
                "VALUES (?, ?)",
                (
                    (fid, text)
                    for fid, text in notes.items()
                ),
            )

    def close(self):
        with self.lock:
            self.connection.close()

    def set_origin(
        self, identifier, language="en"
    ):
        """Reject a checkpoint belonging to a different starting person."""
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = 'origin'"
            ).fetchone()
            if (
                row
                and row[0] != identifier
            ):
                raise ValueError(
                    translate(
                        language,
                        "error_checkpoint_origin",
                        existing=row[0],
                        requested=identifier,
                    )
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('origin', ?)",
                (identifier,),
            )

    def get_origin(self):
        """Return the starting FamilySearch ID stored in this checkpoint."""
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = 'origin'"
            ).fetchone()
        return row[0] if row else None

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
                "SELECT COUNT(*) FROM people WHERE status = ?",
                (status,),
            ).fetchone()[
                0
            ]

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
                "SELECT data FROM responses WHERE request_key = ?",
                (key,),
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else None
        )


    def get_responses(self, keys):
        """Read cached JSON responses in bounded SQLite batches"""
        keys = list(dict.fromkeys(keys))

        if not keys:
            return {}

        found = {}
        batch_size = 400

        with self.lock:
            for start in range(0, len(keys), batch_size):
                batch = keys[start:start + batch_size]

                placeholders = ",".join("?" for _ in batch)

                rows = self.connection.execute(
                    f"""
                    SELECT request_key, data
                    FROM responses
                    WHERE request_key IN ({placeholders})
                    """,
                    batch,
                ).fetchall()

                for request_key, data in rows:
                    found[request_key] = json.loads(data)

        return found

    def save_response(
        self, key, document
    ):
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO responses (request_key, data) VALUES (?, ?)",
                (
                    key,
                    json.dumps(
                        document,
                        ensure_ascii=False,
                    ),
                ),
            )

    def finish(self, fid, discovered):
        with self.lock, self.connection:
            added = self.connection.executemany(
                "INSERT OR IGNORE INTO people (fid) VALUES (?)",
                (
                    (person,)
                    for person in sorted(
                        discovered
                    )
                    if person != fid
                ),
            ).rowcount
            self.connection.execute(
                "UPDATE people SET status = 'done' WHERE fid = ?",
                (fid,),
            )
            return added

    def fail(self, fid):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE people SET status = 'failed' WHERE fid = ?",
                (fid,),
            )


def crawl(
    checkpoint,
    session,
    max_people=1000,
    progress=1,
    language="en",
):
    """Explore reachable people and report each completed person by default.
    max_people=0 removes the person cap; progress=0 silences progress messages.
    """
    done = checkpoint.count("done")
    pending = checkpoint.count(
        "pending"
    )
    if progress:
        limit = (
            str(max_people)
            if max_people
            else translate(
                language, "unlimited"
            )
        )
        print(
            translate(
                language,
                "start",
                done=done,
                pending=pending,
                limit=limit,
            ),
            file=sys.stderr,
            flush=True,
        )

    while (
        max_people == 0
        or done < max_people
    ):
        fid = checkpoint.next_pending()
        if fid is None:
            break
        report = bool(
            progress
            and (done + 1) % progress
            == 0
        )
        if report:
            print(
                translate(
                    language,
                    "exploring",
                    position=done + 1,
                    total=(
                        f"/{max_people}"
                        if max_people
                        else ""
                    ),
                    fid=fid,
                ),
                file=sys.stderr,
                flush=True,
            )

        key = PERSON_URL + fid
        document = (
            checkpoint.get_response(key)
        )
        if document is None:
            document = session.get_url(
                key
            )
            if document is not None:
                checkpoint.save_response(
                    key, document
                )
        if not document or not any(
            person.get("id") == fid
            for person in document.get(
                "persons", []
            )
        ):
            checkpoint.fail(fid)
            print(
                translate(
                    language,
                    "retrieval_failed",
                    fid=fid,
                ),
                file=sys.stderr,
                flush=True,
            )
            continue
        # The families endpoint also includes siblings and other relatives
        # that the regular person response may not return.
        families_key = (
            FAMILIES_URL.format(fid)
        )
        families = (
            checkpoint.get_response(
                families_key
            )
        )
        if families is None:
            families = session.get_url(
                families_key
            )
            if families is not None:
                checkpoint.save_response(
                    families_key,
                    families,
                )
        discovered = related_people(
            document
        )
        if families:
            discovered.update(
                related_people(families)
            )
        added = checkpoint.finish(
            fid, discovered
        )
        done += 1
        if report:
            print(
                translate(
                    language,
                    "found",
                    name=person_display_name(
                        document, fid
                    ),
                    fid=fid,
                    added=added,
                    pending=checkpoint.count(
                        "pending"
                    ),
                ),
                file=sys.stderr,
                flush=True,
            )
    pending = checkpoint.count(
        "pending"
    )
    failed = checkpoint.count("failed")
    complete = (
        pending == 0 and failed == 0
    )
    if progress:
        print(
            translate(
                language,
                (
                    "complete"
                    if complete
                    else "paused"
                ),
                done=done,
                pending=pending,
                failed=failed,
            ),
            file=sys.stderr,
            flush=True,
        )
    return complete


def _combined_documents(documents):
    """Recreate the batch response expected by Tree.add_indis from cached pages"""
    combined = {
        key: []
        for key in (
            "persons",
            *RELATIONSHIP_FIELDS,
            "places",
        )
    }
    seen = {
        key: set() for key in combined
    }
    for document in documents:
        for key in combined:
            for item in document.get(
                key, []
            ):
                marker = item.get(
                    "id"
                ) or json.dumps(
                    item, sort_keys=True
                )
                if (
                    marker
                    not in seen[key]
                ):
                    seen[key].add(
                        marker
                    )
                    combined[
                        key
                    ].append(item)
    return combined


class ReplaySession:
    """Expose cached person responses to the original GEDCOM exporter"""

    def __init__(
        self, session, checkpoint
    ):
        self.session = session
        self.checkpoint = checkpoint

    def __getattr__(self, name):
        return getattr(
            self.session, name
        )

    def get_url(
        self,
        url,
        headers=None,
        no_api=False,
    ):
        if url.startswith(PERSON_URL):
            ids = url[
                len(PERSON_URL) :
            ].split(",")
            documents = []
            for fid in ids:
                document = self.checkpoint.get_response(
                    PERSON_URL + fid
                )
                if document is None:
                    raise RuntimeError(
                        f"Missing cached person {fid}"
                    )
                documents.append(
                    document
                )
            combined = (
                _combined_documents(
                    documents
                )
            )
            # A FamilySearch response can include additional relatives whose
            # profiles were not requested. Do not silently export them when
            # the user selected a bounded generation or family scope.
            chosen = set(ids)
            combined["persons"] = [
                person
                for person in combined[
                    "persons"
                ]
                if person.get("id")
                in chosen
            ]
            return combined

        accept = (
            headers.get("Accept", "")
            if headers
            else ""
        )
        key = f"{int(no_api)}:{accept}:{url}"
        document = self.checkpoint.get_response(
            key
        )
        if document is None:
            document = (
                self.session.get_url(
                    url, headers, no_api
                )
            )
            if document is not None:
                self.checkpoint.save_response(
                    key, document
                )
        return document


def assemble(
    checkpoint,
    session,
    output,
    workers=2,
    no_notes=False,
    no_sources=False,
    no_memories=False,
    no_contributors=False,
    selected_ids=None,
    language="en",
):
    """Build a GEDCOM snapshot containing only the selected scope"""
    ids = (
        checkpoint.completed_ids()
        if selected_ids is None
        else list(selected_ids)
    )
    if not ids:
        return 0, 0
    tree = Tree(
        ReplaySession(
            session, checkpoint
        ),
        no_sources=no_sources,
        no_memories=no_memories,
    )
    total = len(ids)
    batch_size = 100

    for start in range(
        0, total, batch_size
    ):
        end = min(
            start + batch_size, total
        )

        print(
            translate(
                language,
                "gedcom_loading_batch",
                start=start + 1,
                end=end,
                total=total,
            ),
            file=sys.stderr,
            flush=True,
        )

        tree.add_indis(ids[start:end])

        print(
            translate(
                language,
                "gedcom_people_progress",
                count=end,
                total=total,
            ),
            file=sys.stderr,
            flush=True,
        )

    # Rebuild relationships from *all* cached responses, not just those
    # visible while Tree.add_indis was loading a particular batch.
    # Otherwise couples and children crossing a 200-person batch boundary
    # are silently omitted from the GEDCOM.
    parent_links = set()
    parent_child_edges = set()
    couple_links = {}
    for fid in ids:
        for document in (
            checkpoint.get_response(
                PERSON_URL + fid
            ),
            checkpoint.get_response(
                FAMILIES_URL.format(fid)
            ),
        ):
            if not document:
                continue
            for (
                relationship
            ) in document.get(
                "childAndParentsRelationships",
                [],
            ):
                child = _member_id(
                    relationship,
                    "child",
                )
                father = _member_id(
                    relationship,
                    "parent1",
                )
                mother = _member_id(
                    relationship,
                    "parent2",
                )
                if child in tree.indi:
                    parent_links.add(
                        (
                            father,
                            mother,
                            child,
                        )
                    )
            for (
                relationship
            ) in document.get(
                "relationships", []
            ):
                kind = relationship.get(
                    "type"
                )
                first = _member_id(
                    relationship,
                    "person1",
                )
                second = _member_id(
                    relationship,
                    "person2",
                )
                if (
                    kind
                    == "http://gedcomx.org/ParentChild"
                ):
                    if (
                        first
                        in tree.indi
                        and second
                        in tree.indi
                    ):
                        parent_child_edges.add(
                            (
                                first,
                                second,
                            )
                        )
                elif (
                    kind
                    == "http://gedcomx.org/Couple"
                ):
                    if (
                        first
                        in tree.indi
                        and second
                        in tree.indi
                    ):
                        couple_links.setdefault(
                            frozenset(
                                (
                                    first,
                                    second,
                                )
                            ),
                            (
                                first,
                                second,
                                relationship.get(
                                    "id"
                                ),
                            ),
                        )

    for father, mother, child in sorted(
        parent_links,
        key=lambda link: tuple(
            item or "" for item in link
        ),
    ):
        father = (
            father
            if father in tree.indi
            else None
        )
        mother = (
            mother
            if mother in tree.indi
            else None
        )
        if father or mother:
            tree.add_trio(
                father, mother, child
            )

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
    for parent, child in sorted(
        parent_child_edges
    ):
        if (
            parent,
            child,
        ) in full_parent_edges:
            continue
        gender = tree.indi[
            parent
        ].gender
        if gender not in {"M", "F"}:
            # The relationship is retained in SQLite; do not silently
            # invent a father/mother role for a person of unknown sex.
            continue
        role = (
            "father"
            if gender == "M"
            else "mother"
        )
        roles = fallback_families.setdefault(
            child, {}
        )
        roles.setdefault(
            role, set()
        ).add(parent)

    for child, roles in sorted(
        fallback_families.items()
    ):
        fathers = roles.get(
            "father", set()
        )
        mothers = roles.get(
            "mother", set()
        )
        # Multiple partners cannot be safely paired without an explicit
        # ChildAndParents relationship. Keep those parents separate.
        if (
            len(fathers) == 1
            and len(mothers) == 1
        ):
            tree.add_trio(
                next(iter(fathers)),
                next(iter(mothers)),
                child,
            )
        else:
            for father in sorted(
                fathers
            ):
                tree.add_trio(
                    father, None, child
                )
            for mother in sorted(
                mothers
            ):
                tree.add_trio(
                    None, mother, child
                )

    for (
        first,
        second,
        relationship_id,
    ) in couple_links.values():
        if (first, second) in tree.fam:
            pair = (first, second)
        elif (
            second,
            first,
        ) in tree.fam:
            pair = (second, first)
        else:
            pair = (first, second)
            tree.add_fam(*pair)
        tree.indi[first].add_fams(pair)
        tree.indi[second].add_fams(pair)
        if relationship_id:
            tree.fam[pair].add_marriage(
                relationship_id
            )

    if (
        not no_notes
        or not no_contributors
    ):
        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:
            futures = []
            if not no_notes:
                futures.extend(
                    executor.submit(
                        person.get_notes
                    )
                    for person in tree.indi.values()
                )
                futures.extend(
                    executor.submit(
                        family.get_notes
                    )
                    for family in tree.fam.values()
                )
            if not no_contributors:
                futures.extend(
                    executor.submit(
                        person.get_contributors
                    )
                    for person in tree.indi.values()
                )
                futures.extend(
                    executor.submit(
                        family.get_contributors
                    )
                    for family in tree.fam.values()
                )
            total_tasks = len(futures)

            print(
                translate(
                    language,
                    "gedcom_extra_start",
                    total=total_tasks,
                ),
                file=sys.stderr,
                flush=True,
            )

            for (
                count,
                future,
            ) in enumerate(
                as_completed(futures), 1
            ):
                future.result()

                if (
                    count % 100 == 0
                    or count
                    == total_tasks
                ):
                    print(
                        translate(
                            language,
                            "gedcom_extra_progress",
                            count=count,
                            total=total_tasks,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

    tree.reset_num()
    output = Path(output).expanduser()
    output.parent.mkdir(
        parents=True, exist_ok=True
    )
    temporary = output.with_name(
        output.name + ".tmp"
    )
    with temporary.open(
        "w", encoding="utf-8"
    ) as file:
        tree.print(file)
    temporary.replace(output)
    return len(tree.indi), len(tree.fam)


def resolve_output_path(destination):
    """Accept either an explicit .ged file or a folder for a new export"""
    destination = Path(
        destination
    ).expanduser()
    if (
        destination.suffix.lower()
        == ".ged"
    ):
        return destination
    if destination.suffix:
        raise ValueError(
            "Expected a .ged filename or a folder path"
        )
    return (
        destination
        / "GetMyFamilyTree.ged"
    )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)

    if argv and argv[0] == "research":
        from getmyfamilytree.research_cli import run as run_research

        return run_research(argv[1:], Checkpoint)
    # Read the language before constructing the translated argparse help
    language_parser = (
        argparse.ArgumentParser(
            add_help=False
        )
    )
    language_parser.add_argument(
        "--language",
        "--lang",
        choices=("auto", "en", "es"),
        default="auto",
    )
    language_options, _ = (
        language_parser.parse_known_args(
            argv
        )
    )
    language = resolve_language(
        language_options.language
    )

    def tr(message, **values):
        return translate(
            language, message, **values
        )

    parser = argparse.ArgumentParser(
        description=tr("description")
    )
    parser.add_argument(
        "--language",
        "--lang",
        choices=("auto", "en", "es"),
        default="auto",
        help=tr("help_language"),
    )
    parser.add_argument(
        "-i",
        "--individual",
        help=tr("help_individual"),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=tr("help_output"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        help=tr("help_database"),
    )
    parser.add_argument(
        "--max-people",
        type=int,
        default=1000,
        help=tr("help_max_people"),
    )
    parser.add_argument(
        "--scope",
        choices=(
            "full",
            "ancestors",
            "descendants",
            "family",
        ),
        default="full",
        help=tr("help_scope"),
    )
    parser.add_argument(
        "--generations",
        type=int,
        metavar="N",
        help=tr("help_generations"),
    )
    parser.add_argument(
        "--distance",
        type=int,
        metavar="N",
        help=tr("help_distance"),
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=2,
        help=tr("help_rate_limit"),
    )
    parser.add_argument(
        "--remember-login",
        action="store_true",
        help=tr("help_remember"),
    )
    parser.add_argument(
        "--forget-login",
        action="store_true",
        help=tr("help_forget"),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1,
        metavar="N",
        help=tr("help_progress"),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help=tr("help_report_only"),
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help=tr("help_csv"),
    )
    parser.add_argument(
        "--no-notes",
        action="store_true",
        help=tr("help_no_notes"),
    )
    parser.add_argument(
        "--no-sources",
        action="store_true",
        help=tr("help_no_sources"),
    )
    parser.add_argument(
        "--no-memories",
        action="store_true",
        help=tr("help_no_memories"),
    )
    parser.add_argument(
        "--no-contributors",
        action="store_true",
        help=tr("help_no_contributors"),
    )
    args = parser.parse_args(argv)
    if args.scope in (
        "ancestors",
        "descendants",
    ):
        if (
            args.generations is None
            or args.generations < 0
            or args.distance is not None
        ):
            parser.error(
                tr("error_generations")
            )
    elif args.scope == "family":
        if (
            args.distance is None
            or args.distance < 0
            or args.generations
            is not None
        ):
            parser.error(
                tr("error_distance")
            )
    elif (
        args.generations is not None
        or args.distance is not None
    ):
        parser.error(
            tr("error_full_options")
        )
    if (
        args.max_people < 0
        or args.rate_limit < 1
        or args.progress_every < 0
    ):
        parser.error(
            tr("error_invalid_limits")
        )
    credentials = CredentialStore()
    if args.forget_login:
        if args.remember_login:
            parser.error(
                tr(
                    "error_forget_conflict"
                )
            )
        try:
            removed = (
                credentials.clear()
            )
        except CredentialStoreError:
            parser.exit(
                2,
                tr(
                    "error_forget_keyring"
                )
                + "\n",
            )
        print(
            tr(
                "saved_removed"
                if removed
                else "saved_not_found"
            )
        )
        return

    if not args.output:
        parser.error(
            tr(
                "error_report_requires_output"
            )
            if args.report_only
            else tr("error_required")
        )
    try:
        output_path = (
            resolve_output_path(
                args.output
            )
        )
    except ValueError:
        parser.error(
            tr("error_output_path")
        )

    checkpoint_path = (
        args.database
        or output_path.with_suffix(
            ".sqlite"
        )
    )
    if args.report_only:
        if (
            not checkpoint_path.expanduser().is_file()
        ):
            parser.error(
                tr(
                    "error_report_database_missing",
                    path=checkpoint_path,
                )
            )
        report_checkpoint = Checkpoint(
            checkpoint_path
        )
        try:
            selected_ids = None
            if args.scope != "full":
                origin = (
                    report_checkpoint.get_origin()
                )
                if not origin:
                    parser.error(
                        tr(
                            "error_scope_origin"
                        )
                    )
                scoped = crawl_scope(
                    report_checkpoint,
                    None,
                    origin,
                    scope=args.scope,
                    generations=args.generations,
                    distance=args.distance,
                    max_people=args.max_people,
                    progress=0,
                    language=language,
                )
                selected_ids = (
                    scoped.ids
                )
            result = write_person_report(
                report_checkpoint,
                output_path,
                language=language,
                selected_ids=selected_ids,
            )
            if args.csv:
                export_reports(
                    report_checkpoint,
                    output_path,
                    selected_ids=selected_ids,
                )
        finally:
            report_checkpoint.close()
        print(
            tr(
                "person_report_saved",
                people=result["people"],
                events=result["events"],
                path=result["path"],
            )
        )
        return

    if not args.individual:
        parser.error(
            tr("error_required")
        )
    if not PERSON_ID.fullmatch(
        args.individual
    ):
        parser.error(
            tr("error_invalid_id")
        )
    if (
        args.max_people < 0
        or args.rate_limit < 1
        or args.progress_every < 0
    ):
        parser.error(
            tr("error_invalid_limits")
        )
    checkpoint = Checkpoint(
        checkpoint_path
    )
    try:
        try:
            checkpoint.set_origin(
                args.individual,
                language=language,
            )
        except ValueError as error:
            parser.exit(2, f"{error}\n")
        checkpoint.seed(
            [args.individual]
        )
        checkpoint.retry_failed()
        try:
            saved_login = (
                credentials.load()
            )
        except CredentialStoreError:
            saved_login = None
            print(
                tr("error_keyring"),
                file=sys.stderr,
            )

        using_saved_login = (
            saved_login is not None
        )
        if saved_login:
            username, password = (
                saved_login
            )
            print(
                tr("saved_login_used"),
                file=sys.stderr,
            )
        else:
            username = input(
                tr("username_prompt")
            )
            password = getpass.getpass(
                tr("password_prompt")
            )

        session = Session(
            username,
            password,
            rate_limit=args.rate_limit,
            delay=0.2,
        )
        if (
            not session.logged
            and using_saved_login
        ):
            print(
                tr(
                    "saved_login_rejected"
                ),
                file=sys.stderr,
            )
            username = input(
                tr("username_prompt")
            )
            password = getpass.getpass(
                tr("password_prompt")
            )
            session = Session(
                username,
                password,
                rate_limit=args.rate_limit,
                delay=0.2,
            )
        if not session.logged:
            parser.exit(
                2,
                tr("error_login")
                + "\n",
            )

        if args.remember_login or (
            using_saved_login
            and saved_login
            != (username, password)
        ):
            try:
                credentials.save(
                    username, password
                )
                print(
                    tr("login_saved"),
                    file=sys.stderr,
                )
            except CredentialStoreError:
                print(
                    tr(
                        "login_save_failed"
                    ),
                    file=sys.stderr,
                )
        del password

        print(
            tr(
                "checkpoint",
                path=checkpoint.path,
            ),
            file=sys.stderr,
        )
        scoped = crawl_scope(
            checkpoint,
            session,
            args.individual,
            scope=args.scope,
            generations=args.generations,
            distance=args.distance,
            max_people=args.max_people,
            progress=args.progress_every,
            language=language,
        )
        if scoped.interrupted:
            print(
                "\n"
                + tr("interrupted"),
                file=sys.stderr,
            )

        print(
            tr(
                "building",
                done=len(scoped.ids),
            ),
            file=sys.stderr,
            flush=True,
        )
        n, families = assemble(
            checkpoint,
            session,
            output_path,
            no_notes=args.no_notes,
            no_sources=args.no_sources,
            no_memories=args.no_memories,
            no_contributors=args.no_contributors,
            selected_ids=scoped.ids,
            language=language,
        )
        print(
            tr(
                "gedcom_done",
                path=output_path,
            ),
            file=sys.stderr,
            flush=True,
        )

        print(
            tr("report_building"),
            file=sys.stderr,
            flush=True,
        )
        report = write_person_report(
            checkpoint,
            output_path,
            language=language,
            selected_ids=scoped.ids,
        )
        if args.csv:
            export_reports(
                checkpoint,
                output_path,
                selected_ids=scoped.ids,
            )
        print(
            tr(
                "person_report_saved",
                people=report["people"],
                events=report["events"],
                path=report["path"],
            ),
            file=sys.stderr,
        )
        pending = scoped.pending
        failed = scoped.failed
        print(
            tr(
                "saved_tree",
                people=n,
                families=families,
                path=output_path,
                pending=pending,
                failed=failed,
            ),
            file=sys.stderr,
        )
        if not scoped.exhausted:
            print(
                tr("resume"),
                file=sys.stderr,
            )
    finally:
        checkpoint.close()


if __name__ == "__main__":
    main()
