from __future__ import annotations

import uuid
from datetime import UTC, datetime


def encode_created_at_cursor(created_at: datetime, resource_id: str) -> str:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return f"{created_at.isoformat()}|{resource_id}"


def decode_created_at_cursor(cursor: str) -> tuple[datetime, str]:
    timestamp, resource_id = cursor.split("|", 1)
    created_at = datetime.fromisoformat(timestamp)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(UTC), str(uuid.UUID(resource_id))
