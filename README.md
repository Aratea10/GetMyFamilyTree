# GetMyFamilyTree

Explore your extended FamilySearch family tree in every direction and export it to GEDCOM.

**Independent, unofficial community project.** This tool is not affiliated with or endorsed by FamilySearch.

## What it does

Starting from a FamilySearch person ID, GetMyFamilyTree follows **all accessible parent–child and couple relationships**. Each discovered person is explored in turn, including spouses, in-laws, collateral branches, and ancestors or descendants of those relatives. There is **no generation limit**.

It retrieves the person's available genealogy details through the open-source `getmyancestors` exporter: names, dates, places, life events, relationships, source citations, notes, and available memory references. It also keeps the retrieved API responses in a private local SQLite database, which is used for resuming interrupted downloads and retaining data that a GEDCOM cannot represent. **Media images are not downloaded as files:** GEDCOM includes available references/URLs, not the binary originals.

FamilySearch may omit records, restrict access or throttle requests. This tool cannot guarantee exhaustive access to the worldwide FamilySearch tree or to every detail of every person.

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). No global Python packages needed.

```sh
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree --help
```

## Download your connected family tree

```sh
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree -i LVXP-TH5 -o ~/Downloads/GetMyFamilyTree.ged
```

Your FamilySearch credentials will be requested interactively. **Never put the password in the command or commit it to Git.**

For safety, the first run pauses after **1,000 people**. That number is not a generation limit. Run the same command with `--max-people 5000` to explore more. If you deliberately want no person limit, pass `--max-people 0`; a large connected tree could run for a very long time and consume significant disk space and requests.

The command creates an SQLite checkpoint beside the GEDCOM (same filename with `.sqlite` suffix). Running with the same `-o` path resumes using that checkpoint, rather than starting from scratch. Use Ctrl+C to request an orderly pause and a GEDCOM snapshot.

The downloaded GEDCOM and SQLite checkpoint can contain **private family information**. Keep both locally and out of public Git repositories.

## Limitations

- The GEDCOM export currently uses the original `getmyancestors` writer. It may not retain every API field or download source-document/media image bytes.
- The SQLite cache contains successful person, source, memory, couple and note endpoint responses actually accessed during download; it is not a complete mirror of FamilySearch.
- Some people or relationships may be inaccessible, unavailable, or returned without complete data.
- The initial version is **experimental**. Verify the results before relying on them as your only genealogical archive.

## Acknowledgements and license

GetMyFamilyTree builds on [Linekio/getmyancestors](https://github.com/Linekio/getmyancestors), maintained by Giulio Genovese. The original project is licensed under GNU GPL v3 or later, and is installed as a dependency rather than copied into this repository. Thanks to its contributors for its FamilySearch integration and GEDCOM export capabilities.

This project's code is also licensed under GPL-3.0-or-later; see LICENSE.
