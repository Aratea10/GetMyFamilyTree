"""Readable CSV exports for checking FamilySearch records by person and year.

The GEDCOM and cached FamilySearch responses are not modified. Generated CSVs
are replaced on each export, except the separate review checklist, whose
existing annotations are preserved and new events are appended.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

PERSON_URL = "/platform/tree/persons?pids="
FAMILIES_URL = "/platform/tree/persons/{}/families"

PEOPLE_COLUMNS = (
    "person_id",
    "name",
    "gender",
    "living",
    "events",
    "familysearch_url",
)
EVENT_COLUMNS = (
    "event_key",
    "year",
    "date_status",
    "person_id",
    "person_name",
    "event_type",
    "date_original",
    "date_formal",
    "place",
    "value",
    "source_references",
    "familysearch_url",
)
RELATIONSHIP_COLUMNS = (
    "relationship_key",
    "relationship_type",
    "person1_id",
    "person1_name",
    "person2_id",
    "person2_name",
    "parent1_id",
    "parent2_id",
    "child_id",
    "date_original",
    "date_formal",
    "place",
)
SOURCE_COLUMNS = (
    "source_key",
    "owner_type",
    "owner_id",
    "owner_name",
    "source_id",
    "title",
    "citation",
    "url",
    "description",
    "source_notes",
    "attribution_note",
    "details_available",
)
NOTE_COLUMNS = (
    "note_key",
    "owner_type",
    "owner_id",
    "owner_name",
    "subject",
    "text",
)
MEMORY_COLUMNS = (
    "memory_key",
    "person_id",
    "person_name",
    "evidence_id",
    "memory_id",
    "media_type",
    "title",
    "description",
    "url",
    "details_available",
)
REVIEW_COLUMNS = ("event_key", "verified", "notes")
EXACT_YEAR = re.compile(r"(?<!\d)([1-9]\d{3})(?!\d)")
FORMAL_YEAR = re.compile(r"^\+([1-9]\d{3})(?:-\d{2}(?:-\d{2})?)?$")
RANGE = re.compile(
    r"\b(?:between|bet|from|to|entre|hasta|before|after|bef|aft|"
    r"antes|despu[eé]s)\b|\.\.|\b\d{4}\s*[-\u2013/]\s*\d{4}\b",
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
    return (
        _text(date.get("original")),
        _text(date.get("formal")),
        _text(date.get("normalized")),
    )


def year_for_sorting(date):
    """Return a reference year and an explicit confidence marker.

    Never convert a range to a single year or guess the day/month of ambiguous
    numeric dates. The underlying original/formal date is always preserved.
    """
    original, formal, _ = _date_parts(date)
    years = set(EXACT_YEAR.findall(original))
    if RANGE.search(original) or len(years) > 1 or (formal and "/" in formal):
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
    return (
        "hash-"
        + hashlib.sha256(
            json.dumps(relationship, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
    )


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


def _cached_response(checkpoint, url):
    """Use the original exporter's request cache, without network calls."""
    return checkpoint.get_response("0::" + url) or {}


def _first_value(items):
    for item in items or []:
        if isinstance(item, dict) and item.get("value") is not None:
            return _text(item["value"])
        if isinstance(item, str) and item:
            return item
    return ""


