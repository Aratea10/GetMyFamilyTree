"""Run existing genealogy commands as controlled local background processes.

Only a previously created checkpoint may be used. Credentials are never
passed through the JSON API, subprocess arguments or log messages.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from uuid import uuid4

from getmyfamilytree.credentials import CredentialStore, CredentialStoreError

SCOPES = frozenset({"full", "ancestors", "descendants", "family"})
JOB_KINDS = frozenset({"download", "reports"})


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobOptions:
    """Options for a download or offline report-generation job."""

    kind: str
    scope: str = "full"
    max_people: int = 1000
    generations: int | None = None
    distance: int | None = None
    language: str = "es"
    csv: bool = False


def _nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_options(options):
    """Validate options before starting a subprocess or modifying output."""
    if not isinstance(options, JobOptions) or options.kind not in JOB_KINDS:
        raise ValueError("Unsupported research job")
    if options.scope not in SCOPES:
        raise ValueError("Unsupported genealogy scope")
    if options.language not in ("es", "en"):
        raise ValueError("Unsupported interface language")
    if not _nonnegative_integer(options.max_people):
        raise ValueError("max_people must be a non-negative integer")
    if not isinstance(options.csv, bool):
        raise ValueError("csv must be a boolean")

    if options.scope in ("ancestors", "descendants"):
        if not _nonnegative_integer(options.generations) or options.distance is not None:
            raise ValueError("Ancestor and descendant scopes require generations")
    elif options.scope == "family":
        if not _nonnegative_integer(options.distance) or options.generations is not None:
            raise ValueError("Family scope requires distance")
    elif options.generations is not None or options.distance is not None:
        raise ValueError("Full scope does not accept a depth limit")


def _command(database, origin, options):
    """Build a fixed-argument command without using a shell."""
    output = Path(database).with_suffix(".ged")
    command = [
        sys.executable,
        "-m",
        "getmyfamilytree.cli",
        "--database",
        str(database),
        "-o",
        str(output),
        "--language",
        options.language,
        "--scope",
        options.scope,
        "--max-people",
        str(options.max_people),
        "--progress-every",
        "1",
    ]

    if options.scope in ("ancestors", "descendants"):
        command.extend(["--generations", str(options.generations)])
    elif options.scope == "family":
        command.extend(["--distance", str(options.distance)])

    if options.kind == "reports":
        command.append("--report-only")
    else:
        command.extend(["--individual", origin])
    if options.csv:
        command.append("--csv")
    return command


class ResearchJobs:
    """Track one local download or report-generation process at a time."""

    def __init__(self, checkpoint, credentials=None, popen=None):
        self.checkpoint = checkpoint
        self.credentials = credentials if credentials is not None else CredentialStore()
        self._popen = popen if popen is not None else subprocess.Popen
        self._lock = RLock()
        self._process = None
        self._state = None
        self._messages = deque(maxlen=30)

    def _snapshot(self):
        state = dict(self._state) if self._state is not None else {
            "id": None,
            "kind": None,
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "error": None,
        }
        state["messages"] = list(self._messages)
        state["download"] = {
            name: self.checkpoint.count(name)
            for name in ("done", "pending", "failed")
        }
        return state

    def status(self):
        """Return job state and persisted download counters."""
        with self._lock:
            return self._snapshot()

    def _require_saved_login(self):
        """Allow non-interactive downloads only with an existing keyring login."""
        try:
            credentials = self.credentials.load()
        except CredentialStoreError as error:
            raise ValueError("Cannot access saved FamilySearch credentials") from error
        if credentials is None:
            raise ValueError(
                "Save a FamilySearch login using --remember-login first"
            )
        # Never retain or return the password in the job controller.

    def start(self, options):
        """Launch a validated task; reject concurrent work on the checkpoint."""
        _validate_options(options)
        if not self.checkpoint.path.is_file():
            raise ValueError("Research checkpoint does not exist")
        origin = self.checkpoint.get_origin()
        if not origin:
            raise ValueError("Research checkpoint has no starting person")

        with self._lock:
            if self._state is not None and self._state["status"] in (
                "running",
                "pause_requested",
            ):
                raise ValueError("A research job is already running")
            if options.kind == "download":
                self._require_saved_login()

            command = _command(self.checkpoint.path, origin, options)
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt"
                else 0
            )
            try:
                process = self._popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=flags,
                )
            except OSError as error:
                raise ValueError("Could not start the research job") from error

            self._process = process
            self._messages.clear()
            self._state = {
                "id": uuid4().hex,
                "kind": options.kind,
                "status": "running",
                "started_at": _timestamp(),
                "finished_at": None,
                "exit_code": None,
                "error": None,
            }
            Thread(target=self._watch, args=(process,), daemon=True).start()
            return self._snapshot()

    def _watch(self, process):
        """Consume output so a long-running child never blocks on its pipe."""
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    clean = line.strip()
                    if clean:
                        with self._lock:
                            self._messages.append(clean[:500])
            exit_code = process.wait()
            with self._lock:
                if process is not self._process:
                    return
                interrupted = self._state["status"] == "pause_requested"
                self._state["status"] = (
                    "paused"
                    if interrupted
                    else "completed"
                    if exit_code == 0
                    else "failed"
                )
                self._state["exit_code"] = exit_code
                self._state["finished_at"] = _timestamp()
                if exit_code != 0 and not interrupted:
                    self._state["error"] = "The research command failed"
        except (OSError, UnicodeError):
            with self._lock:
                if process is self._process:
                    self._state["status"] = "failed"
                    self._state["error"] = "Could not collect research job output"
                    self._state["finished_at"] = _timestamp()

    def pause(self):
        """Request a graceful interruption of an active download."""
        with self._lock:
            if self._state is None or self._state["status"] != "running":
                raise ValueError("No running research job")
            if self._state["kind"] != "download":
                raise ValueError("Only downloads support pausing")
            process = self._process
            if process.poll() is not None:
                raise ValueError("The download has already finished")
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGINT)
            except OSError as error:
                raise ValueError("Could not interrupt the download") from error
            self._state["status"] = "pause_requested"
            return self._snapshot()
