from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from packages.minicloud_cli import cli


class MiniCloudApiHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return

    def _respond(self, code: int, payload: dict | list) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.command, self.path, payload, self.headers.get("Authorization")))  # type: ignore[attr-defined]
        if self.path == "/api/v1/auth/login":
            if self.server.login_status != 200:  # type: ignore[attr-defined]
                self._respond(self.server.login_status, {"detail": "Invalid credentials"})  # type: ignore[attr-defined]
                return
            self._respond(200, {"access_token": "test-access-token"})
            return
        if self.path == "/api/v1/auth/register":
            self._respond(
                201,
                {"user": {"id": "user-1", "email": payload["email"]}, "access_token": "test-access-token"},
            )
            return
        if self.path == "/api/v1/projects/project-1/deployments":
            self._respond(
                202,
                {
                    "deployment_id": "deployment-1",
                    "kubernetes_resource_name": "mc-deployment-1",
                    "job_state": "queued",
                    "status": "accepted",
                    "last_error": None,
                },
            )
            return
        if self.path == "/api/v1/projects/project-1/deployments/deployment-1/rollback":
            self._respond(
                202,
                {
                    "deployment_id": "deployment-1",
                    "revision_id": "revision-4",
                    "kubernetes_resource_name": "mc-deployment-1",
                    "job_state": "queued",
                    "status": "accepted",
                    "last_error": None,
                },
            )
            return
        if self.path == "/api/v1/projects/project-1/members":
            self._respond(
                201,
                {"user_id": "user-2", "email": payload["email"], "role": payload["role"]},
            )
            return
        self._respond(404, {"detail": "Not found"})

    def do_GET(self) -> None:
        self.server.requests.append((self.command, self.path, None, self.headers.get("Authorization")))  # type: ignore[attr-defined]
        request_path = urlsplit(self.path).path
        if request_path == "/api/v1/projects":
            self._respond(
                200,
                {
                    "items": [{"id": "project-1", "name": "Demo"}],
                    "next_cursor": "next-project-page",
                },
            )
            return
        if request_path == "/api/v1/projects/project-1/deployments":
            self._respond(
                200,
                {
                    "items": [{"id": "deployment-1", "name": "web"}],
                    "next_cursor": "next-deployment-page",
                },
            )
            return
        if request_path == "/api/v1/projects/project-1/deployments/deployment-1/revisions":
            self._respond(
                200,
                {
                    "items": [{"id": "revision-1", "revision_number": 1}],
                    "next_cursor": None,
                },
            )
            return
        if self.path == "/api/v1/capabilities":
            self._respond(
                200,
                {
                    "api_version": "v1",
                    "metrics": {
                        "available": False,
                        "status": "unavailable",
                        "reason": "No metrics provider is configured.",
                    },
                    "alerts": {
                        "available": False,
                        "status": "unavailable",
                        "reason": "Alert delivery is not implemented.",
                    },
                },
            )
            return
        if self.path == "/api/v1/projects/project-1/members":
            self._respond(
                200,
                [{"user_id": "user-2", "email": "member@example.com", "role": "member"}],
            )
            return
        if self.path == "/api/v1/operations/deployment-1":
            if self.server.operation_status == 503:  # type: ignore[attr-defined]
                self._respond(503, {"detail": "internal details must not be shown"})  # type: ignore[attr-defined]
                return
            operation_sequence = self.server.operation_sequence  # type: ignore[attr-defined]
            request_index = self.server.operation_get_count  # type: ignore[attr-defined]
            self.server.operation_get_count += 1  # type: ignore[attr-defined]
            if operation_sequence:
                index = min(request_index, len(operation_sequence) - 1)
                status, job_state = operation_sequence[index]
            else:
                status = self.server.operation_status  # type: ignore[attr-defined]
                job_state = self.server.job_state  # type: ignore[attr-defined]
            self._respond(
                200,
                {
                    "deployment_id": "deployment-1",
                    "kubernetes_resource_name": "mc-deployment-1",
                    "job_state": job_state,
                    "status": status,
                    "last_error": None,
                },
            )
            return
        if self.path.startswith("/api/v1/projects/project-1/deployments/deployment-1/events"):
            self._respond(200, {"items": [], "next_cursor": None})
            return
        if request_path == "/api/v1/projects/project-1/deployments/deployment-1/logs":
            if self.server.diagnostics_status != 200:  # type: ignore[attr-defined]
                self._respond(503, {"detail": "Kubernetes diagnostics are unavailable."})
                return
            self._respond(
                200,
                {
                    "items": [
                        {
                            "pod_name": "web-abc",
                            "container": "app",
                            "log": "server started",
                            "truncated": False,
                        }
                    ],
                    "source_time": "2026-11-14T12:00:00Z",
                    "truncated": False,
                },
            )
            return
        if request_path == "/api/v1/projects/project-1/deployments/deployment-1/kubernetes-events":
            if self.server.diagnostics_status != 200:  # type: ignore[attr-defined]
                self._respond(503, {"detail": "Kubernetes diagnostics are unavailable."})
                return
            self._respond(
                200,
                {
                    "items": [
                        {
                            "resource_kind": "Pod",
                            "resource_name": "web-abc",
                            "event_type": "Warning",
                            "reason": "BackOff",
                            "message": "Back-off restarting container",
                            "created_at": "2026-11-14T12:00:00Z",
                        }
                    ],
                    "source_time": "2026-11-14T12:00:00Z",
                    "truncated": False,
                },
            )
            return
        self._respond(404, {"detail": "Not found"})

    def do_PATCH(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.command, self.path, payload, self.headers.get("Authorization")))  # type: ignore[attr-defined]
        if self.path == "/api/v1/projects/project-1/deployments/deployment-1/scale":
            self._respond(
                202,
                {
                    "deployment_id": "deployment-1",
                    "kubernetes_resource_name": "mc-deployment-1",
                    "job_state": "queued",
                    "status": "accepted",
                    "last_error": None,
                },
            )
            return
        if self.path == "/api/v1/projects/project-1/members/user-2":
            self._respond(
                200,
                {"user_id": "user-2", "email": "member@example.com", "role": payload["role"]},
            )
            return
        self._respond(404, {"detail": "Not found"})

    def do_DELETE(self) -> None:
        self.server.requests.append((self.command, self.path, None, self.headers.get("Authorization")))  # type: ignore[attr-defined]
        if self.path == "/api/v1/projects/project-1/members/user-2":
            self.send_response(204)
            self.end_headers()
            return
        self._respond(404, {"detail": "Not found"})


