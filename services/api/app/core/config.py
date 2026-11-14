from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

INSECURE_PRODUCTION_SECRETS = {
    "change-me",
    "changeme",
    "dev-minicloud-jwt-secret-key-2026",
    "password",
    "replace-me",
    "secret",
    "test",
}


class Settings(BaseSettings):
    app_env: str = "development"
    service_role: Literal["api", "worker", "migrate"] = "api"
    app_name: str = "MiniCloud"
    app_debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8001
    frontend_url: str = "http://localhost:3001"
    database_url: str = Field(default="", repr=False, exclude=True)
    jwt_secret_key: str | None = Field(default=None, repr=False, exclude=True)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = 60
    cors_origins: str = "http://localhost:3001"
    log_level: str = "INFO"
    worker_interval_seconds: int = 10
    kubernetes_in_cluster: bool = False
    kubernetes_config_file: str | None = None
    kubernetes_context: str | None = None
    kubernetes_namespace: str = "minicloud"
    kubernetes_rollout_timeout_seconds: int = 600
    kubernetes_diagnostics_in_cluster: bool = False
    kubernetes_diagnostics_config_file: str | None = None
    kubernetes_diagnostics_context: str | None = None
    enable_metrics: bool = False
    migration_database_url: str | None = Field(default=None, repr=False, exclude=True)
    auth_rate_limit_max_requests: int = 5
    auth_rate_limit_window_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_database_url(cls, value: str | None, info: ValidationInfo) -> str | None:
        setting_name = (info.field_name or "database_url").upper()
        if value is None and info.field_name == "migration_database_url":
            if info.data.get("service_role") == "migrate":
                raise ValueError("MIGRATION_DATABASE_URL must be set for the migration service")
            return None
        if not value or not value.strip():
            raise ValueError(f"{setting_name} must be set")
        try:
            parsed = make_url(value)
        except (ArgumentError, TypeError, ValueError):
            raise ValueError(f"{setting_name} must be a valid SQLAlchemy URL") from None
        password = parsed.password.strip() if parsed.password else None
        if password and password.casefold().startswith("replace-with-"):
            raise ValueError(f"{setting_name} must not use a placeholder credential")
        if parsed.drivername not in {
            "postgresql",
            "postgresql+psycopg",
            "sqlite",
            "sqlite+pysqlite",
        }:
            raise ValueError(f"{setting_name} uses an unsupported database driver")
        app_env = info.data.get("app_env", "development")
        if (
            app_env not in {"development", "test"}
            and password is not None
            and (
                password.casefold() in INSECURE_PRODUCTION_SECRETS
                or password.casefold() in {"admin", "postgres", "root", "123456"}
                or len(password) < 16
                or len(set(password)) < 8
            )
        ):
            raise ValueError(f"{setting_name} must not use a weak database credential")
        if app_env not in {"development", "test"} and parsed.drivername.startswith("sqlite"):
            raise ValueError(f"{setting_name} must use PostgreSQL outside development and test")
        if (
            app_env not in {"development", "test"}
            and info.data.get("service_role") in {"api", "worker"}
            and parsed.username in {"postgres", "root"}
        ):
            raise ValueError(f"{setting_name} must use a non-administrator runtime role")
        service_role = info.data.get("service_role")
        expected_runtime_user = (
            "minicloud_api_runtime"
            if service_role == "api"
            else "minicloud_worker_runtime"
            if service_role == "worker"
            else None
        )
        if (
            app_env not in {"development", "test"}
            and expected_runtime_user
            and parsed.username != expected_runtime_user
        ):
            raise ValueError(f"{setting_name} must use the dedicated {service_role} database role")
        if (
            app_env not in {"development", "test"}
            and info.field_name == "migration_database_url"
            and info.data.get("service_role") == "migrate"
        ):
            runtime_url = make_url(info.data["database_url"])
            if parsed.username == runtime_url.username:
                raise ValueError(
                    "MIGRATION_DATABASE_URL must use credentials distinct from DATABASE_URL"
                )
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, value: str | None, info: ValidationInfo) -> str | None:
        service_role = info.data.get("service_role", "api")
        if value is None or not value.strip():
            if service_role != "api":
                return None
            raise ValueError("JWT_SECRET_KEY must be set for the API service")

        cleaned = value.strip()
        if len(cleaned) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 characters")
        if cleaned.casefold() in INSECURE_PRODUCTION_SECRETS:
            raise ValueError("JWT_SECRET_KEY must not use a known insecure default")
        if cleaned.casefold().startswith("replace-with-"):
            raise ValueError("JWT_SECRET_KEY must not use a placeholder value")
        if len(set(cleaned)) < 8:
            raise ValueError("JWT_SECRET_KEY must not use a low-variation value")
        return cleaned

    @field_validator("kubernetes_namespace")
    @classmethod
    def validate_kubernetes_namespace(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value) or len(value) > 63:
            raise ValueError(
                "KUBERNETES_NAMESPACE must be a valid lowercase DNS label of at most 63 characters"
            )
        return value

    @field_validator("kubernetes_rollout_timeout_seconds")
    @classmethod
    def validate_rollout_timeout(cls, value: int) -> int:
        if not 30 <= value <= 3600:
            raise ValueError("KUBERNETES_ROLLOUT_TIMEOUT_SECONDS must be between 30 and 3600")
        return value

    @field_validator("access_token_expire_minutes")
    @classmethod
    def validate_access_token_expiry(cls, value: int) -> int:
        if not 1 <= value <= 1440:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 1440")
        return value

    @field_validator("auth_rate_limit_max_requests")
    @classmethod
    def validate_auth_rate_limit_max(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("AUTH_RATE_LIMIT_MAX_REQUESTS must be between 1 and 1000")
        return value

    @field_validator("auth_rate_limit_window_seconds")
    @classmethod
    def validate_auth_rate_limit_window(cls, value: int) -> int:
        if not 1 <= value <= 3600:
            raise ValueError("AUTH_RATE_LIMIT_WINDOW_SECONDS must be between 1 and 3600")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        names = sorted(
            {".".join(str(part) for part in error["loc"]).upper() for error in exc.errors()}
        )
        raise RuntimeError(f"Invalid configuration for setting(s): {', '.join(names)}") from None


settings = load_settings()
