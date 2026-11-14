from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]


def test_compose_published_development_ports_bind_loopback():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    expected = {
        "postgres": "127.0.0.1:5433:5432",
        "api": "127.0.0.1:8001:8000",
        "web": "127.0.0.1:3001:3000",
    }
    for service, binding in expected.items():
        assert compose["services"][service]["ports"] == [binding]


def test_compose_migrations_are_explicit_and_not_an_api_startup_dependency():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    assert compose["services"]["migrate"]["profiles"] == ["migration"]
    assert "migrate" not in compose["services"]["api"].get("depends_on", {})


def test_compose_uses_distinct_api_and_worker_database_urls():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    assert compose["services"]["api"]["environment"]["DATABASE_URL"] == (
        "${MINICLOUD_API_DATABASE_URL:-}"
    )
    assert compose["services"]["worker"]["environment"]["DATABASE_URL"] == (
        "${MINICLOUD_WORKER_DATABASE_URL:-}"
    )
    assert compose["services"]["migrate"]["environment"]["DATABASE_URL"] == (
        "${MINICLOUD_API_DATABASE_URL:-}"
    )


def test_compose_defaults_to_fail_closed_production_environment():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    for service in ("migrate", "api", "worker", "web"):
        assert compose["services"][service]["environment"]["APP_ENV"] == (
            "${APP_ENV:-production}"
        )


def test_worker_healthcheck_fails_when_database_dependency_is_unavailable():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    worker = compose["services"]["worker"]
    assert worker["environment"]["WORKER_DATABASE_STATUS_FILE"] == (
        "/tmp/minicloud-worker-db-blocked"
    )
    assert "/tmp/minicloud-worker-db-blocked" in " ".join(
        str(part) for part in worker["healthcheck"]["test"]
    )


def test_core_services_restart_and_web_healthcheck_is_http_based():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    for service in ("postgres", "api", "worker", "web"):
        assert compose["services"][service]["restart"] == "unless-stopped"

    web_healthcheck = compose["services"]["web"]["healthcheck"]
    assert web_healthcheck["test"][:2] == ["CMD", "node"]
    assert "127.0.0.1:3000" in " ".join(web_healthcheck["test"])


def test_api_healthcheck_uses_ipv4_loopback_and_bounded_readiness_probe():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    healthcheck = compose["services"]["api"]["healthcheck"]
    command = " ".join(str(part) for part in healthcheck["test"])
    assert healthcheck["test"][0] == "CMD-SHELL"
    assert "127.0.0.1:8000/readyz" in command
    assert "timeout=4" in command


def test_worker_runs_as_pid_one_without_a_signal_swallowing_shell():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    )

    assert compose["services"]["worker"]["command"] == [
        "python",
        "services/worker/main.py",
    ]


def test_web_image_installs_the_locked_dependency_graph():
    dockerfile = (ROOT / "infra/docker/web.Dockerfile").read_text(encoding="utf-8")

    assert "COPY --chown=node:node apps/web/package.json apps/web/package-lock.json" in dockerfile
    assert "npm@11.6.2" in dockerfile
    assert "npm ci" in dockerfile
    assert "RUN npm run build && npm prune --omit=dev --omit=peer" in dockerfile
    assert "FROM node:22-alpine@" in dockerfile
    assert "AS build" in dockerfile
    assert "AS runtime" in dockerfile
    assert "RUN cd /workspace/apps/web && npm install" not in dockerfile
    assert "USER node" in dockerfile
    assert 'CMD ["npm", "run", "start"' in dockerfile


def test_demo_compose_uses_non_candidate_image_tags():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")
    )

    assert compose["services"]["api"]["image"] == "minicloud-resume-api:local"
    assert compose["services"]["web"]["image"] == "minicloud-resume-web:local"
    assert compose["services"]["api"]["image"] != "minicloud-api:mc-final-20261114-r19"
    assert compose["services"]["web"]["image"] != "minicloud-web:mc-final-20261114-r20"


def test_api_and_worker_images_use_a_non_root_runtime_user():
    for service in ("api", "worker"):
        dockerfile = (ROOT / f"infra/docker/{service}.Dockerfile").read_text(
            encoding="utf-8"
        )

        assert "useradd --system --uid 10001" in dockerfile
        assert "USER minicloud" in dockerfile


def test_ci_checks_isolated_postgres_full_frontend_audit_and_images():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "branches: [ main, master ]" in workflow
    assert "scripts/Test-MiniCloudPostgresIntegration.ps1" in workflow
    assert "npm audit --audit-level=moderate" in workflow
    assert "docker compose build api worker web" in workflow


def test_env_example_uses_placeholders_and_git_ignores_real_env():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "\n.env\n" in f"\n{gitignore}\n"
    assert "!.env.example" in gitignore
    for name in (
        "JWT_SECRET_KEY",
        "POSTGRES_ADMIN_PASSWORD",
        "MINICLOUD_API_DATABASE_URL",
        "MINICLOUD_WORKER_DATABASE_URL",
        "MINICLOUD_MIGRATION_DATABASE_URL",
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
    ):
        value = next(
            line.split("=", 1)[1]
            for line in env_example.splitlines()
            if line.startswith(f"{name}=")
        )
        assert "replace-with-" in value

    assert "minicloud_api_runtime:" in env_example
    assert "minicloud_worker_runtime:" in env_example


def test_docker_build_context_excludes_local_worker_credentials():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".local" in dockerignore
    assert "**/.local" in dockerignore


def test_postgresql_integration_runner_does_not_mount_the_host_worktree():
    runner = (ROOT / "scripts/Test-MiniCloudPostgresIntegration.ps1").read_text(
        encoding="utf-8"
    )

    assert '"--mount"' not in runner
    assert "type=bind,source=$repositoryRoot" not in runner


def test_postgresql_integration_errors_do_not_echo_docker_arguments():
    runner = (ROOT / "scripts/Test-MiniCloudPostgresIntegration.ps1").read_text(
        encoding="utf-8"
    )

    assert 'Docker command failed with exit code $exitCode.' in runner
    assert '$Arguments -join' not in runner