@pytest.fixture
def api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCloudApiHandler)
    server.requests = []
    server.login_status = 200
    server.operation_status = "ready"
    server.job_state = "completed"
    server.operation_sequence = None  # type: ignore[attr-defined]
    server.operation_get_count = 0  # type: ignore[attr-defined]
    server.diagnostics_status = 200  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _args(api_url: str, *command: str) -> list[str]:
    return ["--output", "json", "--api-url", api_url, "--email", "developer@example.com", *command]


def test_deploy_create_waits_for_ready_without_exposing_token(api_server, monkeypatch, capsys):
    server, api_url = api_server
    server.operation_sequence = [("accepted", "queued"), ("ready", "completed")]
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "do-not-print-this-password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "create",
            "--project-id",
            "project-1",
            "--name",
            "web",
            "--image",
            "nginx:1.27",
            "--poll-interval",
            "0.1",
            "--timeout",
            "1",
        )
    )

    output = capsys.readouterr().out
    assert result == 0
    assert '"status": "ready"' in output
    assert server.operation_get_count == 2
    assert "test-access-token" not in output
    assert "do-not-print-this-password" not in output
    assert server.requests[-1][3] == "Bearer test-access-token"


def test_deploy_create_returns_nonzero_when_only_accepted_before_timeout(
    api_server,
    monkeypatch,
    capsys,
):
    server, api_url = api_server
    server.operation_status = "accepted"
    server.job_state = "queued"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "create",
            "--project-id",
            "project-1",
            "--name",
            "web",
            "--image",
            "nginx:1.27",
            "--timeout",
            "0.1",
            "--poll-interval",
            "0.1",
        )
    )

    assert result == 1
    assert '"timed_out": true' in capsys.readouterr().out


