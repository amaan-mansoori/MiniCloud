from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.core.models import ProjectResourcePolicy
from app.core.resource_policy_constants import (
    DEFAULT_CPU_LIMIT_MILLICORES,
    DEFAULT_MAX_REPLICAS,
    DEFAULT_MEMORY_LIMIT_BYTES,
    MAX_PROJECT_CPU_MILLICORES,
    MAX_PROJECT_MEMORY_BYTES,
    POD_CPU_LIMIT_MILLICORES,
    POD_CPU_REQUEST_MILLICORES,
    POD_MEMORY_LIMIT_BYTES,
    POD_MEMORY_REQUEST_BYTES,
)
from app.schemas import ProjectQuotaDesired, ProjectQuotaOut
from kubernetes.utils.quantity import parse_quantity
from sqlalchemy.orm import Session


class ProjectQuotaError(ValueError):
    def __init__(self, detail: str, *, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class InvalidProjectQuota(ValueError):
    pass


def parse_cpu_limit(value: str) -> int:
    try:
        quantity = Decimal(str(parse_quantity(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidProjectQuota("CPU limit must be a valid Kubernetes quantity") from exc

    millicores = quantity * 1000
    if millicores != millicores.to_integral_value():
        raise InvalidProjectQuota("CPU limit must be a whole number of millicores")
    result = int(millicores)
    if not POD_CPU_LIMIT_MILLICORES <= result <= MAX_PROJECT_CPU_MILLICORES:
        raise InvalidProjectQuota("CPU limit must be between 500m and 2 CPU")
    return result


def parse_memory_limit(value: str) -> int:
    try:
        quantity = Decimal(str(parse_quantity(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidProjectQuota("Memory limit must be a valid Kubernetes quantity") from exc

    if quantity != quantity.to_integral_value():
        raise InvalidProjectQuota("Memory limit must resolve to a whole number of bytes")
    result = int(quantity)
    if not POD_MEMORY_LIMIT_BYTES <= result <= MAX_PROJECT_MEMORY_BYTES:
        raise InvalidProjectQuota("Memory limit must be between 512Mi and 2Gi")
    return result


def validate_project_policy(
    *,
    max_replicas: int,
    cpu_limit_millicores: int,
    memory_limit_bytes: int,
) -> tuple[int, int]:
    required_cpu = max_replicas * POD_CPU_LIMIT_MILLICORES
    required_memory = max_replicas * POD_MEMORY_LIMIT_BYTES
    if cpu_limit_millicores < required_cpu:
        raise InvalidProjectQuota(
            "CPU limit is too small for the selected maximum replica count"
        )
    if memory_limit_bytes < required_memory:
        raise InvalidProjectQuota(
            "Memory limit is too small for the selected maximum replica count"
        )
    return (
        max_replicas * POD_CPU_REQUEST_MILLICORES,
        max_replicas * POD_MEMORY_REQUEST_BYTES,
    )


def format_cpu_quantity(millicores: int) -> str:
    if millicores % 1000 == 0:
        return str(millicores // 1000)
    return f"{millicores}m"


def format_memory_quantity(byte_count: int) -> str:
    gibibyte = 1024**3
    mebibyte = 1024**2
    if byte_count % gibibyte == 0:
        return f"{byte_count // gibibyte}Gi"
    if byte_count % mebibyte == 0:
        return f"{byte_count // mebibyte}Mi"
    return str(byte_count)


def new_default_project_policy(project_id: str, owner_user_id: str) -> ProjectResourcePolicy:
    cpu_request_millicores, memory_request_bytes = validate_project_policy(
        max_replicas=DEFAULT_MAX_REPLICAS,
        cpu_limit_millicores=DEFAULT_CPU_LIMIT_MILLICORES,
        memory_limit_bytes=DEFAULT_MEMORY_LIMIT_BYTES,
    )
    return ProjectResourcePolicy(
        project_id=project_id,
        max_replicas=DEFAULT_MAX_REPLICAS,
        cpu_request_millicores=cpu_request_millicores,
        cpu_limit_millicores=DEFAULT_CPU_LIMIT_MILLICORES,
        memory_request_bytes=memory_request_bytes,
        memory_limit_bytes=DEFAULT_MEMORY_LIMIT_BYTES,
        version=1,
        applied_version=None,
        updated_by_user_id=owner_user_id,
    )


def project_quota_out(policy: ProjectResourcePolicy | None) -> ProjectQuotaOut:
    if policy is None:
        return ProjectQuotaOut(
            state="unconfigured",
            version=None,
            desired=None,
            applied_version=None,
            updated_at=None,
        )

    desired = ProjectQuotaDesired(
        max_replicas=policy.max_replicas,
        cpu_request=format_cpu_quantity(policy.cpu_request_millicores),
        cpu_limit=format_cpu_quantity(policy.cpu_limit_millicores),
        memory_request=format_memory_quantity(policy.memory_request_bytes),
        memory_limit=format_memory_quantity(policy.memory_limit_bytes),
    )
    return ProjectQuotaOut(
        state="applied" if policy.applied_version == policy.version else "pending",
        version=policy.version,
        desired=desired,
        applied_version=policy.applied_version,
        updated_at=policy.updated_at,
    )


def enforce_project_replica_policy(
    db: Session,
    project_id: str,
    replicas: int,
) -> None:
    policy = (
        db.query(ProjectResourcePolicy)
        .filter(ProjectResourcePolicy.project_id == project_id)
        .with_for_update()
        .one_or_none()
    )
    if policy is None:
        raise ProjectQuotaError(
            "Project resource policy is not configured; an owner must set a quota first",
            status_code=409,
        )

    required_cpu = replicas * POD_CPU_LIMIT_MILLICORES
    required_memory = replicas * POD_MEMORY_LIMIT_BYTES
    if (
        replicas > policy.max_replicas
        or required_cpu > policy.cpu_limit_millicores
        or required_memory > policy.memory_limit_bytes
    ):
        raise ProjectQuotaError(
            "Requested replicas exceed the project's configured CPU, memory, or replica quota",
            status_code=422,
        )
