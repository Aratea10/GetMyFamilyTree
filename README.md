# GetMyFamilyTree

<div align="center">

[![Python](https://img.shields.io/badge/python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

</div>

Explore your extended FamilySearch family tree in every direction and export it to GEDCOM.

**Independent, unofficial community project.** This tool is not affiliated with or endorsed by FamilySearch.

## What it does

Starting from a FamilySearch person ID, GetMyFamilyTree follows **all accessible parent–child and couple relationships**. Each discovered person is explored in turn, including spouses, in-laws, collateral branches, and ancestors or descendants of those relatives. There is **no generation limit**.

It retrieves the person's available genealogy details through the open-source `getmyancestors` exporter: names, dates, places, life events, relationships, source citations, notes, and available memory references. It also keeps the retrieved API responses in a private local SQLite database, which is used for resuming interrupted downloads and retaining data that a GEDCOM cannot represent. **Media images are not downloaded as files:** GEDCOM includes available references/URLs, not the binary originals.

FamilySearch may omit records, restrict access or throttle requests. This tool cannot guarantee exhaustive access to the worldwide FamilySearch tree or to every detail of every person.

## Installation

GetMyFamilyTree is designed for **Windows, macOS, and Linux**. It is a Python
command-line tool, not a graphical desktop application. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) first; it can
download Python 3.12 and run the tool in an isolated environment without
installing Python packages globally.

### macOS

Open Terminal and install uv using the official installer:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Alternatively, if Homebrew is already installed: `brew install uv`.

### Windows

Open **PowerShell** or **Windows Terminal**. On systems with Windows Package
Manager (WinGet), install uv using:

```powershell
winget install --id=astral-sh.uv -e
```

Alternatively, see the [official uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
for the PowerShell standalone installer.

### Linux

Open a terminal and install uv using:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On any platform, restart the terminal if necessary, then check that uv is
available:

```text
uvx --version
```

## Download your connected family tree

These commands run the tool directly from its GitHub repository and request
Python 3.12 through uv. They do not require cloning this repository.

**macOS and Linux** (save to Downloads):

```sh
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree -i XXXX-XXX -o ~/Downloads/GetMyFamilyTree
```

**Windows PowerShell** (save to Downloads):

```powershell
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree -i XXXX-XXX -o "$HOME\Downloads\GetMyFamilyTree"
```

Replace `XXXX-XXX` with your own FamilySearch person ID. The `-o`
parameter now accepts a **folder path** (recommended), automatically creating
`GetMyFamilyTree.ged`, `GetMyFamilyTree.sqlite`, and
`GetMyFamilyTree.md` inside it. You can still pass an explicit `.ged`
filename for compatibility with older commands, but those files will then
appear beside that filename rather than in a dedicated folder.

To display all options without starting a download:

```text
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree --help
```

**Platform support is still being validated.** The commands are intended
to work on all three systems, but successful end-to-end FamilySearch downloads
have not yet been verified on each platform.

### Remember your FamilySearch login (optional)

By default, GetMyFamilyTree prompts for your FamilySearch username and password
whenever no saved login exists. If this is your own computer, add
`--remember-login` **once** to store the login in the operating system's
credential vault (macOS Keychain, Windows Credential Manager, or a supported
Linux secret store).

For example, from a cloned project with uv:

```sh
uv run getmyfamilytree -i XXXX-XXX --remember-login --max-people 100 -o ~/Downloads/GetMyFamilyTree
```

After a successful login, later runs automatically reuse those saved credentials
without asking again. Run the same command without `--remember-login` to
continue exploring. On Windows, replace the output path with
`"$HOME\\Downloads\\GetMyFamilyTree.ged"`.

To remove the saved login at any time:

```sh
uv run getmyfamilytree --forget-login
```

The saved login stays in the **system keyring**, not in the GEDCOM, SQLite
checkpoint or repository. Nothing is saved without `--remember-login`.
Your operating system may request permission to access the keyring. If no
secure keyring is available, the program will continue to ask for credentials.

**Never put your password in a command or commit it to Git.**

For safety, the first run pauses after **1,000 people**. That number is not a generation limit. Run the same command with `--max-people 5000` to explore more. If you deliberately want no person limit, pass `--max-people 0`; a large connected tree could run for a very long time and consume significant disk space and requests.

### Language / Idioma

The command-line interface supports **English** and **Spanish**. By default,
`--language auto` uses the operating system's language when it is supported;
other languages fall back to English.

To choose Spanish explicitly:

```sh
uv run getmyfamilytree -i XXXX-XXX --language es --max-people 200 -o ~/Downloads/GetMyFamilyTree
```

To choose English, use `--language en`. The shorter option
`--lang es` also works. Help messages follow the selected language:

```sh
uv run getmyfamilytree --language es --help
```

**The language option changes the terminal interface and headings in the
Markdown review.** It does not translate the GEDCOM or alter names, dates,
places, notes and other original FamilySearch data. GEDCOM record identifiers
and date codes follow the GEDCOM standard regardless of the interface language.

### Live progress

The terminal now reports progress **for every person** by default. As
FamilySearch records are explored, it shows the person's name (when available),
FamilySearch ID, the number of newly discovered relatives and how many are
still queued. For example:

```text
Starting exploration: 20 already explored, 78 queued, limit 100.
Exploring 21/100: XXXX-XXX...
Found: Example Person (XXXX-XXX) | +3 new relatives | 80 queued.
```

To reduce terminal output for large downloads, use
`--progress-every 25` (one update per 25 people) or
`--progress-every 0` to disable per-person progress. The person count
tracks successful explorations, not the total size of the worldwide family
tree; newly found connections can increase the number still queued.

The command creates a SQLite checkpoint beside the GEDCOM (same filename with `.sqlite` suffix). Running with the same `-o` path resumes using that checkpoint rather than starting from scratch. Use Ctrl+C to request an orderly pause and a GEDCOM snapshot.

The downloaded GEDCOM and SQLite checkpoint can contain **private family information**. Keep both locally and out of public Git repositories.

## Review one person at a time

By default the program creates one **readable Markdown document** with an
overview of every person and their available **events, relationships, sources,
notes, and memories**. It opens as plain text in any code editor and as a
nicely formatted document in a Markdown preview.

With a recommended folder destination, the export looks like this:

```text
Downloads/
└── GetMyFamilyTree/
    ├── GetMyFamilyTree.ged
    ├── GetMyFamilyTree.sqlite
    └── GetMyFamilyTree.md
```

The Markdown file contains a **chronological index** for finding events by
year. Below it, individuals are ordered by their birth year (when known).
Each individual has **one section** containing their own events (sorted by
year), parents/children/partners, source citations and links, notes, and
references to memories. Relationship sources appear next to the relevant
partner relationship. Unknown or uncertain dates are kept as originally
received, never silently assigned a precise year.

You can check items in the person's **My review / Mi revisión** section and
write additional comments there. That section is preserved when the document
is regenerated; newly found events receive new checklist entries. **Only
edit notes inside the marked personal review section**: the remaining
generated text will be refreshed from the local database.

### Regenerate the document without downloading anything

After a previous download has created the SQLite file:

```sh
uv run getmyfamilytree --report-only --language es -o ~/Downloads/GetMyFamilyTree
```

On Windows PowerShell, use
`-o "$HOME\Downloads\GetMyFamilyTree"`. Report-only mode needs neither
FamilySearch credentials nor a person ID; it does not alter the GEDCOM.

### Optional: export CSV tables too

If you need to sort or filter the underlying tabular information in Numbers,
Excel or LibreOffice Calc, use `--csv`:

```sh
uv run getmyfamilytree --report-only --csv --language es -o ~/Downloads/GetMyFamilyTree
```

This additionally generates the seven CSV tables: people, events,
relationships, sources, notes, memories, and a separate persistent
review checklist. **They are no longer generated by default.** CSV tables
are optional; the GEDCOM, SQLite checkpoint and person-by-person Markdown
are enough for normal use.

These reports can contain private details about living relatives. Keep the
entire output folder private and do not commit it to public repositories.
The program can only report FamilySearch details that were actually
accessible and cached; a source attached to a person does not automatically
prove every fact in that person's record.

## Local research backend for the future graphical interface

The SQLite research plan now has a **workspace service and a local JSON API**.
The API reuses the already downloaded FamilySearch cache; it does **not** log
in to FamilySearch or make network requests. Manual priorities, categories,
branches, status changes and ordering remain in the research tables.

After downloading a tree, start the local service from a cloned repository:

```sh
uv run --offline getmyfamilytree research --database ~/Downloads/GetMyFamilyTree/GetMyFamilyTree.sqlite serve
```

The server listens **only on `127.0.0.1`**, chooses an available port by
default and prints its base URL and a fresh bearer token. The token must be
sent as `Authorization: Bearer <token>` with every request; keep it private.
Press Ctrl+C to stop. To request a specific port, add `--port 8765`.

The JSON API uses `/api/v1` as its prefix:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/summary` | Research progress and board size |
| GET | `/tasks` | Search and paginate with `search`, `status`, `category`, `branch`, `order`, `limit` and `offset` |
| GET | `/people/{id}` | Individual sheet, events, relatives, notes, sources and memories |
| PATCH | `/people/{id}/task` | Change one of `status`, `priority`, `category`, `branches` or `before_id` |
| PUT | `/people/{id}/note` | Set or clear a separate private workspace note |
| GET/POST | `/cards` | Browse or create research-board cards (`?person_id=XXXX-XXX` to select one person's corcho) |
| GET/PATCH/DELETE | `/cards/{id}` | Inspect, move, edit, link or remove one card |
| GET/POST | `/assets` | List or import an image or PDF |
| GET/DELETE | `/assets/{id}` | Inspect metadata or remove an unused attachment |
| GET | `/assets/{id}/content` | Retrieve imported content for previews |

Each person has an individual board view; the app filters cards by person,
rather than displaying one enormous family-wide board. The same attachment
can be referenced from separate cards with independent layouts.

Card data includes `kind` (`document`, `photo`, `note` or `person`),
`title`, `description`, `url` or `asset_id`, coordinates `x`/`y`,
`width`, `rotation`, and stacking order `z`. Cards can link to several
already downloaded people. The future interface will animate opening and
zooming documents; the backend stores the required references and positions.

Import attachments with `POST /assets` and JSON fields `name` and
`content_base64`. Up to 20 MiB per file is supported, for PDF, JPEG, PNG,
WebP, GIF and TIFF. Imported files are held in a private `*.assets/` folder
beside the SQLite checkpoint, with generated filenames. The API **cannot**
read arbitrary file paths on the computer, and does not permit cross-origin
browser access. Imported files are not put into the GEDCOM and are not
uploaded to FamilySearch.

The `workspace_person_notes` table is deliberately separate from existing
Markdown `Mi revisión / My review` checklists, so regenerating the Markdown
does not discard workspace notes. Existing CSV, GEDCOM and report-only
commands remain unchanged.

**Privacy:** The SQLite database and assets folder may contain personal
information about living relatives. Never commit them to a public repository.
The local service is intended for the eventual desktop application, not for
hosting on the public internet.

## Limitations

- The GEDCOM export currently uses the original `getmyancestors` writer. It may not retain every API field or download source-document/media image bytes.
- The SQLite cache contains successful person, source, memory, couple and note endpoint responses actually accessed during download; it is not a complete mirror of FamilySearch.
- Some people or relationships may be inaccessible, unavailable, or returned without complete data.
- The initial version is **experimental**. Verify the results before relying on them as your only genealogical archive.

## Acknowledgements and license

GetMyFamilyTree is an independently developed genealogy research project that uses  [Linekio/getmyancestors](https://github.com/Linekio/getmyancestors), maintained by Giulio Genovese, as a dependency for FamilySearch integration and GEDCOM export.

The getmyancestors project is licensed under the GNU General Public License, version 3 or later (GPL-3.0-or-later).

GetMyFamilyTree is also distributed under GPL-3.0-or-later. See [LICENSE](https://github.com/Aratea10/GetMyFamilyTree?tab=GPL-3.0-1-ov-file) for the full license text.

Copyright (C) 2026 Aratea for the original GetMyFamilyTree code. Third-party components retain their respective copyrights and licenses.
