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
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree -i XXXX-XXX -o ~/Downloads/GetMyFamilyTree.ged
```

**Windows PowerShell** (save to Downloads):

```powershell
uvx --python 3.12 --from "git+https://github.com/Aratea10/GetMyFamilyTree.git" getmyfamilytree -i XXXX-XXX -o "$HOME\Downloads\GetMyFamilyTree.ged"
```

Replace `XXXX-XXX` with your own FamilySearch person ID. On all platforms
you can instead use `-o GetMyFamilyTree.ged` to save to the current directory.

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
uv run getmyfamilytree -i XXXX-XXX --remember-login --max-people 100 -o ~/Downloads/GetMyFamilyTree.ged
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
uv run getmyfamilytree -i XXXX-XXX --language es --max-people 200 -o ~/Downloads/GetMyFamilyTree.ged
```

To choose English, use `--language en`. The shorter option
`--lang es` also works. Help messages follow the selected language:

```sh
uv run getmyfamilytree --language es --help
```

**The language option changes only the terminal interface.** It does not
translate the GEDCOM or alter names, dates, places, notes and other original
FamilySearch data. GEDCOM record identifiers and date codes follow the
GEDCOM standard regardless of the interface language.

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
