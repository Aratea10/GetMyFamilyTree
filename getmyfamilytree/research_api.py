"""Token-protected, loopback-only JSON API for the local research interface.

The API reads downloaded FamilySearch records from SQLite but never downloads
new data. It does not serve arbitrary local files or allow browser CORS.
"""

from __future__ import annotations

import json
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from getmyfamilytree.research_workspace import ResearchWorkspace, TaskQuery
from getmyfamilytree.scopes import PERSON_ID

MAX_REQUEST_BYTES = 1024 * 1024
CARD_ID = re.compile(r"[0-9a-f]{32}\Z")


def _query_options(parameters):
    """Parse and validate filtering options for the person list."""
    allowed = {"search", "status", "category", "branch", "order", "limit", "offset"}
    if set(parameters) - allowed:
        raise ValueError("Unknown task query parameter")
    if any(len(values) != 1 for values in parameters.values()):
        raise ValueError("Query parameters must not be repeated")

    values = {key: items[0] for key, items in parameters.items()}
    try:
        limit = int(values.get("limit", "50"))
        offset = int(values.get("offset", "0"))
    except ValueError as error:
        raise ValueError("Limit and offset must be integers") from error

    return TaskQuery(
        search=values.get("search", ""),
        status=values.get("status"),
        category=values.get("category"),
        branch=values.get("branch"),
        order=values.get("order", "recommended"),
        limit=limit,
        offset=offset,
    )


def _person_id(value):
    if not PERSON_ID.fullmatch(value):
        raise ValueError("Invalid FamilySearch person ID")
    return value


def _card_id(value):
    if not CARD_ID.fullmatch(value):
        raise ValueError("Invalid board card ID")
    return value


class _ResearchRequestHandler(BaseHTTPRequestHandler):
    """Handle local JSON requests without exposing a file server."""

    def log_message(self, format, *args):
        """Avoid printing potentially private person IDs into server logs."""

    def _respond(self, status, result=None):
        encoded = b"" if result is None else json.dumps(
            result, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if result is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def _authorized(self):
        value = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return value.startswith(prefix) and secrets.compare_digest(
            value[len(prefix):], self.server.auth_token
        )

    def _body(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != (
            "application/json"
        ):
            raise ValueError("Expected application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("JSON body has an invalid size")

        try:
            result = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid JSON body") from error
        if not isinstance(result, dict):
            raise ValueError("JSON body must be an object")
        return result

    def _get(self, parts, parameters):
        workspace = self.server.workspace
        if parts == ["summary"]:
            return workspace.summary()
        if parts == ["tasks"]:
            return workspace.list_tasks(_query_options(parameters))
        if parts == ["cards"]:
            return {"items": workspace.list_cards()}
        if len(parts) == 2 and parts[0] == "cards":
            return workspace.get_card(_card_id(parts[1]))
        if len(parts) == 2 and parts[0] == "people":
            return workspace.person_detail(_person_id(parts[1]))
        raise KeyError("Unknown route")

    def _task_change(self, identifier, payload):
        """Apply exactly one update so a rejected request has no side effects."""
        if len(payload) != 1:
            raise ValueError("Update exactly one task field per request")
        field, value = next(iter(payload.items()))
        plan = self.server.workspace.plan

        if field == "status":
            plan.set_status(identifier, value)
        elif field == "priority":
            plan.set_priority(identifier, value)
        elif field == "category":
            plan.set_category(identifier, value)
        elif field == "branches":
            plan.set_branches(identifier, value)
        elif field == "before_id":
            plan.move(identifier, before_id=value)
        else:
            raise ValueError("Unsupported task field")

        return self.server.workspace.person_detail(identifier)["task"]

    def _write(self, method, parts, payload):
        workspace = self.server.workspace

        if method == "POST" and parts == ["cards"]:
            if set(payload) - {"card", "people"}:
                raise ValueError("Unknown card creation property")
            return HTTPStatus.CREATED, workspace.create_card(
                payload.get("card", {}), payload.get("people")
            )

        if len(parts) == 2 and parts[0] == "cards":
            card_id = _card_id(parts[1])
            if method == "PATCH":
                if set(payload) - {"card", "people"}:
                    raise ValueError("Unknown card update property")
                return HTTPStatus.OK, workspace.update_card(
                    card_id, payload.get("card", {}), payload.get("people")
                )
            if method == "DELETE":
                workspace.delete_card(card_id)
                return HTTPStatus.NO_CONTENT, None

        if len(parts) == 3 and parts[0] == "people":
            identifier = _person_id(parts[1])
            if method == "PUT" and parts[2] == "note":
                if set(payload) != {"content"}:
                    raise ValueError("Expected the content field")
                workspace.set_person_note(identifier, payload["content"])
                return HTTPStatus.OK, {"content": workspace.get_person_note(identifier)}
            if method == "PATCH" and parts[2] == "task":
                return HTTPStatus.OK, self._task_change(identifier, payload)

        raise KeyError("Unknown route")

    def _dispatch(self, method):
        if not self._authorized():
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/v1/"):
            self._respond(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            return

        parts = parsed.path[len("/api/v1/"):].split("/")
        try:
            if method == "GET":
                parameters = parse_qs(parsed.query, keep_blank_values=True)
                response = self._get(parts, parameters)
                status = HTTPStatus.OK
            else:
                payload = {} if method == "DELETE" else self._body()
                status, response = self._write(method, parts, payload)
        except KeyError:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        except (TypeError, ValueError) as error:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        self._respond(status, response)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def create_research_server(checkpoint, port=0, token=None):
    """Create a local API server with a random bearer token by default."""
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", port), _ResearchRequestHandler)
    server.daemon_threads = True
    server.auth_token = secrets.token_urlsafe(32) if token is None else token
    server.workspace = ResearchWorkspace(checkpoint)
    return server


def serve_research(checkpoint, port=0):
    """Run the local API until interrupted, closing its socket afterwards."""
    server = create_research_server(checkpoint, port=port)
    address = f"http://127.0.0.1:{server.server_port}/api/v1"
    print(f"Local research API: {address}", flush=True)
    print(f"Authorization: Bearer {server.auth_token}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