def test_deploy_scale_submits_idempotency_key_and_waits_for_ready(
    api_server,
    monkeypatch,
    capsys,
):
    server, api_url = api_server
    server.operation_sequence = [("progressing", "running"), ("ready", "completed")]
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "scale",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
            "--replicas",
            "3",
            "--expected-replicas",
            "2",
            "--idempotency-key",
            "scale-request-1",
            "--poll-interval",
            "0.1",
            "--timeout",
            "1",
        )
    )

    assert result == 0
    assert '"status": "ready"' in capsys.readouterr().out
    assert server.requests[1] == (
        "PATCH",
        "/api/v1/projects/project-1/deployments/deployment-1/scale",
        {
            "replicas": 3,
            "expected_replicas": 2,
            "idempotency_key": "scale-request-1",
        },
        "Bearer test-access-token",
    )
    assert server.operation_get_count == 2


def test_deploy_rollback_submits_revision_precondition_and_waits_for_ready(
    api_server,
    monkeypatch,
    capsys,
):
    server, api_url = api_server
    server.operation_sequence = [("progressing", "running"), ("ready", "completed")]
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "rollback",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
            "--revision-id",
            "revision-1",
            "--expected-revision-id",
            "revision-3",
            "--idempotency-key",
            "rollback-request-1",
            "--poll-interval",
            "0.1",
            "--timeout",
            "1",
        )
    )

    assert result == 0
    assert '"status": "ready"' in capsys.readouterr().out
    assert server.requests[1] == (
        "POST",
        "/api/v1/projects/project-1/deployments/deployment-1/rollback",
        {
            "revision_id": "revision-1",
            "expected_revision_id": "revision-3",
            "idempotency_key": "rollback-request-1",
        },
        "Bearer test-access-token",
    )
    assert server.operation_get_count == 2


def test_deploy_revisions_preserves_cursor_pagination(api_server, monkeypatch, capsys):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "revisions",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
            "--limit",
            "10",
        )
    )

    assert result == 0
    assert '"revision_number": 1' in capsys.readouterr().out
    assert server.requests[-1][0] == "GET"
    assert urlsplit(server.requests[-1][1]).path.endswith("/revisions")
    assert parse_qs(urlsplit(server.requests[-1][1]).query) == {"limit": ["10"]}


@pytest.mark.parametrize(
    ("status", "job_state", "exit_code"),
    [("progressing", "running", 0), ("failed", "failed", 1)],
)
def test_deploy_status_preserves_pending_and_fails_terminal_operation(
    api_server, monkeypatch, capsys, status, job_state, exit_code
):
    server, api_url = api_server
    server.operation_status = status
    server.job_state = job_state
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, "deploy", "status", "--deployment-id", "deployment-1"))

    output = capsys.readouterr().out
    assert result == exit_code
    assert f'"status": "{status}"' in output


def test_cli_status_defaults_to_readable_text(api_server, monkeypatch, capsys):
    _, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        [
            "--api-url",
            api_url,
            "--email",
            "developer@example.com",
            "deploy",
            "status",
            "--deployment-id",
            "deployment-1",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Status: ready" in output
    assert "Job state: completed" in output
    assert not output.lstrip().startswith("{")


def test_platform_capabilities_reports_unavailable_features_from_api(api_server, monkeypatch, capsys):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, "platform", "capabilities"))

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["api_version"] == "v1"
    assert output["metrics"]["available"] is False
    assert output["metrics"]["status"] == "unavailable"
    assert output["alerts"]["available"] is False
    assert server.requests[-1] == (
        "GET",
        "/api/v1/capabilities",
        None,
        "Bearer test-access-token",
    )


