"""One readable, person-centred Markdown review of cached FamilySearch data.

All relationship, source, note and memory details stay with their person.
The review checklist enclosed by explicit markers survives regeneration.
"""
from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from getmyfamilytree.reports import collect_reports

PERSON_ID_PATTERN = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{3}$")
REVIEW_BLOCK = re.compile(
    r"<!-- review:start ([A-Z0-9-]+) -->\n(.*?)\n<!-- review:end \1 -->",
    re.DOTALL,
)
EVENT_LABELS = {
    "Birth": ("Nacimiento", "Birth"),
    "Death": ("Defunción", "Death"),
    "Burial": ("Entierro", "Burial"),
    "Christening": ("Bautismo", "Christening"),
    "Baptism": ("Bautismo", "Baptism"),
    "Marriage": ("Matrimonio", "Marriage"),
    "Residence": ("Residencia", "Residence"),
    "Occupation": ("Profesión", "Occupation"),
    "Immigration": ("Inmigración", "Immigration"),
    "Emigration": ("Emigración", "Emigration"),
    "Confirmation": ("Confirmación", "Confirmation"),
    "Religion": ("Religión", "Religion"),
    "Census": ("Censo", "Census"),
    "LifeSketch": ("Semblanza", "Life sketch"),
}


def _md(value):
    """Escape external data so it is displayed as text, not Markdown syntax."""
    text = " ".join(str(value or "").split())
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", text)


def _link(url, label):
    raw = str(url or "").strip()
    split = urlsplit(raw)
    if split.scheme not in ("https", "http") or not split.netloc:
        return _md(label) + (f" — {_md(raw)}" if raw else "")
    if any(ch in raw for ch in "\n\r<>"):
        return _md(label)
    return f"[{_md(label)}]({raw.replace('(', '%28').replace(')', '%29')})"


def _label(kind, language):
    if kind in EVENT_LABELS:
        return EVENT_LABELS[kind][0 if language == "es" else 1]
    return kind


def _saved_reviews(path):
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    return {match.group(1): match.group(2) for match in REVIEW_BLOCK.finditer(content)}


def _event_checklist(events, existing, language):
    """Preserve checked items and comments; add newly discovered event IDs."""
    found = set(re.findall(r"\(`([^`]+)`\)", existing))
    items = []
    for event in events:
        if event["event_key"] in found:
            continue
        label = _label(event["event_type"], language)
        year = event["year"] or ("Sin año" if language == "es" else "No year")
        items.append(
            f"- [ ] {_md(year)} · {_md(label)} "
            f"(`{_md(event['event_key'])}`)"
        )
    previous = existing.strip("\n")
    return "\n".join(part for part in (previous, *items) if part)


