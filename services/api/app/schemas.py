from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.resource_policy_constants import MAX_PROJECT_REPLICAS


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class UserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str


class TokenOut(BaseModel):
    user: UserOut
    access_token: str
    expires_in: int


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=120)

    @field_validator("name", "slug", mode="before")
    @classmethod
    def strip_project_fields(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("slug")
    @classmethod
    def normalize_project_slug(cls, value: str) -> str:
        return value.lower()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def strip_project_name(cls, value: object) -> object:
        return _strip_text(value)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: str
    owner_id: str


class ProjectPageOut(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None


class ProjectQuotaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_replicas: int = Field(..., ge=1, le=MAX_PROJECT_REPLICAS)
    cpu_limit: str = Field(..., min_length=1, max_length=32)
    memory_limit: str = Field(..., min_length=1, max_length=32)

    @field_validator("cpu_limit", "memory_limit", mode="before")
    @classmethod
    def strip_quantities(cls, value: object) -> object:
        return _strip_text(value)


class ProjectQuotaDesired(BaseModel):
    max_replicas: int
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


class ProjectQuotaOut(BaseModel):
    state: Literal["unconfigured", "pending", "applied"]
    version: int | None
    desired: ProjectQuotaDesired | None
    applied_version: int | None
    updated_at: datetime | None


class ProjectMemberCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: Literal["owner", "member"] = "member"

    @field_validator("email", mode="before")
    @classmethod
    def normalize_member_email(cls, value: object) -> object:
        normalized = _strip_text(value)
        if isinstance(normalized, str):
            return normalized.lower()
        return normalized

    @field_validator("email")
    @classmethod
    def validate_member_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("A valid email address is required")
        return value


class ProjectMemberRoleUpdate(BaseModel):
    role: Literal["owner", "member"]


class ProjectMemberOut(BaseModel):
    user_id: str
    email: str
    role: str


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=120)
    image: str = Field(..., min_length=3, max_length=255)
    port: int = Field(default=8080, ge=1, le=65535)
    replicas: int = Field(default=1, ge=1, le=10)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def strip_idempotency_key(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("name")
    @classmethod
    def validate_deployment_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value):
            raise ValueError("Deployment name must use lowercase letters, digits, and hyphens")
        return value

    @field_validator("image")
    @classmethod
    def validate_image_reference(cls, value: str) -> str:
        image_pattern = (
            r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)?"
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
            r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
            r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
            r"(?:@sha256:[a-f0-9]{64})?$"
        )
        if not re.fullmatch(image_pattern, value):
            raise ValueError("Image must be a valid container image reference")
        return value


class DeploymentScale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replicas: int = Field(..., ge=1, le=10)
    expected_replicas: int = Field(..., ge=1, le=10)
    idempotency_key: str = Field(..., min_length=1, max_length=128)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def strip_idempotency_key(cls, value: object) -> object:
        return _strip_text(value)


class DeploymentRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str = Field(..., min_length=36, max_length=36)
    expected_revision_id: str = Field(..., min_length=36, max_length=36)
    idempotency_key: str = Field(..., min_length=1, max_length=128)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def strip_idempotency_key(cls, value: object) -> object:
        return _strip_text(value)


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    owner_id: str
    name: str
    image: str
    port: int
    replicas: int
    kubernetes_resource_name: str
    status: str
    last_error: str | None = None


class DeploymentPageOut(BaseModel):
    items: list[DeploymentOut]
    next_cursor: str | None


class OperationOut(BaseModel):
    deployment_id: str
    kubernetes_resource_name: str
    revision_id: str | None = None
    job_state: str | None = None
    status: str
    last_error: str | None = None


class DeploymentRevisionOut(BaseModel):
    id: str
    revision_number: int
    name: str
    image: str
    port: int
    replicas: int
    created_by_user_id: str
    rollback_of_revision_id: str | None
    created_at: datetime


class DeploymentRevisionPageOut(BaseModel):
    items: list[DeploymentRevisionOut]
    next_cursor: str | None


class DeploymentEventOut(BaseModel):
    id: str
    event_type: str
    message: str
    created_at: datetime


class DeploymentEventPageOut(BaseModel):
    items: list[DeploymentEventOut]
    next_cursor: str | None


class KubernetesPodLogOut(BaseModel):
    pod_name: str
    container: str
    log: str
    truncated: bool


class KubernetesPodLogsOut(BaseModel):
    items: list[KubernetesPodLogOut]
    source_time: datetime
    truncated: bool


class KubernetesEventOut(BaseModel):
    resource_kind: str
    resource_name: str
    event_type: str
    reason: str | None
    message: str
    created_at: datetime


class KubernetesEventPageOut(BaseModel):
    items: list[KubernetesEventOut]
    source_time: datetime
    truncated: bool
