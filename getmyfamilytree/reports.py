"""Readable CSV exports for checking FamilySearch records by person and year.

The GEDCOM and cached FamilySearch responses are not modified. Generated CSVs
are replaced on each export, except the separate review checklist, whose
existing annotations are preserved and new events are appended.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re

PERSON_URL = "/platform/tree/persons?pids="
FAMILIES_URL = "/platform/tree/persons/{}/families"

PEOPLE_COLUMNS = (
    "person_id", "name", "gender", "living", "events",
    "familysearch_url",
)
EVENT_COLUMNS = (
    "event_key", "year", "date_status", "person_id", "person_name",
    "event_type", "date_original", "date_formal", "place", "value",
    "source_references", "familysearch_url",
)
RELATIONSHIP_COLUMNS = (
    "relationship_key", "relationship_type", "person1_id", "person1_name",
    "person2_id", "person2_name", "parent1_id", "parent2_id", "child_id",
    "date_original", "date_formal", "place",
)
REVIEW_COLUMNS = ("event_key", "verified", "notes")
EXACT_YEAR = re.compile(r"(?<!\d)([1-9]\d{3})(?!\d)")
FORMAL_YEAR = re.compile(r"^\+([1-9]\d{3})(?:-\d{2}(?:-\d{2})?)?$")
RANGE = re.compile(
    r"\b(?:between|bet|from|to|entre|hasta|before|after|bef|aft|"
    r"antes|despu[eé]s)\b|\.\.|\b\d{4}\s*[-–/]\s*\d{4}\b",
    re.IGNORECASE,
)
APPROXIMATE = re.compile(
    r"\b(?:about|abt|circa|ca|approx|estimated|est|calculated|"
    r"aprox(?:imadamente)?|hacia|sobre)\b|~|\d{4}s\b",
    re.IGNORECASE,
)
AMBIGUOUS = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")


def _text(value):
    """Return plain source text without trying to translate its meaning."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value) if isinstance(value, (int, float, bool)) else ""


def _safe_cell(value):
    """Prevent spreadsheet formula execution when opening untrusted source data."""
    value = _text(value)
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _name(person):
    display = person.get("display") or {}
    if display.get("name"):
        return _text(display["name"])
    for name in person.get("names") or []:
        for form in name.get("nameForms") or []:
            if form.get("fullText"):
                return _text(form["fullText"])
    return _text(person.get("id"))


def _date_parts(date):
    if not isinstance(date, dict):
        return "", "", ""
    return _text(date.get("original")), _text(date.get("formal")), _text(date.get("normalized"))


def year_for_sorting(date):
    """Return a reference year and an explicit confidence marker.

    Never convert a range to a single year or guess the day/month of ambiguous
    numeric dates. The underlying original/formal date is always preserved.
    """
    original, formal, _ = _date_parts(date)
    years = set(EXACT_YEAR.findall(original))
    if RANGE.search(original) or len(years) > 1 or (
        formal and "/" in formal
    ):
        return "", "range_or_relative"

    if years:
        year = next(iter(years))
        if APPROXIMATE.search(original):
            return year, "approximate"
        if AMBIGUOUS.search(original):
            return year, "ambiguous_day_month"
        return year, "stated_year"

    match = FORMAL_YEAR.fullmatch(formal)
    if match:
        return match.group(1), "formal_year"
    return "", "undated_or_unparsed"


def _place(item):
    place = item.get("place")
    if isinstance(place, dict):
        return _text(place.get("original") or place.get("description"))
    return _text(place)