@pytest.mark.parametrize(
    ("command", "expected_path", "cursor", "limit"),
    [
        (
            ["project", "list", "--limit", "10", "--cursor", "project-cursor"],
            "/api/v1/projects",
            "project-cursor",
            "10",
        ),
        (
            [
                "deploy",
                "list",
                "--project-id",
                "project-1",
                "--limit",
                "5",
                "--cursor",
                "deployment-cursor",
            ],
            "/api/v1/projects/project-1/deployments",
            "deployment-cursor",
            "5",
        ),
    ],
)
def test_cli_lists_cursor_pages(
    api_server, monkeypatch, capsys, command, expected_path, cursor, limit
):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, *command))

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["next_cursor"].startswith("next-")
    request_path = server.requests[-1][1]
    parsed = urlsplit(request_path)
    query = parse_qs(parsed.query)
    assert parsed.path == expected_path
    assert query["cursor"] == [cursor]
    assert query["limit"] == [limit]
    assert server.requests[-1][3] == "Bearer test-access-token"


def test_project_member_list_uses_api(api_server, monkeypatch, capsys):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, "project", "member", "list", "--project-id", "project-1"))

    assert result == 0
    assert '"email": "member@example.com"' in capsys.readouterr().out
    assert server.requests[-1] == (
        "GET",
        "/api/v1/projects/project-1/members",
        None,
        "Bearer test-access-token",
    )


@pytest.mark.parametrize(
    ("command", "method", "path", "body"),
    [
        (
            ["add", "--project-id", "project-1", "--email", "member@example.com"],
            "POST",
            "/api/v1/projects/project-1/members",
            {"email": "member@example.com", "role": "member"},
        ),
        (
            ["role", "--project-id", "project-1", "--user-id", "user-2", "--role", "owner"],
            "PATCH",
            "/api/v1/projects/project-1/members/user-2",
            {"role": "owner"},
        ),
        (
            ["remove", "--project-id", "project-1", "--user-id", "user-2"],
            "DELETE",
            "/api/v1/projects/project-1/members/user-2",
            None,
        ),
    ],
)
def test_project_member_mutations_use_api(
    api_server, monkeypatch, command, method, path, body
):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, "project", "member", *command))

    assert result == 0
    assert server.requests[-1] == (method, path, body, "Bearer test-access-token")
    assert server.requests[0][2]["email"] == "developer@example.com"


def test_deploy_watch_polls_until_ready(api_server, monkeypatch, capsys):
    server, api_url = api_server
    server.operation_sequence = [("progressing", "running"), ("ready", "completed")]
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "watch",
            "--deployment-id",
            "deployment-1",
            "--poll-interval",
            "0.1",
            "--timeout",
            "1",
        )
    )

    assert result == 0
    assert '"status": "ready"' in capsys.readouterr().out
    assert server.operation_get_count == 2


@pytest.mark.parametrize(
    ("status", "job_state"),
    [("failed", "failed"), ("progressing", "failed")],
)
def test_deploy_watch_fails_terminal_failure(api_server, monkeypatch, capsys, status, job_state):
    server, api_url = api_server
    server.operation_status = status
    server.job_state = job_state
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(api_url, "deploy", "watch", "--deployment-id", "deployment-1")
    )

    assert result == 1
    assert f'"status": "{status}"' in capsys.readouterr().out
    assert server.operation_get_count == 1


def test_deploy_watch_times_out_with_last_observed_state(api_server, monkeypatch, capsys):
    server, api_url = api_server
    server.operation_status = "progressing"
    server.job_state = "running"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "watch",
            "--deployment-id",
            "deployment-1",
            "--poll-interval",
            "0.3",
            "--timeout",
            "0.2",
        )
    )

    output = capsys.readouterr().out
    assert result == 1
    assert '"timed_out": true' in output
    assert '"status": "progressing"' in output