def write_person_report(checkpoint, output, language="en"):
    """Write a single Markdown report; return its path and record counts."""
    output = Path(output).expanduser()
    report = output.with_suffix(".md")
    report.parent.mkdir(parents=True, exist_ok=True)
    data = collect_reports(checkpoint)
    saved = _saved_reviews(report)
    es = language == "es"

    people = data["people"]
    events = data["events"]
    relationships = data["relationships"]
    sources = data["sources"]
    notes = data["notes"]
    memories = data["memories"]

    event_by_person = defaultdict(list)
    source_by_person = defaultdict(list)
    note_by_person = defaultdict(list)
    memory_by_person = defaultdict(list)
    for event in events:
        event_by_person[event["person_id"]].append(event)
    for source in sources:
        if source["owner_type"] == "person":
            source_by_person[source["owner_id"]].append(source)
    for note in notes:
        if note["owner_type"] == "person":
            note_by_person[note["owner_id"]].append(note)
    for memory in memories:
        memory_by_person[memory["person_id"]].append(memory)

    people_by_id = {person["person_id"]: person for person in people}
    def name_for(identifier):
        return people_by_id.get(identifier, {}).get("name") or identifier

    def relative(identifier):
        return f"{_md(name_for(identifier))} (`{_md(identifier)}`)"

    def birth_year(person):
        years = [
            int(event["year"])
            for event in event_by_person[person["person_id"]]
            if event["event_type"] == "Birth" and event["year"]
        ]
        return min(years) if years else 99999

    people = sorted(
        people, key=lambda p: (birth_year(p), p["name"].casefold(), p["person_id"])
    )
    lines = [
        "# GetMyFamilyTree — " + ("Revisión genealógica" if es else "Genealogy review"),
        "",
        (
            "> Informe generado a partir del progreso guardado. No modifica el GEDCOM. "
            "Los datos y las fuentes no equivalen a una verificación documental."
            if es else
            "> Generated from cached research data. The GEDCOM is unchanged. "
            "The records and sources are not themselves independent verification."
        ),
        "",
        f"**{'Personas' if es else 'People'}:** {len(people)} · "
        f"**{'Acontecimientos' if es else 'Events'}:** {len(events)} · "
        f"**{'Fuentes' if es else 'Sources'}:** {len(sources)} · "
        f"**{'Notas' if es else 'Notes'}:** {len(notes)} · "
        f"**{'Recuerdos' if es else 'Memories'}:** {len(memories)}",
        "",
        "## " + ("Índice cronológico" if es else "Chronological index"),
        "",
        (
            "Los años dudosos son solo referencias para ordenar. "
            "La fecha original se mantiene en cada ficha."
            if es else
            "Uncertain years are only sorting references. "
            "Each person's original dates are retained."
        ),
        "",
        "| " + ("Año | Acontecimiento | Persona | Fecha original |" if es
                else "Year | Event | Person | Original date |"),
        "| --- | --- | --- | --- |",
    ]

    for event in events:
        label = _label(event["event_type"], language)
        fid = event["person_id"]
        year = event["year"] or "—"
        lines.append(
            f"| {_md(year)} | {_md(label)} | "
            f"[{_md(event['person_name'])}](#persona-{fid.lower()}) | "
            f"{_md(event['date_original'])} |"
        )

    lines.extend([
        "",
        "## " + ("Fichas por persona" if es else "Person records"),
        "",
        (
            "Las fichas se ordenan por el año de nacimiento disponible. "
            "Sin año de nacimiento, se muestran al final. "
            "Escribe tus comprobaciones únicamente dentro de cada bloque "
            "«Mi revisión»; ese contenido se conserva al regenerar el archivo."
            if es else
            "People are ordered by their available birth year, with undated "
            "people last. Write your own notes only inside each 'My review' "
            "block: those sections are preserved when regenerating the file."
        ),
        "",
    ])

    for person in people:
        fid = person["person_id"]
        events_here = event_by_person[fid]
        lines.extend([
            f'<a id="persona-{fid.lower()}"></a>',
            "",
            f"### {_md(person['name'])} (`{_md(fid)}`)",
            "",
            "**FamilySearch:** " + _link(person["familysearch_url"],
                                        person["familysearch_url"]),
            "",
        ])
        if person["gender"]:
            lines.append(
                f"**{'Sexo' if es else 'Sex'}:** {_md(person['gender'])}"
            )
        if person["living"] != "":
            lines.append(
                f"**{'Vive' if es else 'Living'}:** {_md(person['living'])}"
            )

        lines.extend(["", "#### " + ("Parentescos" if es else "Relationships"), ""])
        rel_lines = []
        for relation in relationships:
            kind = relation["relationship_type"]
            first, second = relation["person1_id"], relation["person2_id"]
            parent1, parent2 = relation["parent1_id"], relation["parent2_id"]
            child = relation["child_id"]
            if kind == "Couple" and fid in (first, second):
                other = second if fid == first else first
                rel_lines.append(
                    "- " + ("Pareja" if es else "Partner")
                    + ": " + relative(other) + f" — `{_md(relation['relationship_key'])}`"
                )
                for source in sources:
                    if (source["owner_type"] == "couple"
                            and source["owner_id"] == relation["relationship_key"]):
                        rel_lines.append(
                            "  - " + ("Fuente de la pareja" if es else "Couple source")
                            + ": " + _md(source["title"] or source["source_id"])
                            + (f" — {_md(source['citation'])}" if source["citation"] else "")
                            + (f" — {_link(source['url'], 'Abrir' if es else 'Open')}" if source["url"] else "")
                        )
                for note in notes:
                    if (note["owner_type"] == "couple"
                            and note["owner_id"] == relation["relationship_key"]):
                        rel_lines.append(
                            "  - " + ("Nota de la pareja" if es else "Couple note")
                            + ": " + _md(note["text"])
                        )
            elif child and kind == "ChildAndParents":
                if fid == child:
                    for number, parent in enumerate((parent1, parent2), 1):
                        if parent:
                            rel_lines.append(
                                f"- {'Progenitor' if es else 'Parent'} {number}: {relative(parent)}"
                            )
                elif fid in (parent1, parent2):
                    rel_lines.append(
                        "- " + ("Hijo/a" if es else "Child") + ": " + relative(child)
                    )
            elif kind == "ParentChild":
                if fid == first and second:
                    rel_lines.append(
                        "- " + ("Hijo/a" if es else "Child") + ": " + relative(second)
                    )
                elif fid == second and first:
                    rel_lines.append(
                        "- " + ("Progenitor" if es else "Parent") + ": " + relative(first)
                    )
        lines.extend(sorted(set(rel_lines)) or [
            "- " + ("Sin vínculos recuperados." if es else "No cached relationships.")
        ])

        lines.extend([
            "", "#### " + ("Acontecimientos" if es else "Events"), "",
        ])
        for event in events_here:
            label = _label(event["event_type"], language)
            year = event["year"] or ("Sin año" if es else "No year")
            parts = [
                _md(item) for item in (
                    event["date_original"], event["place"], event["value"]
                ) if item
            ]
            lines.append(
                f"- **{_md(year)} · {_md(label)}**"
                + (": " + " · ".join(parts) if parts else "")
                + f" (`{_md(event['event_key'])}`)"
            )
            if event["date_status"] not in ("stated_year", "formal_year"):
                lines.append(
                    "  - " + ("Precisión de la fecha" if es else "Date certainty")
                    + ": `" + _md(event["date_status"]) + "`"
                )
            if event["date_formal"]:
                lines.append(
                    "  - " + ("Fecha formal" if es else "Formal date")
                    + ": `" + _md(event["date_formal"]) + "`"
                )
            if event["source_references"]:
                lines.append(
                    "  - " + ("Referencias de fuentes del hecho" if es
                             else "Event source references")
                    + ": " + _md(event["source_references"])
                )
        if not events_here:
            lines.append("- " + ("Sin acontecimientos recuperados." if es
                                 else "No cached events."))

        lines.extend(["", "#### " + ("Fuentes" if es else "Sources"), ""])
        for source in source_by_person[fid]:
            title = source["title"] or source["source_id"]
            lines.append(f"- **{_md(title)}** (`{_md(source['source_id'])}`)")
            if source["citation"]:
                lines.append("  - " + ("Cita" if es else "Citation")
                             + ": " + _md(source["citation"]))
            if source["description"]:
                lines.append("  - " + ("Descripción" if es else "Description")
                             + ": " + _md(source["description"]))
            if source["url"]:
                lines.append("  - " + _link(source["url"],
                                          "Abrir documento" if es else "Open document"))
            if source["source_notes"]:
                lines.append("  - " + ("Notas de fuente" if es else "Source notes")
                             + ": " + _md(source["source_notes"]))
            if source["details_available"] is False:
                lines.append("  - " + ("Detalles no recuperados." if es
                                     else "Details not retrieved."))
        if not source_by_person[fid]:
            lines.append("- " + ("No hay fuentes recuperadas." if es
                                 else "No cached sources."))

        lines.extend(["", "#### " + ("Notas" if es else "Notes"), ""])
        for note in note_by_person[fid]:
            lines.append(
                "- **" + _md(note["subject"] or ("Nota" if es else "Note"))
                + "**: " + _md(note["text"])
            )
        if not note_by_person[fid]:
            lines.append("- " + ("No hay notas recuperadas." if es
                                 else "No cached notes."))

        lines.extend(["", "#### " + ("Recuerdos" if es else "Memories"), ""])
        for memory in memory_by_person[fid]:
            line = "- **" + _md(memory["title"] or memory["memory_id"]) + "**"
            if memory["media_type"]:
                line += " · " + _md(memory["media_type"])
            if memory["url"]:
                line += " — " + _link(memory["url"], "Abrir" if es else "Open")
            lines.append(line)
            if memory["description"]:
                lines.append("  - " + _md(memory["description"]))
            if memory["details_available"] is False:
                lines.append("  - " + ("Detalles no recuperados." if es
                                     else "Details not retrieved."))
        if not memory_by_person[fid]:
            lines.append("- " + ("No hay recuerdos recuperados." if es
                                 else "No cached memories."))

        lines.extend([
            "", "#### " + ("Mi revisión" if es else "My review"), "",
            f"<!-- review:start {fid} -->",
            _event_checklist(events_here, saved.get(fid, ""), language),
            f"<!-- review:end {fid} -->",
            "", "---", "",
        ])

    temporary = report.with_name(report.name + ".tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(report)
    return {
        "path": report,
        **{key: len(data[key]) for key in (
            "people", "events", "relationships", "sources", "notes", "memories"
        )},
    }
