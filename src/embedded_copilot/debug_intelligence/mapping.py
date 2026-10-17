from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)


_HEX = re.compile(r"^0x[0-9a-fA-F]+$")
_MAP_LINE = re.compile(
    r"^(?P<start>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s+"
    r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<source>[A-Za-z0-9._/+-]+):(?P<line>[1-9][0-9]*)\s+"
    r"(?P<source_fp>sha256:[0-9a-f]{64})$"
)


@dataclass(frozen=True, slots=True)
class HardFaultMappingResult:
    evidence: tuple[EngineeringContextEvidence, ...]
    status: str
    missing_evidence: tuple[str, ...]


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_lookup_key(source_reference: str, source_fingerprints: dict[str, str]) -> str | None:
    """Resolve common MAP project-prefix notation to the bound source identity."""
    path = PurePosixPath(source_reference)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    candidates = (source_reference, source_reference.removeprefix("firmware/"))
    return next((candidate for candidate in candidates if candidate in source_fingerprints), None)


class HardFaultMappingService:
    """Resolve a HardFault PC through bounded, project-bound MAP metadata."""

    def map(
        self,
        *,
        tenant_id: str,
        project_id: str,
        artifact_id: str,
        pc: str,
        map_text: str | None,
        source_fingerprints: dict[str, str],
        map_source_reference: str,
        elf_metadata: str | None = None,
    ) -> HardFaultMappingResult:
        if not isinstance(map_text, str) or not map_text.strip():
            return HardFaultMappingResult((), "INSUFFICIENT_MAPPING_EVIDENCE", ("ELF/MAP symbol mapping",))
        if len(map_text.encode("utf-8")) > 1_000_000:
            return HardFaultMappingResult((), "MAPPING_TOO_LARGE", ("bounded MAP metadata",))
        if not isinstance(pc, str) or _HEX.fullmatch(pc.strip()) is None:
            return HardFaultMappingResult((), "MALFORMED_HARDFAULT", ("valid PC register evidence",))
        address = int(pc, 16)
        matches = []
        for raw_line in map_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _MAP_LINE.fullmatch(line)
            if match is None:
                continue
            start = int(match.group("start"), 16)
            size = int(match.group("size"), 16)
            if start <= address < start + size:
                matches.append(match)
        if not matches:
            return HardFaultMappingResult((), "ADDRESS_NOT_IN_SYMBOL_RANGE", ("PC symbol range",))
        match = matches[0]
        mapped_source_reference = match.group("source")
        source_key = _source_lookup_key(mapped_source_reference, source_fingerprints)
        expected_source_fp = source_fingerprints.get(source_key) if source_key is not None else None
        if source_key is None or expected_source_fp != match.group("source_fp"):
            return HardFaultMappingResult((), "STALE_MAPPING", ("matching source fingerprint",))
        if elf_metadata is not None:
            if not isinstance(elf_metadata, str) or len(elf_metadata.encode("utf-8")) > 1_000_000:
                return HardFaultMappingResult((), "INSUFFICIENT_MAPPING_EVIDENCE", ("bounded ELF metadata",))
            try:
                elf = json.loads(elf_metadata)
            except (TypeError, ValueError, UnicodeError):
                return HardFaultMappingResult((), "MALFORMED_HARDFAULT", ("structured ELF metadata",))
            elf_source = elf.get("source") if type(elf) is dict else None
            elf_fingerprint = elf.get("source_fingerprint") if type(elf) is dict else None
            elf_key = _source_lookup_key(elf_source, source_fingerprints) if isinstance(elf_source, str) else None
            if elf_key != source_key or elf_fingerprint != expected_source_fp:
                return HardFaultMappingResult((), "STALE_MAPPING", ("matching ELF source fingerprint",))
        symbol = match.group("symbol")
        line = match.group("line")
        evidence = EngineeringContextEvidence.create(
            evidence_id=f"debug-map-{artifact_id}-{symbol}-{line}",
            tenant_id=tenant_id,
            project_id=project_id,
            source_type=ContextSourceType.DEBUG,
            subject=symbol,
            fact_key="source_mapping",
            fact_value=f"{source_key}:L{line}",
            summary=f"HardFault PC {pc.upper()} mapped to {symbol} at {source_key}:L{line}",
            source_reference=source_key,
            source_fingerprint=expected_source_fp,
            confidence=1.0,
            verification_status=ContextVerificationStatus.VERIFIED,
            trust_level=EvidenceTrustLevel.VERIFIED_FACT,
            evidence_status=EvidenceVerificationStatus.VERIFIED,
            provenance=(artifact_id, "map:" + _fingerprint(map_text), expected_source_fp),
        )
        return HardFaultMappingResult((evidence,), "MAPPED", ())


__all__ = ("HardFaultMappingResult", "HardFaultMappingService")
