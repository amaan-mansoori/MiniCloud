from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Literal, overload
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_WATCH_TIMEOUT_SECONDS = 86_400.0
MIN_WATCH_POLL_INTERVAL_SECONDS = 0.1
MAX_WATCH_POLL_INTERVAL_SECONDS = 300.0


class CliError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive number of seconds") from None
    if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_WATCH_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 86400 seconds")
    return seconds


def _poll_interval_seconds(value: str) -> float:
    seconds = _positive_seconds(value)
    if not MIN_WATCH_POLL_INTERVAL_SECONDS <= seconds <= MAX_WATCH_POLL_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError("must be between 0.1 and 300 seconds")
    return seconds


def _bounded_integer(minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("must be an integer") from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number

    return parse


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise CliError("API URL must be an HTTP(S) URL without credentials, query, or fragment.", 2)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise CliError("HTTPS is required for non-local API URLs.", 2)
        self.base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.timeout = timeout
        self.token: str | None = None

    @overload
    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
        allow_empty_response: Literal[False] = False,
    ) -> dict[str, Any] | list[Any]: ...

    @overload
    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
        allow_empty_response: Literal[True],
    ) -> dict[str, Any] | list[Any] | None: ...

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
        allow_empty_response: bool = False,
    ) -> dict[str, Any] | list[Any] | None:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if authenticated:
            if not self.token:
                raise CliError("Authentication is required.")
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                response_status = response.status
        except HTTPError as exc:
            detail = ""
            if exc.code < 500:
                try:
                    error_body = json.loads(exc.read())
                    value = error_body.get("detail")
                    if isinstance(value, str):
                        detail = f": {value}"
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            raise CliError(f"API request failed with HTTP {exc.code}{detail}.") from None
        except (URLError, TimeoutError, OSError):
            raise CliError("MiniCloud API is unavailable.") from None
        if response_status == 204:
            if allow_empty_response:
                return None
            raise CliError("MiniCloud API returned an unexpected empty response.")
        try:
            result = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CliError("MiniCloud API returned an invalid response.") from None
        if not isinstance(result, (dict, list)):
            raise CliError("MiniCloud API returned an invalid response.")
        return result

    def authenticate(self, email: str, password: str) -> None:
        result = self.request(
            "POST",
            "/api/v1/auth/login",
            body={"email": email, "password": password},
            authenticated=False,
        )
        if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
            raise CliError("MiniCloud API returned an invalid authentication response.")
        self.token = result["access_token"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minicloud", description="Manage MiniCloud projects and deployments.")
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("MINICLOUD_API_URL", "http://localhost:8001"),
        help="MiniCloud API base URL (or MINICLOUD_API_URL).",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("MINICLOUD_EMAIL"),
        help="Account email (or MINICLOUD_EMAIL); password is prompted securely.",
    )
    commands = parser.add_subparsers(dest="resource", required=True)

    register = commands.add_parser("register", help="Register an account.")
    register.add_argument("--email", dest="register_email", required=True)

    platform = commands.add_parser("platform", help="Inspect platform capabilities.")
    platform_commands = platform.add_subparsers(dest="action", required=True)
    platform_commands.add_parser("capabilities", help="Show available and unavailable capabilities.")

    project = commands.add_parser("project", help="Manage projects.")
    project_commands = project.add_subparsers(dest="action", required=True)
    project_list = project_commands.add_parser("list", help="List a page of projects.")
    project_list.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    project_list.add_argument("--cursor")
    create_project = project_commands.add_parser("create", help="Create a project.")
    create_project.add_argument("--name", required=True)
    create_project.add_argument("--slug", required=True)

    member = project_commands.add_parser("member", help="Manage project members.")
    member_commands = member.add_subparsers(dest="member_action", required=True)
    list_members = member_commands.add_parser("list", help="List project members.")
    list_members.add_argument("--project-id", required=True)
    add_member = member_commands.add_parser("add", help="Add a project member.")
    add_member.add_argument("--project-id", required=True)
    add_member.add_argument("--email", dest="member_email", required=True)
    add_member.add_argument("--role", choices=("owner", "member"), default="member")
    update_member = member_commands.add_parser("role", help="Update a member's role.")
    update_member.add_argument("--project-id", required=True)
    update_member.add_argument("--user-id", required=True)
    update_member.add_argument("--role", choices=("owner", "member"), required=True)
    remove_member = member_commands.add_parser("remove", help="Remove a project member.")
    remove_member.add_argument("--project-id", required=True)
    remove_member.add_argument("--user-id", required=True)

    deploy = commands.add_parser("deploy", help="Manage deployment operations.")
    deploy_commands = deploy.add_subparsers(dest="action", required=True)
    deployment_list = deploy_commands.add_parser("list", help="List a page of project deployments.")
    deployment_list.add_argument("--project-id", required=True)
    deployment_list.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    deployment_list.add_argument("--cursor")
    create_deploy = deploy_commands.add_parser("create", help="Create a deployment and wait for readiness.")
    create_deploy.add_argument("--project-id", required=True)
    create_deploy.add_argument("--name", required=True)
    create_deploy.add_argument("--image", required=True)
    create_deploy.add_argument("--port", type=int, default=8080)
    create_deploy.add_argument("--replicas", type=int, default=1)
    create_deploy.add_argument("--idempotency-key")
    create_deploy.add_argument("--poll-interval", type=_poll_interval_seconds, default=2.0, metavar="SECONDS")
    create_deploy.add_argument("--timeout", type=_positive_seconds, default=300.0, metavar="SECONDS")

    scale = deploy_commands.add_parser("scale", help="Scale a ready deployment and wait for readiness.")
    scale.add_argument("--project-id", required=True)
    scale.add_argument("--deployment-id", required=True)
    scale.add_argument("--replicas", type=int, choices=range(1, 11), required=True)
    scale.add_argument("--expected-replicas", type=int, choices=range(1, 11), required=True)
    scale.add_argument("--idempotency-key", required=True)
    scale.add_argument("--poll-interval", type=_poll_interval_seconds, default=2.0, metavar="SECONDS")
    scale.add_argument("--timeout", type=_positive_seconds, default=300.0, metavar="SECONDS")

    revisions = deploy_commands.add_parser("revisions", help="List a page of deployment revisions.")
    revisions.add_argument("--project-id", required=True)
    revisions.add_argument("--deployment-id", required=True)
    revisions.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    revisions.add_argument("--cursor")

    rollback = deploy_commands.add_parser("rollback", help="Roll back to a revision and wait for readiness.")
    rollback.add_argument("--project-id", required=True)
    rollback.add_argument("--deployment-id", required=True)
    rollback.add_argument("--revision-id", required=True)
    rollback.add_argument("--expected-revision-id", required=True)
    rollback.add_argument("--idempotency-key", required=True)
    rollback.add_argument("--poll-interval", type=_poll_interval_seconds, default=2.0, metavar="SECONDS")
    rollback.add_argument("--timeout", type=_positive_seconds, default=300.0, metavar="SECONDS")

    operation = deploy_commands.add_parser("status", help="Read deployment operation state.")
    operation.add_argument("--deployment-id", required=True)

    watch = deploy_commands.add_parser("watch", help="Poll until a deployment is ready or fails.")
    watch.add_argument("--deployment-id", required=True)
    watch.add_argument("--poll-interval", type=_poll_interval_seconds, default=2.0, metavar="SECONDS")
    watch.add_argument("--timeout", type=_positive_seconds, default=300.0, metavar="SECONDS")

    events = deploy_commands.add_parser("events", help="Read deployment lifecycle events.")
    events.add_argument("--project-id", required=True)
    events.add_argument("--deployment-id", required=True)
    events.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    events.add_argument("--cursor")

    logs = deploy_commands.add_parser("logs", help="Read bounded logs from deployment Pods.")
    logs.add_argument("--project-id", required=True)
    logs.add_argument("--deployment-id", required=True)
    logs.add_argument("--container", default="app")
    logs.add_argument("--since-seconds", type=_bounded_integer(1, 86_400))
    logs.add_argument("--tail-lines", type=_bounded_integer(1, 1000), default=100)

    kubernetes_events = deploy_commands.add_parser(
        "kubernetes-events",
        help="Read Kubernetes Events associated with deployment Pods.",
    )
    kubernetes_events.add_argument("--project-id", required=True)
    kubernetes_events.add_argument("--deployment-id", required=True)
    return parser


