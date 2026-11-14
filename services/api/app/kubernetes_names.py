from __future__ import annotations

import re


def deployment_resource_name(deployment_id: str) -> str:
    compact_id = re.sub(r"[^a-z0-9]", "", deployment_id.lower())
    if len(compact_id) < 8:
        raise ValueError("Deployment ID must contain at least eight alphanumeric characters.")
    return f"mc-{compact_id[:24]}"
