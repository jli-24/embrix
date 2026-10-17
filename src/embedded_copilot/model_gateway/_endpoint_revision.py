"""Pure, stable endpoint projection identity shared with production composition."""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, cast


class _EndpointProjection(Protocol):
    endpoint_id: str
    provider_id: str
    protocol_family: object
    base_url: str
    credential_ref: str | None
    enabled: bool


def endpoint_revision(endpoint: object) -> str:
    endpoint = cast(_EndpointProjection, endpoint)
    value = {
        "endpoint_id": endpoint.endpoint_id,
        "provider_id": endpoint.provider_id,
        "protocol_family": getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
        "base_url": endpoint.base_url,
        "credential_ref": endpoint.credential_ref,
        "enabled": endpoint.enabled,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