def _print_text(value: Any, indent: int = 0) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            print(f"{prefix}(none)")
            return
        for key in sorted(value):
            item = value[key]
            label = key.replace("_", " ").capitalize()
            if isinstance(item, (dict, list)):
                print(f"{prefix}{label}:")
                _print_text(item, indent + 2)
            else:
                print(f"{prefix}{label}: {item}")
        return
    if isinstance(value, list):
        if not value:
            print(f"{prefix}(none)")
            return
        for index, item in enumerate(value, start=1):
            if isinstance(item, (dict, list)):
                print(f"{prefix}{index}.")
                _print_text(item, indent + 2)
            else:
                print(f"{prefix}- {item}")
        return
    print(f"{prefix}{value}")


def _print_result(value: dict[str, Any] | list[Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        _print_text(value)


def _list_page(
    client: ApiClient,
    path: str,
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    params = {"limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    result = client.request("GET", f"{path}?{urlencode(params)}")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("items"), list)
        or "next_cursor" not in result
        or (
            result.get("next_cursor") is not None
            and not isinstance(result.get("next_cursor"), str)
        )
    ):
        raise CliError("MiniCloud API returned an invalid paginated response.")
    return result


def _wait_for_operation(
    client: ApiClient,
    deployment_id: str,
    *,
    poll_interval: float,
    timeout: float,
    output_format: str,
) -> int:
    deadline = time.monotonic() + timeout
    request_timeout = client.timeout
    latest_result: dict[str, Any] | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if latest_result is None:
                raise CliError("Timed out waiting for deployment status.")
            _print_result({"timed_out": True, "operation": latest_result}, output_format)
            return 1

        client.timeout = min(request_timeout, remaining)
        try:
            result = client.request(
                "GET",
                f"/api/v1/operations/{quote(deployment_id, safe='')}",
            )
        finally:
            client.timeout = request_timeout

        if not isinstance(result, dict):
            raise CliError("MiniCloud API returned an invalid operation response.")
        latest_result = result
        if result.get("status") == "failed" or result.get("job_state") == "failed":
            _print_result(result, output_format)
            return 1
        if result.get("status") == "ready":
            _print_result(result, output_format)
            return 0

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _print_result({"timed_out": True, "operation": latest_result}, output_format)
            return 1
        time.sleep(min(poll_interval, remaining))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        client = ApiClient(args.api_url)
        if args.resource == "register":
            password = getpass.getpass("Password: ")
            result = client.request(
                "POST",
                "/api/v1/auth/register",
                body={"email": args.register_email, "password": password},
                authenticated=False,
            )
            if not isinstance(result, dict) or not isinstance(result.get("user"), dict):
                raise CliError("MiniCloud API returned an invalid registration response.")
            _print_result({"registered": True, "user": result["user"]}, args.output)
            return 0

        if not args.email:
            raise CliError("Set --email or MINICLOUD_EMAIL to authenticate.", 2)
        password = getpass.getpass("Password: ")
        client.authenticate(args.email, password)

        if args.resource == "platform" and args.action == "capabilities":
            result = client.request("GET", "/api/v1/capabilities")
            if not isinstance(result, dict):
                raise CliError("MiniCloud API returned an invalid capabilities response.")
            _print_result(result, args.output)
        elif args.resource == "project" and args.action == "list":
            _print_result(
                _list_page(
                    client,
                    "/api/v1/projects",
                    limit=args.limit,
                    cursor=args.cursor,
                ),
                args.output,
            )
        elif args.resource == "project" and args.action == "create":
            _print_result(
                client.request(
                    "POST",
                    "/api/v1/projects",
                    body={"name": args.name, "slug": args.slug},
                ),
                args.output,
            )
        elif args.resource == "project" and args.action == "member":
            members_path = f"/api/v1/projects/{quote(args.project_id, safe='')}/members"
            if args.member_action == "list":
                result = client.request("GET", members_path)
                if not isinstance(result, list):
                    raise CliError("MiniCloud API returned an invalid project-members response.")
                _print_result(result, args.output)
            elif args.member_action == "add":
                result = client.request(
                    "POST",
                    members_path,
                    body={"email": args.member_email, "role": args.role},
                )
                if not isinstance(result, dict):
                    raise CliError("MiniCloud API returned an invalid project-member response.")
                _print_result(result, args.output)
            elif args.member_action == "role":
                result = client.request(
                    "PATCH",
                    f"{members_path}/{quote(args.user_id, safe='')}",
                    body={"role": args.role},
                )
                if not isinstance(result, dict):
                    raise CliError("MiniCloud API returned an invalid project-member response.")
                _print_result(result, args.output)
            elif args.member_action == "remove":
                result = client.request(
                    "DELETE",
                    f"{members_path}/{quote(args.user_id, safe='')}",
                    allow_empty_response=True,
                )
                if result is not None:
                    raise CliError("MiniCloud API returned an invalid member-removal response.")
                _print_result({"removed": True, "user_id": args.user_id}, args.output)
        elif args.resource == "deploy" and args.action == "list":
            path = f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments"
            _print_result(
                _list_page(client, path, limit=args.limit, cursor=args.cursor),
                args.output,
            )
        elif args.resource == "deploy" and args.action == "create":
            body: dict[str, Any] = {
                "name": args.name,
                "image": args.image,
                "port": args.port,
                "replicas": args.replicas,
            }
            if args.idempotency_key:
                body["idempotency_key"] = args.idempotency_key
            result = client.request(
                "POST",
                f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments",
                body=body,
            )
            if not isinstance(result, dict) or not isinstance(result.get("deployment_id"), str):
                raise CliError("MiniCloud API returned an invalid deployment response.")
            return _wait_for_operation(
                client,
                result["deployment_id"],
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                output_format=args.output,
            )
        elif args.resource == "deploy" and args.action == "scale":
            result = client.request(
                "PATCH",
                (
                    f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                    f"{quote(args.deployment_id, safe='')}/scale"
                ),
                body={
                    "replicas": args.replicas,
                    "expected_replicas": args.expected_replicas,
                    "idempotency_key": args.idempotency_key,
                },
            )
            if not isinstance(result, dict) or result.get("deployment_id") != args.deployment_id:
                raise CliError("MiniCloud API returned an invalid scale-operation response.")
            return _wait_for_operation(
                client,
                args.deployment_id,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                output_format=args.output,
            )
        elif args.resource == "deploy" and args.action == "revisions":
            path = (
                f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                f"{quote(args.deployment_id, safe='')}/revisions"
            )
            _print_result(
                _list_page(client, path, limit=args.limit, cursor=args.cursor),
                args.output,
            )
        elif args.resource == "deploy" and args.action == "rollback":
            result = client.request(
                "POST",
                (
                    f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                    f"{quote(args.deployment_id, safe='')}/rollback"
                ),
                body={
                    "revision_id": args.revision_id,
                    "expected_revision_id": args.expected_revision_id,
                    "idempotency_key": args.idempotency_key,
                },
            )
            if not isinstance(result, dict) or result.get("deployment_id") != args.deployment_id:
                raise CliError("MiniCloud API returned an invalid rollback-operation response.")
            return _wait_for_operation(
                client,
                args.deployment_id,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                output_format=args.output,
            )
        elif args.resource == "deploy" and args.action == "status":
            result = client.request(
                "GET",
                f"/api/v1/operations/{quote(args.deployment_id, safe='')}",
            )
            if not isinstance(result, dict):
                raise CliError("MiniCloud API returned an invalid operation response.")
            _print_result(result, args.output)
            if result.get("status") == "failed" or result.get("job_state") == "failed":
                return 1
        elif args.resource == "deploy" and args.action == "watch":
            return _wait_for_operation(
                client,
                args.deployment_id,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                output_format=args.output,
            )
        elif args.resource == "deploy" and args.action == "events":
            query: dict[str, str | int] = {"limit": args.limit}
            if args.cursor:
                query["cursor"] = args.cursor
            path = (
                f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                f"{quote(args.deployment_id, safe='')}/events?{urlencode(query)}"
            )
            _print_result(client.request("GET", path), args.output)
        elif args.resource == "deploy" and args.action == "logs":
            query: dict[str, str | int] = {
                "container": args.container,
                "tail_lines": args.tail_lines,
            }
            if args.since_seconds is not None:
                query["since_seconds"] = args.since_seconds
            path = (
                f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                f"{quote(args.deployment_id, safe='')}/logs?{urlencode(query)}"
            )
            _print_result(client.request("GET", path), args.output)
        elif args.resource == "deploy" and args.action == "kubernetes-events":
            path = (
                f"/api/v1/projects/{quote(args.project_id, safe='')}/deployments/"
                f"{quote(args.deployment_id, safe='')}/kubernetes-events"
            )
            _print_result(client.request("GET", path), args.output)
        else:
            raise CliError("Unsupported command.")
        return 0
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
