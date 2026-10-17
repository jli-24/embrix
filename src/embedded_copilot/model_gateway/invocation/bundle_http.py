"""Private raw generation-failure carrier; never included in API diagnostics."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from .budget import digest, wire_target_digest


@dataclass(frozen=True)
class GenerationHTTPFailure:
    http_status: int
    request_sha256: str
    wire_target_sha256: str
    raw_response: bytes

    def material(self):
        if (
            type(self.http_status) is not int
            or not 100 <= self.http_status <= 599
            or type(self.raw_response) is not bytes
        ):
            raise ValueError("generation HTTP observation invalid")
        from ..contracts import fingerprint

        fingerprint(self.request_sha256)
        fingerprint(self.wire_target_sha256)
        return {
            "schema_version": 2,
            "kind": "GENERATION_HTTP_FAILURE",
            "http_status": self.http_status,
            "request_sha256": self.request_sha256,
            "wire_target_sha256": self.wire_target_sha256,
            "raw_response_base64": base64.b64encode(self.raw_response).decode("ascii"),
            "raw_response_sha256": "sha256:"
            + hashlib.sha256(self.raw_response).hexdigest(),
        }


def capture_http_failure(error, payload, response, wire_target):
    # The exact adapter holds the actual request bytes and HTTP response here.
    value = GenerationHTTPFailure(
        response.status_code,
        digest(payload),
        wire_target_digest(wire_target),
        response.content,
    )
    value.material()
    error._bundle_http_failure = value


def observed_http_failure(error):
    seen = set()
    for _ in range(8):
        if error is None or id(error) in seen:
            return None
        seen.add(id(error))
        value = getattr(error, "_bundle_http_failure", None)
        if type(value) is GenerationHTTPFailure:
            value.material()
            return value
        error = error.__cause__
    return None