def _event_key(person_id, fact, occurrence):
    identifier = _text(fact.get("id"))
    if identifier:
        return person_id + ":" + identifier
    fingerprint = hashlib.sha256(
        json.dumps(fact, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return f"{person_id}:hash-{fingerprint}:{occurrence}"


def _relationship_key(relationship):
    identifier = _text(relationship.get("id"))
    if identifier:
        return identifier
    return "hash-" + hashlib.sha256(
        json.dumps(relationship, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]


def _member(relationship, part):
    entry = relationship.get(part)
    return _text(entry.get("resourceId")) if isinstance(entry, dict) else ""


def _source_refs(fact):
    refs = set()
    for item in fact.get("sources") or []:
        if isinstance(item, dict):
            reference = item.get("description") or item.get("descriptionId")
            if reference:
                refs.add(_text(reference))
    return " | ".join(sorted(refs))


def _write_csv(path, columns, rows):
    """Write generated tables atomically with a UTF-8 BOM for Excel/Numbers."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _safe_cell(row.get(column)) for column in columns})
    temporary.replace(path)


def _review_rows(path):
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not set(REVIEW_COLUMNS).issubset(reader.fieldnames):
            raise ValueError(
                f"Review checklist has unexpected columns: {path}. "
                "It has not been overwritten."
            )
        return {
            row["event_key"]: {
                key: row.get(key, "") for key in REVIEW_COLUMNS
            }
            for row in reader if row.get("event_key")
        }


def export_reports(checkpoint, output):
    """Create sorted people/events/relationships CSVs plus a safe review log."""
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    people = []
    events = []
    relations = {}
    names = {}

    for person_id in checkpoint.completed_ids():
        document = checkpoint.get_response(PERSON_URL + person_id) or {}
        person = next(
            (p for p in document.get("persons") or []
             if p.get("id") == person_id), None,
        )
        if not person:
            continue
        name = _name(person)
        names[person_id] = name
        facts = person.get("facts") or []
        gender = (person.get("gender") or {}).get("type", "").rsplit("/", 1)[-1]
        people.append({
            "person_id": person_id,
            "name": name,
            "gender": gender,
            "living": person.get("living", ""),
            "events": len(facts),
            "familysearch_url": f"https://www.familysearch.org/tree/person/details/{person_id}",
        })
        seen = {}
        for fact in facts:
            digest = json.dumps(fact, sort_keys=True, ensure_ascii=False)
            seen[digest] = seen.get(digest, 0) + 1
            date = fact.get("date")
            year, date_status = year_for_sorting(date)
            original, formal, _ = _date_parts(date)
            events.append({
                "event_key": _event_key(person_id, fact, seen[digest]),
                "year": year,
                "date_status": date_status,
                "person_id": person_id,
                "person_name": name,
                "event_type": _text(fact.get("type")).rsplit("/", 1)[-1],
                "date_original": original,
                "date_formal": formal,
                "place": _place(fact),
                "value": _text(fact.get("value")),
                "source_references": _source_refs(fact),
                "familysearch_url": f"https://www.familysearch.org/tree/person/details/{person_id}",
            })
        for document_part in (
            document,
            checkpoint.get_response(FAMILIES_URL.format(person_id)) or {},
        ):
            for category in ("relationships", "childAndParentsRelationships"):
                for relationship in document_part.get(category) or []:
                    key = _relationship_key(relationship)
                    # Keep the fullest available representation of the link.
                    if key not in relations or len(relationship) > len(relations[key]):
                        relations[key] = relationship

    people.sort(key=lambda item: (item["name"].casefold(), item["person_id"]))
    events.sort(key=lambda item: (
        item["year"] == "",
        int(item["year"]) if item["year"] else 0,
        item["person_name"].casefold(),
        item["event_type"],
        item["event_key"],
    ))

    relationships = []
    for key, relationship in relations.items():
        first = _member(relationship, "person1")
        second = _member(relationship, "person2")
        date = next(
            (fact.get("date") for fact in relationship.get("facts") or []
             if isinstance(fact, dict) and fact.get("date")), None,
        )
        place_fact = next(
            (fact for fact in relationship.get("facts") or []
             if isinstance(fact, dict) and fact.get("place")), {},
        )
        original, formal, _ = _date_parts(date)
        relationships.append({
            "relationship_key": key,
            "relationship_type": (
                _text(relationship.get("type")).rsplit("/", 1)[-1]
                or "ChildAndParents"
            ),
            "person1_id": first,
            "person1_name": names.get(first, ""),
            "person2_id": second,
            "person2_name": names.get(second, ""),
            "parent1_id": _member(relationship, "parent1"),
            "parent2_id": _member(relationship, "parent2"),
            "child_id": _member(relationship, "child"),
            "date_original": original,
            "date_formal": formal,
            "place": _place(place_fact),
        })
    relationships.sort(key=lambda item: (
        item["relationship_type"], item["relationship_key"],
    ))

    paths = {
        "people": output.with_suffix(".people.csv"),
        "events": output.with_suffix(".events.csv"),
        "relationships": output.with_suffix(".relationships.csv"),
        "review": output.with_suffix(".review.csv"),
    }
    # Validate a pre-existing review file *before* replacing any generated CSV.
    reviews = _review_rows(paths["review"])
    for event in events:
        reviews.setdefault(event["event_key"], {
            "event_key": event["event_key"], "verified": "", "notes": "",
        })

    _write_csv(paths["people"], PEOPLE_COLUMNS, people)
    _write_csv(paths["events"], EVENT_COLUMNS, events)
    _write_csv(paths["relationships"], RELATIONSHIP_COLUMNS, relationships)
    _write_csv(
        paths["review"], REVIEW_COLUMNS,
        [reviews[key] for key in sorted(reviews)],
    )
    return {
        "paths": paths,
        "people": len(people),
        "events": len(events),
        "relationships": len(relationships),
        "review_items": len(reviews),
    }
