from __future__ import annotations

from app.core.models import AuditEvent
from sqlalchemy.orm import Session


def add_audit_event(
    db: Session,
    *,
    actor_user_id: str,
    project_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details={} if details is None else details,
        )
    )
