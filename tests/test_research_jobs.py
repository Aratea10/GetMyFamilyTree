"""Regression tests for GUI-driven genealogy download and report jobs."""

from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.research_jobs import JobOptions, ResearchJobs, _command


class _Credentials:
    def __init__(self, saved=True):
        self.saved = saved
        self.calls = 0

    def load(self):
        self.calls += 1
        return ("saved_user", "test-secret") if self.saved else None


class _CompletedProcess:
    def __init__(self, code=0, output="Ready\n"):
        self.stdout = io.StringIO(output)
        self.code = code

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def send_signal(self, signal_number):
        raise AssertionError("Already completed")


class _PendingProcess:
    def __init__(self):
        self.release = Event()
        self.signals = []
        self.stdout = self

    def __iter__(self):
        self.release.wait(timeout=5)
        yield "Pausing the download\n"

    def wait(self, timeout=None):
        self.release.wait(timeout=5 if timeout is None else timeout)
        return 0

    def poll(self):
        return None if not self.release.is_set() else 0

    def send_signal(self, signal_number):
        self.signals.append(signal_number)
        self.release.set()


class ResearchJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "research.sqlite"
        self.store = Checkpoint(self.db)
        self.addCleanup(self.store.close)
        self.store.set_origin("SELF-111")
        self.calls = []
        self.credentials = _Credentials()

    def _popen(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return _CompletedProcess()

    @staticmethod
    def _settled(jobs):
        for _ in range(600):
            status = jobs.status()
            if status["status"] in ("completed", "failed", "paused"):
                return status
            time.sleep(0.02)
        raise AssertionError("Research job did not settle")

    def test_report_generation_uses_existing_checkpoint_without_login(self):
        jobs = ResearchJobs(self.store, credentials=self.credentials, popen=self._popen)
        status = jobs.start(JobOptions(kind="reports", csv=True))
        self.assertEqual(status["kind"], "reports")
        result = self._settled(jobs)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["messages"], ["Ready"])
        self.assertEqual(self.credentials.calls, 0)

        command, kwargs = self.calls[0]
        self.assertIn("--report-only", command)
        self.assertIn("--csv", command)
        self.assertNotIn("--individual", command)
        self.assertEqual(
            command[command.index("-o") + 1], str(self.db.with_suffix(".ged"))
        )
        self.assertEqual(kwargs["stdin"], -3)

    def test_download_requires_saved_keyring_login(self):
        missing = ResearchJobs(
            self.store, credentials=_Credentials(saved=False), popen=self._popen
        )
        with self.assertRaisesRegex(ValueError, "remember-login"):
            missing.start(JobOptions(kind="download"))
        self.assertEqual(self.calls, [])

        jobs = ResearchJobs(self.store, credentials=self.credentials, popen=self._popen)
        jobs.start(JobOptions(kind="download", scope="ancestors", generations=3))
        self._settled(jobs)
        command = self.calls[0][0]
        self.assertEqual(command[command.index("--individual") + 1], "SELF-111")
        self.assertIn("--generations", command)
        self.assertNotIn("test-secret", command)

    def test_graceful_pause_and_concurrent_job_rejection(self):
        process = _PendingProcess()
        jobs = ResearchJobs(
            self.store,
            credentials=self.credentials,
            popen=lambda command, **kwargs: process,
        )
        jobs.start(JobOptions(kind="download", scope="family", distance=2))
        with self.assertRaisesRegex(ValueError, "already running"):
            jobs.start(JobOptions(kind="reports"))
        result = jobs.pause()
        self.assertEqual(result["status"], "pause_requested")
        self.assertEqual(len(process.signals), 1)
        self.assertEqual(self._settled(jobs)["status"], "paused")

    def test_closing_the_service_requests_safe_pause(self):
        process = _PendingProcess()
        jobs = ResearchJobs(
            self.store,
            credentials=self.credentials,
            popen=lambda command, **kwargs: process,
        )
        jobs.start(JobOptions(kind="download"))
        jobs.close()
        self.assertTrue(process.signals)
        self.assertEqual(self._settled(jobs)["status"], "paused")

    def test_offline_reports_run_in_a_real_subprocess(self):
        self.store.seed(["SELF-111"])
        self.store.save_response(
            PERSON_URL + "SELF-111",
            {"persons": [{"id": "SELF-111", "display": {"name": "Test"}}]},
        )
        self.store.finish("SELF-111", [])
        jobs = ResearchJobs(self.store)
        jobs.start(JobOptions(kind="reports", csv=True))
        status = self._settled(jobs)

        self.assertEqual(status["status"], "completed", status["messages"])
        self.assertTrue(self.db.with_suffix(".md").is_file())
        self.assertTrue(self.db.with_suffix(".events.csv").is_file())

    def test_bad_options_rejected_without_running_a_process(self):
        jobs = ResearchJobs(self.store, credentials=self.credentials, popen=self._popen)
        for options in (
            JobOptions(kind="unknown"),
            JobOptions(kind="download", max_people=-1),
            JobOptions(kind="download", scope="ancestors"),
            JobOptions(kind="reports", scope="family", distance=-2),
            JobOptions(kind="reports", scope="full", generations=3),
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                jobs.start(options)
        self.assertEqual(self.calls, [])

    def test_failed_jobs_preserve_exit_code_and_output(self):
        jobs = ResearchJobs(
            self.store,
            credentials=self.credentials,
            popen=lambda command, **kwargs: _CompletedProcess(2, "Bad credentials\n"),
        )
        jobs.start(JobOptions(kind="reports"))
        result = self._settled(jobs)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["messages"], ["Bad credentials"])

    def test_command_uses_only_explicit_validated_options(self):
        command = _command(
            self.db,
            "SELF-111",
            JobOptions(kind="reports", scope="descendants", generations=2),
        )
        self.assertIn("--generations", command)
        self.assertEqual(command[command.index("--generations") + 1], "2")
        self.assertIn("--report-only", command)
        self.assertNotIn("--individual", command)


if __name__ == "__main__":
    unittest.main()
