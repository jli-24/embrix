from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)


_SECRET = re.compile(r"(?i)(api[_ -]?key|token|password|secret|credential)\s*[:=]\s*[^\s,;]+")
_REGISTER = re.compile(r"\b(PC|LR|SP|xPSR|CFSR|HFSR|MMFAR|BFAR)\s*[:=]\s*(0x[0-9a-fA-F]+|[0-9]+)")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{4,16}")
_SYMBOL = re.compile(r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*\+\s*0x[0-9a-fA-F]+")


@dataclass(frozen=True, slots=True)
class DebugIntelligenceResult:
    evidence: tuple[EngineeringContextEvidence, ...]
    observations: tuple[str, ...]
    root_cause_candidates: tuple[str, ...]
    confidence: float
    missing_evidence: tuple[str, ...]
    validation_steps: tuple[str, ...]


def _fp(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe(value: str) -> str:
    value = _SECRET.sub("[REDACTED]", value)
    return value[:512]


class DebugIntelligenceService:
    """Read-only, bounded debug evidence normalizer."""

    def __init__(self, *, max_bytes: int = 100_000, max_lines: int = 2_000) -> None:
        if max_bytes < 1 or max_lines < 1:
            raise ValueError("debug parser limits must be positive")
        self._max_bytes = max_bytes
        self._max_lines = max_lines

    def analyze(
        self,
        *,
        tenant_id: str,
        project_id: str,
        artifact_id: str,
        source: str,
        source_reference: str,
        source_fingerprint: str | None = None,
    ) -> DebugIntelligenceResult:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("debug source is empty")
        if len(source.encode("utf-8")) > self._max_bytes:
            raise ValueError("debug source exceeds bounded size")
        lines = source.splitlines()
        if len(lines) > self._max_lines:
            raise ValueError("debug source exceeds bounded line count")
        safe_lines = tuple(_safe(line.strip()) for line in lines if line.strip())
        joined = "\n".join(safe_lines)
        source_fp = source_fingerprint or _fp(joined)
        observations: list[str] = []
        candidates: list[str] = []
        missing: list[str] = []
        if "undefined reference" in joined.casefold():
            observations.append("linker reports an unresolved symbol")
            candidates.extend(("source file may be absent from linked objects", "conditional compilation may remove the symbol", "C/C++ linkage may differ"))
        if any(marker in joined.casefold() for marker in ("hardfault", "hard fault")):
            observations.append("STM32 HardFault signature is present")
            candidates.append("faulting memory or execution context requires register and mapping evidence")
            if not _REGISTER.search(joined):
                missing.append("CFSR/HFSR/PC/LR register evidence")
            if not _SYMBOL.search(joined):
                missing.append("ELF/MAP symbol mapping")
        if "guru meditation" in joined.casefold() or "panic" in joined.casefold():
            observations.append("ESP32 panic/Guru Meditation signature is present")
            candidates.append("panic address requires ELF/MAP symbol mapping before source attribution")
            if not _SYMBOL.search(joined):
                missing.append("ELF/MAP symbol mapping")
        if "hal_timeout" in joined.casefold() or "hal timeout" in joined.casefold():
            observations.append("HAL_TIMEOUT is directly observed")
            candidates.append("an I2C/SPI transaction may be timing out at the reported call site")
            missing.extend(("runtime call trace", "peripheral status registers or bus capture"))
        if not observations:
            observations.append("no supported debug signature was observed")
            missing.append("structured compiler, runtime, or register evidence")
        evidence = tuple(
            EngineeringContextEvidence.create(
                evidence_id=f"debug-{artifact_id}-{index}",
                tenant_id=tenant_id,
                project_id=project_id,
                source_type=ContextSourceType.DEBUG,
                subject=artifact_id,
                summary=observation,
                fact_key="debug.observation",
                fact_value=observation,
                source_reference=source_reference,
                source_fingerprint=source_fp,
                confidence=1.0,
                verification_status=ContextVerificationStatus.VERIFIED,
                trust_level=EvidenceTrustLevel.OBSERVATION,
                evidence_status=EvidenceVerificationStatus.OBSERVED,
                provenance=(artifact_id,),
            )
            for index, observation in enumerate(observations, start=1)
        )
        return DebugIntelligenceResult(
            evidence=evidence,
            observations=tuple(observations),
            root_cause_candidates=tuple(dict.fromkeys(candidates)),
            confidence=0.6 if candidates else 0.2,
            missing_evidence=tuple(dict.fromkeys(missing)),
            validation_steps=("capture the earliest failing event", "provide ELF/MAP mapping", "reproduce with bounded diagnostic artifacts"),
        )


__all__ = ("DebugIntelligenceResult", "DebugIntelligenceService")