@pytest.mark.parametrize("timeout", ["0", "inf", "86401"])
def test_watch_rejects_invalid_timeout(api_server, timeout):
    _, api_url = api_server

    with pytest.raises(SystemExit):
        cli.main(
            _args(
                api_url,
                "deploy",
                "watch",
                "--deployment-id",
                "deployment-1",
                "--timeout",
                timeout,
            )
        )


def test_watch_rejects_poll_interval_below_rate_limit(api_server):
    _, api_url = api_server

    with pytest.raises(SystemExit):
        cli.main(
            _args(
                api_url,
                "deploy",
                "watch",
                "--deployment-id",
                "deployment-1",
                "--poll-interval",
                "0.01",
            )
        )


def test_events_cursor_is_encoded_and_pagination_cursor_is_preserved(api_server, monkeypatch):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")
    cursor = "2026-11-12T00:00:00+00:00|event-1"

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "events",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
            "--cursor",
            cursor,
        )
    )

    assert result == 0
    request_path = server.requests[-1][1]
    query = urlsplit(request_path).query
    assert "cursor=2026-11-12T00%3A00%3A00%2B00%3A00%7Cevent-1" in query


def test_deploy_logs_sends_bounded_parameters_and_preserves_response(api_server, monkeypatch, capsys):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "logs",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
            "--container",
            "app",
            "--since-seconds",
            "3600",
            "--tail-lines",
            "120",
        )
    )

    assert result == 0
    assert '"server started"' in capsys.readouterr().out
    method, request_path, _, authorization = server.requests[-1]
    query = parse_qs(urlsplit(request_path).query)
    assert method == "GET"
    assert urlsplit(request_path).path.endswith("/deployments/deployment-1/logs")
    assert query == {"container": ["app"], "since_seconds": ["3600"], "tail_lines": ["120"]}
    assert authorization == "Bearer test-access-token"


def test_deploy_kubernetes_events_calls_scoped_api(api_server, monkeypatch, capsys):
    server, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "kubernetes-events",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
        )
    )

    assert result == 0
    assert '"BackOff"' in capsys.readouterr().out
    method, request_path, _, authorization = server.requests[-1]
    assert method == "GET"
    assert request_path == (
        "/api/v1/projects/project-1/deployments/deployment-1/kubernetes-events"
    )
    assert authorization == "Bearer test-access-token"


@pytest.mark.parametrize(("option", "value"), [("--tail-lines", "1001"), ("--since-seconds", "86401")])
def test_deploy_logs_rejects_values_beyond_api_bounds(api_server, option, value):
    _, api_url = api_server

    with pytest.raises(SystemExit):
        cli.main(
            _args(
                api_url,
                "deploy",
                "logs",
                "--project-id",
                "project-1",
                "--deployment-id",
                "deployment-1",
                option,
                value,
            )
        )


def test_deploy_logs_reports_unavailable_diagnostics(api_server, monkeypatch, capsys):
    server, api_url = api_server
    server.diagnostics_status = 503
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        _args(
            api_url,
            "deploy",
            "logs",
            "--project-id",
            "project-1",
            "--deployment-id",
            "deployment-1",
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "HTTP 503" in captured.err
    assert '"items"' not in captured.out


def test_authentication_and_server_errors_are_nonzero_and_sanitized(api_server, monkeypatch, capsys):
    server, api_url = api_server
    server.login_status = 401
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(_args(api_url, "deploy", "status", "--deployment-id", "deployment-1"))
    assert result == 1
    assert "HTTP 401: Invalid credentials" in capsys.readouterr().err

    server.login_status = 200
    server.operation_status = 503
    result = cli.main(_args(api_url, "deploy", "status", "--deployment-id", "deployment-1"))
    error = capsys.readouterr().err
    assert result == 1
    assert "HTTP 503" in error
    assert "internal details" not in error


def test_register_does_not_print_or_persist_access_token(api_server, monkeypatch, capsys):
    _, api_url = api_server
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "password")

    result = cli.main(
        ["--output", "json", "--api-url", api_url, "register", "--email", "new@example.com"]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert '"registered": true' in output
    assert "test-access-token" not in output
    assert "password" not in output
