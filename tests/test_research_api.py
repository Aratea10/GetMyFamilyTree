"""Tests for the loopback-only JSON interface used by future UI clients."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from getmyfamilytree.checkpoint import Checkpoint
from getmyfamilytree.reports import PERSON_URL
from getmyfamilytree.research_api import create_research_server


class ResearchAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "test.sqlite"
        self.checkpoint = Checkpoint(self.database)
        self.checkpoint.set_origin("SELF-111")
        self.checkpoint.seed(["SELF-111"])
        self.checkpoint.save_response(
            PERSON_URL + "SELF-111",
            {"persons": [{"id": "SELF-111", "display": {"name": "Test Person"}}]},
        )
        self.checkpoint.finish("SELF-111", [])

        self.server = create_research_server(
            self.checkpoint, port=0, token="test-secret"
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.checkpoint.close()
        self.temporary.cleanup()

    def request(self, path, method="GET", payload=None, authenticated=True):
        headers = {}
        data = None
        if authenticated:
            headers["Authorization"] = "Bearer test-secret"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        request = Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def test_api_requires_token_and_only_binds_to_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.assertRaises(HTTPError) as caught:
            self.request("/summary", authenticated=False)
        self.assertEqual(caught.exception.code, 401)

    def test_summary_tasks_person_detail_and_manual_status(self):
        status, summary = self.request("/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["people"], 1)

        _, page = self.request("/tasks?search=test&limit=1")
        self.assertEqual(page["items"][0]["id"], "SELF-111")

        _, updated = self.request(
            "/people/SELF-111/task",
            method="PATCH",
            payload={"status": "in_progress"},
        )
        self.assertEqual(updated["status"], "in_progress")

        _, detail = self.request("/people/SELF-111")
        self.assertEqual(detail["task"]["status"], "in_progress")

        self.request(
            "/people/SELF-111/note",
            method="PUT",
            payload={"content": "Check archive"},
        )
        _, detail = self.request("/people/SELF-111")
        self.assertEqual(detail["research_note"], "Check archive")

    def test_board_crud_and_unknown_card(self):
        status, card = self.request(
            "/cards",
            method="POST",
            payload={
                "card": {"kind": "document", "title": "Baptism record"},
                "people": ["SELF-111"],
            },
        )
        self.assertEqual(status, 201)

        _, updated = self.request(
            "/cards/" + card["id"],
            method="PATCH",
            payload={"card": {"x": 78}},
        )
        self.assertEqual(updated["x"], 78.0)

        _, detail = self.request("/people/SELF-111")
        self.assertEqual(len(detail["cards"]), 1)

        self.assertEqual(
            self.request("/cards/" + card["id"], method="DELETE")[0],
            204,
        )
        with self.assertRaises(HTTPError) as caught:
            self.request("/cards/" + card["id"])
        self.assertEqual(caught.exception.code, 404)

    def test_upload_and_preview_attachment_with_authorization(self):
        status, asset = self.request(
            "/assets",
            method="POST",
            payload={
                "name": "old-record.pdf",
                "content_base64": base64.b64encode(b"%PDF-test").decode("ascii"),
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(asset["media_type"], "application/pdf")

        request = Request(
            self.base + "/assets/" + asset["id"] + "/content",
            headers={"Authorization": "Bearer test-secret"},
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.read(), b"%PDF-test")
            self.assertEqual(response.headers["Content-Type"], "application/pdf")

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/assets",
                method="POST",
                payload={"name": "bad.exe", "content_base64": "YQ=="},
            )
        self.assertEqual(caught.exception.code, 400)

        self.assertEqual(
            self.request("/assets/" + asset["id"], method="DELETE")[0],
            204,
        )

    def test_invalid_requests_do_not_modify_data(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/people/SELF-111/task",
                method="PATCH",
                payload={"status": "done", "priority": 0},
            )
        self.assertEqual(caught.exception.code, 400)

        with self.assertRaises(HTTPError) as caught:
            self.request("/tasks?limit=invalid")
        self.assertEqual(caught.exception.code, 400)

        _, detail = self.request("/people/SELF-111")
        self.assertEqual(detail["task"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
