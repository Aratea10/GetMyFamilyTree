"""Explore FamilySearch relatives and export genealogy data.

The crawl uses an SQLite checkpoint to resume without revisiting people
already fetched. The full scope has no generation boundaries.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from getmyancestors.classes.session import Session

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.credentials import CredentialStore, CredentialStoreError
from getmyfamilytree.gedcom_export import (
    RELATIONSHIP_FIELDS,
    ReplaySession,
    rebuild_relationships,
)
from getmyfamilytree.gedcom_tree import ExportTree
from getmyfamilytree.i18n import resolve_language, translate
from getmyfamilytree.person_report import write_person_report
from getmyfamilytree.reports import export_reports
from getmyfamilytree.scopes import crawl_scope

PERSON_URL = "/platform/tree/persons?pids="
FAMILIES_URL = "/platform/tree/persons/{}/families"
PERSON_ID = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{3}\Z")

MEMBER_FIELDS = (
    "parent1",
    "parent2",
    "child",
    "person1",
    "person2",
)


def related_people(document):
    """Find endpoints in all parent/child and couple relationships"""
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
    """Use the FamilySearch display name, falling back to the person ID"""
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


def _load_cached_response(checkpoint, session, key):
    """Return a cached response or fetch and store it when missing."""
    document = checkpoint.get_response(key)

    if document is None:
        document = session.get_url(key)

        if document is not None:
            checkpoint.save_response(key, document)

    return document


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
    pending = checkpoint.count("pending")

    if progress:
        limit = str(max_people) if max_people else translate(language, "unlimited")

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

    while max_people == 0 or done < max_people:
        fid = checkpoint.next_pending()

        if fid is None:
            break

        report = bool(progress and (done + 1) % progress == 0)

        if report:
            print(
                translate(
                    language,
                    "exploring",
                    position=done + 1,
                    total=(f"/{max_people}" if max_people else ""),
                    fid=fid,
                ),
                file=sys.stderr,
                flush=True,
            )

        document = _load_cached_response(
            checkpoint,
            session,
            PERSON_URL + fid,
        )

        if not document or not any(
            person.get("id") == fid for person in document.get("persons", [])
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

        # The families endpoint includes relatives that the regular
        # person response may not return.
        families = _load_cached_response(
            checkpoint,
            session,
            FAMILIES_URL.format(fid),
        )

        discovered = related_people(document)

        if families:
            discovered.update(related_people(families))

        added = checkpoint.finish(fid, discovered)
        done += 1

        if report:
            print(
                translate(
                    language,
                    "found",
                    name=person_display_name(document, fid),
                    fid=fid,
                    added=added,
                    pending=checkpoint.count("pending"),
                ),
                file=sys.stderr,
                flush=True,
            )

    pending = checkpoint.count("pending")
    failed = checkpoint.count("failed")
    complete = pending == 0 and failed == 0

    if progress:
        print(
            translate(
                language,
                "complete" if complete else "paused",
                done=done,
                pending=pending,
                failed=failed,
            ),
            file=sys.stderr,
            flush=True,
        )

    return complete


# Existing callers rely on this public signature.
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
    ids = checkpoint.completed_ids() if selected_ids is None else list(selected_ids)

    if not ids:
        return 0, 0

    tree = ExportTree(
        ReplaySession(session, checkpoint),
        no_sources=no_sources,
        no_memories=no_memories,
    )

    total = len(ids)
    batch_size = 100

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)

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

    rebuild_relationships(checkpoint, tree, ids)

    if not no_notes or not no_contributors:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []

            if not no_notes:
                futures.extend(
                    executor.submit(person.get_notes) for person in tree.indi.values()
                )
                futures.extend(
                    executor.submit(family.get_notes) for family in tree.fam.values()
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

            for count, future in enumerate(as_completed(futures), 1):
                future.result()

                if count % 100 == 0 or count == total_tasks:
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
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary = output.with_name(output.name + ".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        tree.print(file)

    temporary.replace(output)

    return len(tree.indi), len(tree.fam)


def resolve_output_path(destination):
    """Accept either an explicit .ged file or a folder for a new export"""
    destination = Path(destination).expanduser()

    if destination.suffix.lower() == ".ged":
        return destination

    if destination.suffix:
        raise ValueError("Expected a .ged filename or a folder path")

    return destination / "GetMyFamilyTree.ged"


def _build_parser(language):
    """Create the translated command-line argument parser."""

    def tr(message, **values):
        return translate(language, message, **values)

    parser = argparse.ArgumentParser(description=tr("description"))

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
        choices=("full", "ancestors", "descendants", "family"),
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

    return parser


def _validate_arguments(parser, args, language):
    """Reject incompatible scope options and invalid limits"""

    def tr(message):
        return translate(language, message)

    if args.scope in ("ancestors", "descendants"):
        if (
            args.generations is None
            or args.generations < 0
            or args.distance is not None
        ):
            parser.error(tr("error_generations"))

    elif args.scope == "family":
        if args.distance is None or args.distance < 0 or args.generations is not None:
            parser.error(tr("error_distance"))

    elif args.generations is not None or args.distance is not None:
        parser.error(tr("error_full_options"))

    if args.max_people < 0 or args.rate_limit < 1 or args.progress_every < 0:
        parser.error(tr("error_invalid_limits"))


def _parse_arguments(argv):
    """Resolve the interface language and parse validated CLI options"""
    language_parser = argparse.ArgumentParser(add_help=False)
    language_parser.add_argument(
        "--language",
        "--lang",
        choices=("auto", "en", "es"),
        default="auto",
    )

    language_options, _ = language_parser.parse_known_args(argv)
    language = resolve_language(language_options.language)

    parser = _build_parser(language)
    args = parser.parse_args(argv)
    _validate_arguments(parser, args, language)

    return parser, args, language


def _forget_saved_login(parser, args, language):
    """Remove stored FamilySearch credentials when explicitly requested"""
    if args.remember_login:
        parser.error(translate(language, "error_forget_conflict"))

    credentials = CredentialStore()

    try:
        removed = credentials.clear()
    except CredentialStoreError:
        parser.exit(
            2,
            translate(language, "error_forget_keyring") + "\n",
        )

    message = "saved_removed" if removed else "saved_not_found"
    print(translate(language, message))


def _resolve_paths(parser, args, language):
    """Validate the requested export path and locate the SQLite checkpoint"""
    if not args.output:
        message = (
            "error_report_requires_output" if args.report_only else "error_required"
        )
        parser.error(translate(language, message))

    try:
        output_path = resolve_output_path(args.output)
    except ValueError:
        parser.error(translate(language, "error_output_path"))

    checkpoint_path = args.database or output_path.with_suffix(".sqlite")
    return output_path, checkpoint_path


def _run_report_only(args, parser, language, output_path, checkpoint_path):
    """Generate review reports from existing cached genealogy data"""
    if not checkpoint_path.expanduser().is_file():
        parser.error(
            translate(
                language,
                "error_report_database_missing",
                path=checkpoint_path,
            )
        )

    checkpoint = Checkpoint(checkpoint_path)

    try:
        selected_ids = None

        if args.scope != "full":
            origin = checkpoint.get_origin()

            if not origin:
                parser.error(translate(language, "error_scope_origin"))

            scoped = crawl_scope(
                checkpoint,
                None,
                origin,
                scope=args.scope,
                generations=args.generations,
                distance=args.distance,
                max_people=args.max_people,
                progress=0,
                language=language,
            )
            selected_ids = scoped.ids

        result = write_person_report(
            checkpoint,
            output_path,
            language=language,
            selected_ids=selected_ids,
        )

        if args.csv:
            export_reports(
                checkpoint,
                output_path,
                selected_ids=selected_ids,
            )

    finally:
        checkpoint.close()

    print(
        translate(
            language,
            "person_report_saved",
            people=result["people"],
            events=result["events"],
            path=result["path"],
        )
    )


def _authenticate(args, parser, language, credentials):
    """Authenticate with FamilySearch, optionally using the system keyring"""
    try:
        saved_login = credentials.load()
    except CredentialStoreError:
        saved_login = None
        print(
            translate(language, "error_keyring"),
            file=sys.stderr,
        )

    using_saved_login = saved_login is not None

    if saved_login:
        username, password = saved_login
        print(
            translate(language, "saved_login_used"),
            file=sys.stderr,
        )
    else:
        username = input(translate(language, "username_prompt"))
        password = getpass.getpass(translate(language, "password_prompt"))

    session = Session(
        username,
        password,
        rate_limit=args.rate_limit,
        delay=0.2,
    )

    if not session.logged and using_saved_login:
        print(
            translate(language, "saved_login_rejected"),
            file=sys.stderr,
        )

        username = input(translate(language, "username_prompt"))
        password = getpass.getpass(translate(language, "password_prompt"))

        session = Session(
            username,
            password,
            rate_limit=args.rate_limit,
            delay=0.2,
        )

    if not session.logged:
        parser.exit(
            2,
            translate(language, "error_login") + "\n",
        )

    if args.remember_login or (
        using_saved_login and saved_login != (username, password)
    ):
        try:
            credentials.save(username, password)
            print(
                translate(language, "login_saved"),
                file=sys.stderr,
            )
        except CredentialStoreError:
            print(
                translate(language, "login_save_failed"),
                file=sys.stderr,
            )

    del password
    return session


def _export_with_session(checkpoint, session, args, output_path, language):
    """Explore the requested scope and produce GEDCOM and review reports"""
    print(
        translate(
            language,
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
            "\n" + translate(language, "interrupted"),
            file=sys.stderr,
        )

    print(
        translate(
            language,
            "building",
            done=len(scoped.ids),
        ),
        file=sys.stderr,
        flush=True,
    )

    people, families = assemble(
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
        translate(
            language,
            "gedcom_done",
            path=output_path,
        ),
        file=sys.stderr,
        flush=True,
    )

    print(
        translate(language, "report_building"),
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
        translate(
            language,
            "person_report_saved",
            people=report["people"],
            events=report["events"],
            path=report["path"],
        ),
        file=sys.stderr,
    )

    print(
        translate(
            language,
            "saved_tree",
            people=people,
            families=families,
            path=output_path,
            pending=scoped.pending,
            failed=scoped.failed,
        ),
        file=sys.stderr,
    )

    if not scoped.exhausted:
        print(
            translate(language, "resume"),
            file=sys.stderr,
        )


def _run_export(args, parser, language, output_path, checkpoint_path):
    """Open a checkpoint, authenticate and run a genealogy export"""
    checkpoint = Checkpoint(checkpoint_path)

    try:
        try:
            checkpoint.set_origin(
                args.individual,
                language=language,
            )
        except ValueError as error:
            parser.exit(2, f"{error}\n")

        checkpoint.seed([args.individual])
        checkpoint.retry_failed()

        credentials = CredentialStore()
        session = _authenticate(args, parser, language, credentials)

        _export_with_session(
            checkpoint,
            session,
            args,
            output_path,
            language,
        )

    finally:
        checkpoint.close()


def main(argv=None):
    """Dispatch research, report-only and FamilySearch export commands"""
    argv = sys.argv[1:] if argv is None else list(argv)

    if argv and argv[0] == "research":
        from getmyfamilytree.research_cli import run as run_research

        return run_research(argv[1:], Checkpoint)

    parser, args, language = _parse_arguments(argv)

    if args.forget_login:
        return _forget_saved_login(parser, args, language)

    output_path, checkpoint_path = _resolve_paths(parser, args, language)

    if args.report_only:
        return _run_report_only(
            args,
            parser,
            language,
            output_path,
            checkpoint_path,
        )

    if not args.individual:
        parser.error(translate(language, "error_required"))

    if not PERSON_ID.fullmatch(args.individual):
        parser.error(translate(language, "error_invalid_id"))

    return _run_export(
        args,
        parser,
        language,
        output_path,
        checkpoint_path,
    )


if __name__ == "__main__":
    main()