def _note_key(owner_type, owner_id, note, ordinal):
    identifier = _text(note.get("id"))
    if identifier:
        return f"{owner_type}:{owner_id}:{identifier}"
    digest = hashlib.sha256(
        json.dumps(note, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return f"{owner_type}:{owner_id}:hash-{digest}:{ordinal}"


def _read_notes(container, owner_type, owner_id, owner_name, rows):
    for number, note in enumerate(container.get("notes") or [], 1):
        if not isinstance(note, dict):
            continue
        rows.append(
            {
                "note_key": _note_key(owner_type, owner_id, note, number),
                "owner_type": owner_type,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "subject": _text(note.get("subject")),
                "text": _text(note.get("text")),
            }
        )


def _source_ref_id(value):
    value = _text(value)
    return value.rsplit("#", 1)[-1] if "#" in value else value


def _owner_sources(
    checkpoint,
    owner_type,
    owner_id,
    owner_name,
    source_refs,
    source_endpoint,
    rows,
    notes,
):
    """Include every owner reference, even when its source details are missing."""
    response = _cached_response(checkpoint, source_endpoint)
    descriptions = {
        _source_ref_id(source.get("id")): source
        for source in response.get("sourceDescriptions") or []
        if isinstance(source, dict)
    }

    refs = {}
    for item in source_refs or []:
        if not isinstance(item, dict):
            continue
        ref = _text(item.get("descriptionId") or item.get("description"))
        if ref:
            refs.setdefault(_source_ref_id(ref), []).append(item)
    for group in ("persons", "relationships"):
        for entity in response.get(group) or []:
            for item in entity.get("sources") or []:
                if not isinstance(item, dict):
                    continue
                ref = _text(item.get("descriptionId") or item.get("description"))
                if ref:
                    refs.setdefault(_source_ref_id(ref), []).append(item)

    for source_id in descriptions:
        refs.setdefault(source_id, [])
    for source_id, references in refs.items():
        description = descriptions.get(source_id) or {}
        attribution = next(
            (
                _text(ref.get("attribution", {}).get("changeMessage"))
                for ref in references
                if isinstance(ref.get("attribution"), dict)
                and ref["attribution"].get("changeMessage")
            ),
            "",
        )
        rows.append(
            {
                "source_key": f"{owner_type}:{owner_id}:{source_id}",
                "owner_type": owner_type,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "source_id": source_id,
                "title": _first_value(description.get("titles")),
                "citation": _first_value(description.get("citations")),
                "url": _text(description.get("about")),
                "description": _first_value(description.get("descriptions")),
                "source_notes": " | ".join(
                    _text(item.get("text"))
                    for item in description.get("notes") or []
                    if isinstance(item, dict) and item.get("text")
                ),
                "attribution_note": attribution,
                "details_available": bool(description),
            }
        )
        if description:
            _read_notes(
                description,
                "source",
                source_id,
                _first_value(description.get("titles")),
                notes,
            )


def _memory_rows(checkpoint, person_id, person_name, person, rows):
    for evidence in person.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        evidence_id = _text(evidence.get("id"))
        memory_id = evidence_id.partition("-")[0]
        if not memory_id:
            continue
        response = _cached_response(
            checkpoint, f"/platform/memories/memories/{memory_id}"
        )
        descriptions = response.get("sourceDescriptions") or [None]
        for index, description in enumerate(descriptions):
            description = description if isinstance(description, dict) else {}
            rows.append(
                {
                    "memory_key": f"{person_id}:{evidence_id}:{index}",
                    "person_id": person_id,
                    "person_name": person_name,
                    "evidence_id": evidence_id,
                    "memory_id": memory_id,
                    "media_type": _text(description.get("mediaType")),
                    "title": _first_value(description.get("titles")),
                    "description": _first_value(description.get("descriptions")),
                    "url": _text(description.get("about")),
                    "details_available": bool(description),
                }
            )


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
            row["event_key"]: {key: row.get(key, "") for key in REVIEW_COLUMNS}
            for row in reader
            if row.get("event_key")
        }


def collect_reports(checkpoint, selected_ids=None):
    """Collect cached genealogy for the selected people only."""
    people = []
    events = []
    relations = {}
    names = {}
    sources = []
    notes = []
    memories = []

    for person_id in (
        checkpoint.completed_ids() if selected_ids is None else selected_ids
    ):
        document = checkpoint.get_response(PERSON_URL + person_id) or {}
        person = next(
            (p for p in document.get("persons") or [] if p.get("id") == person_id),
            None,
        )
        if not person:
            continue
        name = _name(person)
        names[person_id] = name
        facts = person.get("facts") or []
        gender = (person.get("gender") or {}).get("type", "").rsplit("/", 1)[-1]
        people.append(
            {
                "person_id": person_id,
                "name": name,
                "gender": gender,
                "living": person.get("living", ""),
                "events": len(facts),
                "familysearch_url": f"https://www.familysearch.org/tree/person/details/{person_id}",
            }
        )
        person_notes = _cached_response(
            checkpoint, f"/platform/tree/persons/{person_id}/notes"
        )
        for entity in person_notes.get("persons") or []:
            _read_notes(entity, "person", person_id, name, notes)
        _owner_sources(
            checkpoint,
            "person",
            person_id,
            name,
            person.get("sources"),
            f"/platform/tree/persons/{person_id}/sources",
            sources,
            notes,
        )
        _memory_rows(checkpoint, person_id, name, person, memories)
        seen = {}
        for fact in facts:
            digest = json.dumps(fact, sort_keys=True, ensure_ascii=False)
            seen[digest] = seen.get(digest, 0) + 1
            date = fact.get("date")
            year, date_status = year_for_sorting(date)
            original, formal, _ = _date_parts(date)
            events.append(
                {
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
                }
            )
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
    events.sort(
        key=lambda item: (
            item["year"] == "",
            int(item["year"]) if item["year"] else 0,
            item["person_name"].casefold(),
            item["event_type"],
            item["event_key"],
        )
    )

    # A bounded scope should not list unselected people as if they had
    # been downloaded. Only keep relationships whose known endpoints are
    # all within the requested person set.
    if selected_ids is not None:
        chosen = set(selected_ids)
        relations = {
            key: relationship
            for key, relationship in relations.items()
            if all(
                identifier in chosen
                for identifier in (
                    _member(relationship, field)
                    for field in ("person1", "person2", "parent1", "parent2", "child")
                )
                if identifier
            )
        }

    relationships = []
    for key, relationship in relations.items():
        first = _member(relationship, "person1")
        second = _member(relationship, "person2")
        date = next(
            (
                fact.get("date")
                for fact in relationship.get("facts") or []
                if isinstance(fact, dict) and fact.get("date")
            ),
            None,
        )
        place_fact = next(
            (
                fact
                for fact in relationship.get("facts") or []
                if isinstance(fact, dict) and fact.get("place")
            ),
            {},
        )
        original, formal, _ = _date_parts(date)
        relationships.append(
            {
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
            }
        )
    relationships.sort(
        key=lambda item: (
            item["relationship_type"],
            item["relationship_key"],
        )
    )
    for key, relation in relations.items():
        if relation.get("type") != "http://gedcomx.org/Couple":
            continue
        couple_details = _cached_response(
            checkpoint, f"/platform/tree/couple-relationships/{key}"
        )
        source_refs = relation.get("sources") or []
        source_owner_name = " / ".join(
            names.get(_member(relation, part), _member(relation, part))
            for part in ("person1", "person2")
            if _member(relation, part)
        )
        relationship_data = next(
            (
                item
                for item in couple_details.get("relationships") or []
                if item.get("id") == key
            ),
            {},
        )
        if relationship_data.get("sources"):
            source_refs = relationship_data["sources"]
        _owner_sources(
            checkpoint,
            "couple",
            key,
            source_owner_name,
            source_refs,
            f"/platform/tree/couple-relationships/{key}/sources",
            sources,
            notes,
        )
        note_response = _cached_response(
            checkpoint, f"/platform/tree/couple-relationships/{key}/notes"
        )
        for entity in note_response.get("relationships") or []:
            _read_notes(entity, "couple", key, source_owner_name, notes)

    sources.sort(
        key=lambda row: (
            row["owner_type"],
            row["owner_name"].casefold(),
            row["source_id"],
        )
    )
    notes.sort(
        key=lambda row: (
            row["owner_type"],
            row["owner_name"].casefold(),
            row["note_key"],
        )
    )
    memories.sort(
        key=lambda row: (
            row["person_name"].casefold(),
            row["memory_key"],
        )
    )

    return {
        "people": people,
        "events": events,
        "relationships": relationships,
        "sources": sources,
        "notes": notes,
        "memories": memories,
    }


def export_reports(checkpoint, output, selected_ids=None):
    """Optional CSV export for the requested people and a review checklist."""
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    data = collect_reports(checkpoint, selected_ids=selected_ids)
    people = data["people"]
    events = data["events"]
    relationships = data["relationships"]
    sources = data["sources"]
    notes = data["notes"]
    memories = data["memories"]
    paths = {
        "people": output.with_suffix(".people.csv"),
        "events": output.with_suffix(".events.csv"),
        "relationships": output.with_suffix(".relationships.csv"),
        "sources": output.with_suffix(".sources.csv"),
        "notes": output.with_suffix(".notes.csv"),
        "memories": output.with_suffix(".memories.csv"),
        "review": output.with_suffix(".review.csv"),
    }
    # Validate the manual review file before touching generated reports.
    reviews = _review_rows(paths["review"])
    for event in events:
        reviews.setdefault(
            event["event_key"],
            {
                "event_key": event["event_key"],
                "verified": "",
                "notes": "",
            },
        )

    _write_csv(paths["people"], PEOPLE_COLUMNS, people)
    _write_csv(paths["events"], EVENT_COLUMNS, events)
    _write_csv(paths["relationships"], RELATIONSHIP_COLUMNS, relationships)
    _write_csv(paths["sources"], SOURCE_COLUMNS, sources)
    _write_csv(paths["notes"], NOTE_COLUMNS, notes)
    _write_csv(paths["memories"], MEMORY_COLUMNS, memories)
    _write_csv(
        paths["review"],
        REVIEW_COLUMNS,
        [reviews[key] for key in sorted(reviews)],
    )
    return {
        "paths": paths,
        "people": len(people),
        "events": len(events),
        "relationships": len(relationships),
        "sources": len(sources),
        "notes": len(notes),
        "memories": len(memories),
        "review_items": len(reviews),
    }
