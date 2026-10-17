from __future__ import annotations

from dataclasses import dataclass

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)


@dataclass(frozen=True, slots=True)
class EngineeringCorrelationFinding:
    finding_type: str
    project_id: str
    evidence_ids: tuple[str, ...]
    summary: str
    confidence: float
    missing_evidence: tuple[str, ...]
    validation_steps: tuple[str, ...]


class EngineeringCorrelationService:
    """Deterministically correlates parser evidence without runtime claims."""

    def correlate(
        self,
        *,
        firmware: tuple[EngineeringContextEvidence, ...],
        debug: tuple[EngineeringContextEvidence, ...],
    ) -> tuple[EngineeringCorrelationFinding, ...]:
        if not isinstance(firmware, tuple) or not isinstance(debug, tuple):
            raise TypeError("correlation inputs must be tuples")
        values = (*firmware, *debug)
        if any(item.tenant_id != values[0].tenant_id for item in values) if values else False:
            raise ValueError("correlation tenant mismatch")
        if any(item.project_id != values[0].project_id for item in values) if values else False:
            raise ValueError("correlation project mismatch")
        if not firmware or not debug:
            return ()
        firmware_text = " ".join(
            f"{item.subject} {item.summary} {item.fact_value or ''}" for item in firmware
        ).casefold()
        debug_text = " ".join(
            f"{item.subject} {item.summary} {item.fact_value or ''}" for item in debug
        ).casefold()
        if "sensor_read" not in firmware_text or "hal_i2c_master_transmit" not in firmware_text:
            return ()
        if "hal_timeout" not in debug_text and "hal timeout" not in debug_text:
            return ()
        ids = tuple(item.evidence_id for item in (*firmware, *debug))
        return (
            EngineeringCorrelationFinding(
                finding_type="ROOT_CAUSE_CANDIDATE",
                project_id=firmware[0].project_id,
                evidence_ids=ids,
                summary="sensor_read contains an I2C transmit edge and the debug artifact reports HAL_TIMEOUT; the transaction may be timing out.",
                confidence=0.65,
                missing_evidence=("runtime call trace", "SDA/SCL waveform", "ACK/NACK observation", "peripheral register state"),
                validation_steps=("capture the failing transaction", "check address and ACK/NACK", "inspect HAL/peripheral timeout status"),
            ),
        )


__all__ = ("EngineeringCorrelationFinding", "EngineeringCorrelationService")
