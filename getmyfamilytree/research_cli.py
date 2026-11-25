"""Offline command-line interface for the research planner"""

import argparse
import json
from collections import Counter
from pathlib import Path

from getmyfamilytree.i18n import resolve_language
from getmyfamilytree.research_plan import (
    VALID_CATEGORIES,
    VALID_STATUSES,
    ResearchPlan,
)


def message(language, spanish, english):
    return spanish if language == "es" else english


def build_parser():
    parser = argparse.ArgumentParser(
        prog="getmyfamilytree research",
        description="Manage genealogy research without downloading data",
    )

    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Existing SQLite genealogy database",
    )

    parser.add_argument(
        "--language",
        choices=("auto", "en", "es"),
        default="auto",
    )

    commands = parser.add_subparsers(
        dest="action",
        required=True,
    )

    listing = commands.add_parser(
        "list",
        help="List and filter research tasks",
    )
    listing.add_argument(
        "--order",
        choices=("recommended", "manual"),
        default="recommended",
    )
    listing.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
    )
    listing.add_argument(
        "--category",
        choices=sorted(VALID_CATEGORIES),
    )
    listing.add_argument("--branch")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--json", action="store_true")

    summary = commands.add_parser(
        "summary",
        help="Show research progress",
    )
    summary.add_argument("--json", action="store_true")

    status = commands.add_parser(
        "status",
        help="Change a person's research status",
    )
    status.add_argument("fid")
    status.add_argument("value", choices=sorted(VALID_STATUSES))

    priority = commands.add_parser(
        "priority",
        help="Change a person's priority",
    )
    priority.add_argument("fid")
    priority.add_argument("value", type=int, choices=(0, 1, 2))

    category = commands.add_parser(
        "category",
        help="Override or restore a recommended category",
    )
    category.add_argument("fid")
    category.add_argument(
        "value",
        choices=[*sorted(VALID_CATEGORIES), "auto"],
    )

    branches = commands.add_parser(
        "branches",
        help="Override branches or restore automatic classification",
    )
    branches.add_argument("fid")
    branches.add_argument(
        "values",
        nargs="+",
        help="Parent IDs, or 'auto'",
    )

    move = commands.add_parser(
        "move",
        help="Move a person in the manual research order",
    )
    move.add_argument("fid")
    move.add_argument("--before")

    server = commands.add_parser(
        "serve",
        help="Start the local, token-protected research JSON API",
    )
    server.add_argument("--port", type=int, default=0)

    return parser


def filter_items(items, args):
    if args.status:
        items = [item for item in items if item["status"] == args.status]

    if args.category:
        items = [item for item in items if item["category"] == args.category]

    if args.branch:
        items = [
            item
            for item in items
            if args.branch in {branch["id"] for branch in item["branches"]}
        ]

    return items


def print_listing(plan, args, language):
    items = filter_items(
        plan.items(order=args.order),
        args,
    )

    total = len(items)

    if args.limit == 0:
        page = items[args.offset :]
    else:
        page = items[args.offset : args.offset + args.limit]

    if args.json:
        print(
            json.dumps(
                {
                    "total": total,
                    "offset": args.offset,
                    "count": len(page),
                    "items": page,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(
        message(
            language,
            f"Mostrando {len(page)} de {total} personas",
            f"Showing {len(page)} of {total} people",
        )
    )

    print("ID\tName\tStatus\tPriority\tCategory\tBranches")

    for item in page:
        branch_ids = ",".join(branch["id"] for branch in item["branches"])

        print(
            f"{item['id']}\t"
            f"{item['name']}\t"
            f"{item['status']}\t"
            f"{item['priority']}\t"
            f"{item['category']}\t"
            f"{branch_ids}"
        )


def print_summary(plan, args, language):
    items = plan.items()

    statuses = Counter(item["status"] for item in items)
    categories = Counter(item["category"] for item in items)

    result = {
        "people": len(items),
        "statuses": {status: statuses[status] for status in sorted(VALID_STATUSES)},
        "categories": {
            category: categories[category] for category in sorted(VALID_CATEGORIES)
        },
    }

    if args.json:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(
        message(
            language,
            f"Personas: {result['people']}",
            f"People: {result['people']}",
        )
    )

    print("\nStatus:")
    for key, value in result["statuses"].items():
        print(f"  {key}: {value}")

    print("\nCategories:")
    for key, value in result["categories"].items():
        print(f"  {key}: {value}")


def _update_branches(plan, args):
    """Apply one manual branch update or restore automatic classification."""
    if args.values == ["auto"]:
        plan.set_branches(args.fid, None)
    elif "auto" in args.values:
        raise ValueError("'auto' cannot be combined with branch IDs")
    else:
        plan.set_branches(args.fid, args.values)


def _execute_command(plan, args, language):
    """Dispatch one parsed research command to the appropriate operation."""
    if args.action == "serve":
        from getmyfamilytree.research_api import serve_research

        serve_research(plan.checkpoint, port=args.port)
        return

    if args.action == "list":
        print_listing(plan, args, language)
        return

    if args.action == "summary":
        print_summary(plan, args, language)
        return

    if args.action == "status":
        plan.set_status(args.fid, args.value)
    elif args.action == "priority":
        plan.set_priority(args.fid, args.value)
    elif args.action == "category":
        value = None if args.value == "auto" else args.value
        plan.set_category(args.fid, value)
    elif args.action == "branches":
        _update_branches(plan, args)
    elif args.action == "move":
        plan.move(args.fid, before_id=args.before)

    print(
        message(
            language,
            f"Actualizado: {args.fid}",
            f"Updated: {args.fid}",
        )
    )


def run(argv, checkpoint_factory):
    """Execute an offline research command.

    The checkpoint factory is injected to avoid circular imports.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    language = resolve_language(args.language)
    database = args.database.expanduser()

    if not database.is_file():
        parser.error(
            message(
                language,
                f"No existe la base de datos: {database}",
                f"Database does not exist: {database}",
            )
        )

    if args.action == "list" and (args.limit < 0 or args.offset < 0):
        parser.error("--limit and --offset must be non-negative")

    if args.action == "serve" and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    checkpoint = checkpoint_factory(database)

    try:
        if not checkpoint.get_origin():
            parser.error(
                message(
                    language,
                    "La base de datos no tiene una persona inicial.",
                    "The database has no starting person.",
                )
            )

        plan = ResearchPlan(checkpoint)
        plan.sync()

        try:
            _execute_command(plan, args, language)
        except (ValueError, KeyError) as error:
            detail = error.args[0] if isinstance(error, KeyError) else str(error)
            parser.error(str(detail))
    finally:
        checkpoint.close()
