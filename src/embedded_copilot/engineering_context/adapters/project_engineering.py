from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from embedded_copilot.debug_intelligence import DebugIntelligenceService, HardFaultMappingService
from embedded_copilot.engineering_context.models import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)
from embedded_copilot.engineering_intelligence.domain_adapters import (
    evaluate_sample_rate,
    project_hardware_metadata,
    project_pcb_connectivity,
)
from embedded_copilot.engineering_pcb.models import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBSourceFormat,
)
from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.parser import KiCadPCBParser, RootedPCBSourceResolver
from embedded_copilot.firmware_intelligence import (
    FirmwareIntelligenceService,
    FirmwareSourceProvider,
    FirmwareSourceUnavailable,
)


_PERIOD = re.compile(r"(?:period|interval|task_period)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_ADDRESS = re.compile(r"sensor_read[\s\S]{0,240}?0x([0-9a-fA-F]{2,4})")
_EDGE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*->\s*([A-Za-z_][A-Za-z0-9_]*)")
_RATE = re.compile(r"sample(?:\s+rate)?\s*>=\s*(\d+(?:\.\d+)?)\s*Hz", re.IGNORECASE)


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class ProjectEngineeringEvidenceAdapter:
    """Read-only project adapter that projects real parser/service results."""

    __slots__ = (
        "_debug_service",
        "_firmware",
        "_firmware_source",
        "_mapping",
        "_root",
        "_project",
    )

    def __init__(
        self,
        root_resolver: Callable[[str], Path],
        project_resolver: Callable[[str, str], object] | None = None,
    ) -> None:
        if not callable(root_resolver):
            raise TypeError("engineering project root resolver is invalid")
        if project_resolver is not None and not callable(project_resolver):
            raise TypeError("engineering project resolver is invalid")
        self._root = root_resolver
        self._project = project_resolver
        self._firmware_source = FirmwareSourceProvider(root_resolver)
        self._firmware = FirmwareIntelligenceService(source_provider=self._firmware_source)
        self._debug_service = DebugIntelligenceService()
        self._mapping = HardFaultMappingService()

    @staticmethod
    def _safe_path(root: Path, relative: str, *, maximum: int = 1_000_000) -> tuple[Path, bytes, str]:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("project artifact reference is invalid")
        candidate = root.joinpath(*pure.parts)
        cursor = root
        for part in pure.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("project artifact symlink is rejected")
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            raise ValueError("project artifact is unavailable")
        content = resolved.read_bytes()
        if not content or len(content) > maximum:
            raise ValueError("project artifact size is invalid")
        return resolved, content, _digest(content)

    @staticmethod
    def _evidence(
        *,
        source_type: ContextSourceType,
        request: EngineeringContextRequest,
        evidence_id: str,
        subject: str,
        fact_key: str | None,
        fact_value: str | None,
        summary: str,
        source_reference: str,
        source_fingerprint: str,
        trust_level: EvidenceTrustLevel = EvidenceTrustLevel.OBSERVATION,
        evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.OBSERVED,
        verification_status: ContextVerificationStatus = ContextVerificationStatus.VERIFIED,
        provenance: tuple[str, ...] = (),
    ) -> EngineeringContextEvidence:
        return EngineeringContextEvidence.create(
            evidence_id=evidence_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            source_type=source_type,
            subject=subject,
            fact_key=fact_key,
            fact_value=fact_value,
            summary=summary,
            source_reference=source_reference,
            source_fingerprint=source_fingerprint,
            confidence=1.0 if trust_level is EvidenceTrustLevel.VERIFIED_FACT else 0.8,
            verification_status=verification_status,
            trust_level=trust_level,
            evidence_status=evidence_status,
            provenance=provenance or (evidence_id,),
        )

    def _firmware_evidence(
        self,
        request: EngineeringContextRequest,
        root: Path,
        *,
        allow_empty_source: bool = False,
    ) -> tuple[EngineeringContextEvidence, ...]:
        try:
            snapshot = self._firmware_source.snapshot(
                request.project_id, allow_empty_source=allow_empty_source
            )
        except FirmwareSourceUnavailable as error:
            if error.missing_source:
                return ()
            raise
        if not snapshot.sources:
            return ()
        result = self._firmware.analyze(
            request.project_id,
            "firmware source inventory sensor_read I2C",
            tenant_id=request.tenant_id,
            allow_empty_source=allow_empty_source,
        )
        values = list(result.evidence)
        for source in snapshot.sources:
            match = _ADDRESS.search(source.content)
            if match:
                address = "0x" + match.group(1).upper()
                values.append(self._evidence(
                    source_type=ContextSourceType.FIRMWARE_SOURCE,
                    request=request,
                    evidence_id="firmware-sensor-address",
                    subject="sensor",
                    fact_key="configured_address",
                    fact_value=address,
                    summary=f"Firmware source configures sensor address {address}",
                    source_reference=source.reference.relative_path,
                    source_fingerprint=source.reference.fingerprint,
                    trust_level=EvidenceTrustLevel.VERIFIED_FACT,
                    evidence_status=EvidenceVerificationStatus.VERIFIED,
                    provenance=(source.reference.relative_path, source.reference.fingerprint),
                ))
            if "HAL_I2C_Master_Transmit" in source.content:
                values.append(self._evidence(
                    source_type=ContextSourceType.FIRMWARE_SOURCE,
                    request=request,
                    evidence_id="firmware-sensor-i2c-call",
                    subject="sensor_read",
                    fact_key="calls",
                    fact_value="HAL_I2C_Master_Transmit",
                    summary="Firmware source contains the sensor_read to HAL_I2C_Master_Transmit call edge",
                    source_reference=source.reference.relative_path,
                    source_fingerprint=source.reference.fingerprint,
                    trust_level=EvidenceTrustLevel.VERIFIED_FACT,
                    evidence_status=EvidenceVerificationStatus.VERIFIED,
                    provenance=(source.reference.relative_path, source.reference.fingerprint),
                ))
            period = _PERIOD.search(source.content)
            if period:
                values.append(self._evidence(
                    source_type=ContextSourceType.FIRMWARE_SOURCE,
                    request=request,
                    evidence_id="firmware-task-period",
                    subject="SensorTask",
                    fact_key="period",
                    fact_value=f"{period.group(1)}ms",
                    summary=f"Firmware source declares SensorTask period {period.group(1)}ms",
                    source_reference=source.reference.relative_path,
                    source_fingerprint=source.reference.fingerprint,
                    trust_level=EvidenceTrustLevel.VERIFIED_FACT,
                    evidence_status=EvidenceVerificationStatus.VERIFIED,
                    provenance=(source.reference.relative_path, source.reference.fingerprint),
                ))
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _optional(
        producer: Callable[[], tuple[EngineeringContextEvidence, ...]],
    ) -> tuple[EngineeringContextEvidence, ...]:
        """Project optional artifacts without blocking source evidence."""

        try:
            return producer()
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            return ()

    def _greenfield_context(
        self, request: EngineeringContextRequest, project: object
    ) -> tuple[EngineeringContextEvidence, ...]:
        creation_mode = getattr(getattr(project, "creation_mode", None), "value", None)
        if creation_mode != "CREATE_NEW":
            return ()
        project_type = getattr(getattr(project, "project_type", None), "value", None)
        project_name = getattr(project, "name", "project")
        material = "|".join(
            (
                request.project_id,
                request.tenant_id,
                str(project_type or "UNKNOWN"),
                request.task,
                *request.constraints,
            )
        )
        source_fingerprint = _digest(material.encode("utf-8"))
        values = [
            self._evidence(
                source_type=ContextSourceType.REQUIREMENT,
                request=request,
                evidence_id="greenfield-project-metadata",
                subject=str(project_name),
                fact_key="project_type",
                fact_value=str(project_type or "UNKNOWN"),
                summary="Greenfield project metadata is bound to the engineering request.",
                source_reference="workspace://project-metadata",
                source_fingerprint=source_fingerprint,
                trust_level=EvidenceTrustLevel.VERIFIED_FACT,
                evidence_status=EvidenceVerificationStatus.VERIFIED,
                provenance=("project-metadata", source_fingerprint),
            ),
            self._evidence(
                source_type=ContextSourceType.REQUIREMENT,
                request=request,
                evidence_id="greenfield-user-requirement",
                subject="user requirement",
                fact_key="requirement",
                fact_value=request.task,
                summary="User requirement is bound to the Greenfield project.",
                source_reference="workspace://user-requirement",
                source_fingerprint=source_fingerprint,
                trust_level=EvidenceTrustLevel.OBSERVATION,
                evidence_status=EvidenceVerificationStatus.OBSERVED,
                provenance=("user-requirement", source_fingerprint),
            ),
        ]
        for index, constraint in enumerate(request.constraints, start=1):
            constraint_fingerprint = _digest(
                f"{source_fingerprint}|constraint|{index}|{constraint}".encode("utf-8")
            )
            values.append(
                self._evidence(
                    source_type=ContextSourceType.REQUIREMENT,
                    request=request,
                    evidence_id=f"greenfield-constraint-{index}",
                    subject="confirmed constraint",
                    fact_key="constraint",
                    fact_value=constraint,
                    summary="Confirmed engineering constraint is bound to the project.",
                    source_reference="workspace://confirmed-constraints",
                    source_fingerprint=constraint_fingerprint,
                    trust_level=EvidenceTrustLevel.VERIFIED_FACT,
                    evidence_status=EvidenceVerificationStatus.VERIFIED,
                    provenance=("confirmed-constraint", constraint_fingerprint),
                )
            )
        return tuple(values)

    def _hardware(self, request: EngineeringContextRequest, root: Path) -> tuple[EngineeringContextEvidence, ...]:
        _, content, fingerprint = self._safe_path(root, "hardware/sensor_bus.json")
        value = json.loads(content.decode("utf-8-sig"))
        if type(value) is not dict or not all(isinstance(key, str) for key in value):
            raise ValueError("hardware metadata is invalid")
        records = tuple(
            {
                "evidence_id": f"hardware-{key}",
                "subject": "sensor",
                "fact_key": key,
                "fact_value": str(item),
                "summary": f"Hardware metadata declares {key} = {item}",
            }
            for key, item in sorted(value.items())
            if isinstance(item, (str, int, float, bool))
        )
        return project_hardware_metadata(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            records=records,
            source_reference="workspace://hardware/sensor_bus.json",
            source_fingerprint=fingerprint,
        ).evidence

    def _requirement(self, request: EngineeringContextRequest, root: Path, firmware: tuple[EngineeringContextEvidence, ...]) -> tuple[EngineeringContextEvidence, ...]:
        _, content, fingerprint = self._safe_path(root, "requirements/system_requirements.md")
        text = content.decode("utf-8-sig")
        rate_match = _RATE.search(text)
        if rate_match is None:
            return ()
        required = float(rate_match.group(1))
        requirement = self._evidence(
            source_type=ContextSourceType.REQUIREMENT,
            request=request,
            evidence_id="requirement-sample-rate",
            subject="sensor sample rate",
            fact_key="required_hz",
            fact_value=f"{required:g}Hz",
            summary=f"Requirement declares sensor sample rate >= {required:g}Hz",
            source_reference="workspace://requirements/system_requirements.md",
            source_fingerprint=fingerprint,
            trust_level=EvidenceTrustLevel.VERIFIED_FACT,
            evidence_status=EvidenceVerificationStatus.VERIFIED,
            provenance=("requirement-sample-rate", fingerprint),
        )
        timing = tuple(item for item in firmware if item.fact_key == "period")
        finding = evaluate_sample_rate(
            requirement_id=requirement.evidence_id,
            required_hz=required,
            firmware_evidence=timing,
        )
        if finding.measured_value is None:
            return (requirement,)
        candidate = self._evidence(
            source_type=ContextSourceType.REQUIREMENT,
            request=request,
            evidence_id="requirement-sample-rate-finding",
            subject="sensor sample rate",
            fact_key="verification_status",
            fact_value=finding.status,
            summary=f"Requirement analysis found {finding.measured_value:g}Hz against {required:g}Hz",
            source_reference="workspace://requirements/system_requirements.md",
            source_fingerprint=fingerprint,
            trust_level=EvidenceTrustLevel.CANDIDATE,
            evidence_status=EvidenceVerificationStatus.CANDIDATE,
            verification_status=ContextVerificationStatus.PROJECTED,
            provenance=(requirement.evidence_id, *finding.evidence_ids),
        )
        return requirement, candidate

    def _architecture(self, request: EngineeringContextRequest, root: Path) -> tuple[EngineeringContextEvidence, ...]:
        _, content, fingerprint = self._safe_path(root, "architecture/architecture.md")
        values = []
        for index, (left, right) in enumerate(_EDGE.findall(content.decode("utf-8-sig")), start=1):
            values.append(self._evidence(
                source_type=ContextSourceType.ARCHITECTURE,
                request=request,
                evidence_id=f"architecture-edge-{index}",
                subject=left,
                fact_key="depends_on",
                fact_value=right,
                summary=f"Architecture document declares {left} -> {right}",
                source_reference="workspace://architecture/architecture.md",
                source_fingerprint=fingerprint,
                provenance=(f"architecture-edge-{index}", fingerprint),
            ))
        return tuple(values)

    def _pcb(self, request: EngineeringContextRequest, root: Path) -> tuple[EngineeringContextEvidence, ...]:
        board_path, content, fingerprint = self._safe_path(root, "pcb/sample.kicad_pcb", maximum=25 * 1024 * 1024)
        artifact = PCBArtifactReference(
            project_id=request.project_id,
            artifact_id="pcb-board-sample",
            artifact_type=PCBArtifactType.BOARD,
            source_format=PCBSourceFormat.KICAD_PCB,
            source_reference="pcb://sample.kicad_pcb",
            filename=board_path.name,
            byte_size=len(content),
            content_fingerprint=fingerprint,
            captured_at=datetime.now(UTC),
        )
        attachment = UserAttachment(
            id="pcb-board-sample",
            filename=board_path.name,
            media_type=AttachmentType.EDA,
            content_type="application/x-kicad-pcb",
            size_bytes=len(content),
            metadata={"category": "eda", "format": "kicad_pcb"},
        )
        parser = KiCadPCBParser(
            RootedPCBSourceResolver(root / "pcb", {attachment.id: attachment.filename})
        )
        model = parser.parse(attachment)
        connections = []
        for net in model.nets:
            if net.name != "I2C_SCL":
                continue
            for node in net.nodes:
                if node.reference == "U3":
                    mcu = next((candidate for candidate in net.nodes if candidate.reference == "U1"), None)
                    if mcu is not None:
                        connections.append({"evidence_id": "pcb-sensor-scl", "component": "U3->U1", "net": net.name, "pin": mcu.pin})
        if not connections:
            return ()
        return project_pcb_connectivity(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            connections=tuple(connections),
            source_reference="workspace://pcb/sample.kicad_pcb",
            source_fingerprint=fingerprint,
        ).evidence

    def _debug_artifacts(self, request: EngineeringContextRequest, root: Path) -> tuple[EngineeringContextEvidence, ...]:
        values: list[EngineeringContextEvidence] = []
        try:
            _, content, fingerprint = self._safe_path(root, "debug/i2c_timeout.json")
            payload = json.loads(content.decode("utf-8-sig"))
            if type(payload) is dict and isinstance(payload.get("message"), str):
                values.extend(self._debug_service.analyze(
                    tenant_id=request.tenant_id,
                    project_id=request.project_id,
                    artifact_id=str(payload.get("artifact_id", "i2c-timeout")),
                    source=payload["message"],
                    source_reference="workspace://debug/i2c_timeout.json",
                    source_fingerprint=fingerprint,
                ).evidence)
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
        try:
            _, content, _ = self._safe_path(root, "debug/hardfault.json")
            payload = json.loads(content.decode("utf-8-sig"))
            source_snapshot = self._firmware_source.snapshot(request.project_id)
            source_fingerprints = {
                item.reference.relative_path: item.reference.fingerprint
                for item in source_snapshot.sources
            }
            _, map_content, _ = self._safe_path(root, "debug/firmware.map")
            _, elf_content, _ = self._safe_path(root, "debug/firmware.elf.json")
            values.extend(self._mapping.map(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                artifact_id="hardfault-1",
                pc=str(payload.get("PC", "")),
                map_text=map_content.decode("utf-8-sig"),
                source_fingerprints=source_fingerprints,
                map_source_reference="workspace://debug/firmware.map",
                elf_metadata=elf_content.decode("utf-8-sig"),
            ).evidence)
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
        return tuple(values)

    def list_evidence(self, request: EngineeringContextRequest) -> tuple[EngineeringContextEvidence, ...]:
        checked = EngineeringContextRequest.model_validate(copy.deepcopy(request.model_dump(mode="python")))
        project = None
        if self._project is not None:
            project = self._project(checked.project_id, checked.tenant_id)
        creation_mode = getattr(getattr(project, "creation_mode", None), "value", None)
        root: Path | None
        try:
            candidate = self._root(checked.project_id)
        except (OSError, ValueError):
            if creation_mode != "CREATE_NEW":
                raise
            candidate = None
        if isinstance(candidate, Path) and not candidate.is_symlink() and candidate.is_dir():
            root = candidate
        elif creation_mode == "CREATE_NEW":
            # A Greenfield project is allowed to have no source directory yet.
            # Requirement metadata remains valid context; Brownfield projects
            # still fail closed below when their root is unavailable.
            root = None
        else:
            raise ValueError("engineering project root is unavailable")
        firmware = (
            ()
            if root is None
            else self._firmware_evidence(
                checked,
                root,
                allow_empty_source=creation_mode == "CREATE_NEW",
            )
        )
        greenfield = () if project is None else self._greenfield_context(checked, project)
        hardware = () if root is None else self._optional(lambda: self._hardware(checked, root))
        requirement = () if root is None else self._optional(lambda: self._requirement(checked, root, firmware))
        architecture = () if root is None else self._optional(lambda: self._architecture(checked, root))
        pcb = () if root is None else self._optional(lambda: self._pcb(checked, root))
        values = [*firmware, *greenfield, *hardware, *requirement, *architecture, *pcb]
        configured = next((item for item in firmware if item.fact_key == "configured_address"), None)
        default = next((item for item in hardware if item.fact_key == "default_address"), None)
        if configured is not None and default is not None and configured.fact_value != default.fact_value:
            values.append(self._evidence(
                source_type=ContextSourceType.HARDWARE,
                request=checked,
                evidence_id="hardware-firmware-address-conflict",
                subject="sensor",
                fact_key="address_conflict",
                fact_value=f"firmware={configured.fact_value};hardware={default.fact_value}",
                summary="Firmware configured address conflicts with the hardware default address",
                source_reference=default.source_reference,
                source_fingerprint=default.source_fingerprint,
                trust_level=EvidenceTrustLevel.CANDIDATE,
                evidence_status=EvidenceVerificationStatus.CANDIDATE,
                verification_status=ContextVerificationStatus.PROJECTED,
                provenance=(configured.evidence_id, default.evidence_id),
            ))
        if root is not None and any(marker in checked.task.casefold() for marker in ("debug", "hardfault", "hard fault", "panic", "guru meditation", "timeout", "compiler")):
            values.extend(self._debug_artifacts(checked, root))
        unique = {item.evidence_id: item for item in values}
        return tuple(unique[key] for key in sorted(unique))


__all__ = ("ProjectEngineeringEvidenceAdapter",)
