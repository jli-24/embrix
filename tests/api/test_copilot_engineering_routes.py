from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_engineering_routes import router


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}


class _FirmwarePort:
    def analyze(self, *, tenant_id: str, request) -> dict[str, object]:
        assert tenant_id == "tenant-1"
        return {
            "project_id": request.project_id,
            "facts": ("observed function i2c_init",),
            "evidence": (
                {
                    "evidence_id": "firmware-evidence-1",
                    "level": "OBSERVATION",
                    "summary": "observed parsed function i2c_init",
                    "source_reference": "firmware/main.c",
                    "source_fingerprint": "sha256:" + "1" * 64,
                    "confidence": 1.0,
                },
            ),
            "recommendations": ("inspect clock and pull-up assumptions",),
            "confidence": 0.72,
            "limitations": ("hardware waveform is unavailable",),
            "source_fingerprint": "sha256:" + "2" * 64,
        }


class _DebugPort:
    def analyze(self, *, tenant_id: str, request) -> dict[str, object]:
        return {
            "project_id": request.project_id,
            "failure_reference": request.failure_reference,
            "root_cause_candidates": (
                {
                    "evidence_id": "debug-candidate-1",
                    "level": "CANDIDATE",
                    "summary": "I2C timeout may be related to clock configuration",
                    "source_reference": "debug:failure-1",
                    "source_fingerprint": "sha256:" + "3" * 64,
                    "confidence": 0.72,
                    "needs_more_evidence": ("logic analyzer waveform",),
                },
            ),
            "confidence": 0.72,
            "needs_more_evidence": ("logic analyzer waveform",),
            "recommendations": ("verify SDA/SCL pull-ups and bus speed",),
        }


def _app(*, firmware=None, debug=None) -> FastAPI:
    app = FastAPI()
    app.state.copilot_firmware_analysis_port = firmware
    app.state.copilot_debug_analysis_port = debug

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    app.include_router(router)
    return app


def test_firmware_and_debug_are_read_only_evidence_projections() -> None:
    with TestClient(_app(firmware=_FirmwarePort(), debug=_DebugPort())) as client:
        firmware = client.post(
            "/api/v1/copilot/firmware/analyze",
            headers=HEADERS,
            json={"project_id": "project-1", "question": "Where is I2C init?"},
        )
        debug = client.post(
            "/api/v1/copilot/debug/analyze",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "failure_reference": "failure-1",
                "failure_type": "I2C_TIMEOUT",
                "summary": "I2C transfer timed out",
            },
        )

    assert firmware.status_code == 200
    assert firmware.json()["evidence"][0]["level"] == "OBSERVATION"
    assert "content" not in firmware.text
    assert debug.status_code == 200
    assert debug.json()["root_cause_candidates"][0]["level"] == "CANDIDATE"


def test_analysis_requires_port_identity_and_rejects_cross_project_result() -> None:
    with TestClient(_app()) as client:
        unavailable = client.post(
            "/api/v1/copilot/firmware/analyze",
            headers=HEADERS,
            json={"project_id": "project-1", "question": "I2C"},
        )
        missing_identity = client.post(
            "/api/v1/copilot/debug/analyze",
            headers={"X-Environment-Tenant": "tenant-1"},
            json={
                "project_id": "project-1",
                "failure_reference": "failure-1",
                "failure_type": "HARDFAULT",
                "summary": "fault",
            },
        )

    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "FIRMWARE_ANALYSIS_UNAVAILABLE"
    assert missing_identity.status_code == 401
