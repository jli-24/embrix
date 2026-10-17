from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from embedded_copilot.engineering_consistency.adapters import (
    CanonicalProjectConsistencyAdapter,
)
from embedded_copilot.engineering_consistency.service import (
    HardwareFirmwareConsistencyService,
)
from embedded_copilot.engineering_pcb.adapters.bom import CSVPCBParserAdapter
from embedded_copilot.engineering_pcb.adapters.kicad import (
    KiCadBoardParserAdapter,
    KiCadSchematicParserAdapter,
)
from embedded_copilot.engineering_pcb.models import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBSourceFormat,
)
from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.parser import KiCadPCBParser, RootedPCBSourceResolver

from .service import ArtifactReaderPort, VirtualAssemblySource


class RootedArtifactReader:
    """Read only the explicit relative artifacts named by the canonical input."""

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root)
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("artifact reader root is invalid")
        self._root = candidate.resolve(strict=True)

    def read(self, relative_path: str) -> bytes:
        raw = str(relative_path).replace("\\", "/")
        relative = Path(raw)
        windows = PureWindowsPath(raw)
        if (
            not raw
            or relative.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("artifact path is invalid")
        candidate = (self._root / relative).resolve(strict=True)
        if not candidate.is_relative_to(self._root) or candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError("artifact is unavailable")
        return candidate.read_bytes()


def _artifact_reference(
    project_id: str,
    artifact_id: str,
    artifact_type: PCBArtifactType,
    source_format: PCBSourceFormat,
    relative_path: str,
    content: bytes,
) -> PCBArtifactReference:
    return PCBArtifactReference.create(
        project_id=project_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        source_format=source_format,
        source_reference=f"artifact://{project_id}/{artifact_id}",
        filename=Path(relative_path).name,
        byte_size=len(content),
        content_fingerprint="sha256:" + hashlib.sha256(content).hexdigest(),
        # The parser artifact identity is an observed input identity, not an
        # event timestamp. A fixed epoch keeps restart fingerprints stable.
        captured_at=datetime(1970, 1, 1, tzinfo=UTC),
    )


class CanonicalVirtualAssemblyAdapter:
    """Compose the existing canonical hardware/build lineage into the service port."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        project_id: str,
        toolchain_bin: str | Path,
    ) -> None:
        self._root = Path(project_root).resolve(strict=True)
        self._project_id = project_id
        self._toolchain_bin = Path(toolchain_bin)

    def collect(self) -> VirtualAssemblySource:
        consistency_input = CanonicalProjectConsistencyAdapter(
            self._root,
            project_id=self._project_id,
            toolchain_bin=self._toolchain_bin,
        ).collect()
        consistency_result = HardwareFirmwareConsistencyService().evaluate(
            consistency_input
        )
        reader: ArtifactReaderPort = RootedArtifactReader(self._root)
        schematic_bytes = reader.read(consistency_input.hardware.schematic.relative_path)
        pcb_bytes = reader.read(consistency_input.hardware.pcb.relative_path)
        bom_bytes = reader.read(consistency_input.hardware.bom.relative_path)
        schematic_ref = _artifact_reference(
            self._project_id,
            consistency_input.hardware.schematic.artifact_id,
            PCBArtifactType.SCHEMATIC,
            PCBSourceFormat.KICAD_SCH,
            consistency_input.hardware.schematic.relative_path,
            schematic_bytes,
        )
        pcb_ref = _artifact_reference(
            self._project_id,
            consistency_input.hardware.pcb.artifact_id,
            PCBArtifactType.BOARD,
            PCBSourceFormat.KICAD_PCB,
            consistency_input.hardware.pcb.relative_path,
            pcb_bytes,
        )
        bom_ref = _artifact_reference(
            self._project_id,
            consistency_input.hardware.bom.artifact_id,
            PCBArtifactType.BOM,
            PCBSourceFormat.CSV_BOM,
            consistency_input.hardware.bom.relative_path,
            bom_bytes,
        )
        schematic = KiCadSchematicParserAdapter(
            {schematic_ref.artifact_id: schematic_bytes}
        ).parse(schematic_ref)
        attachment = UserAttachment(
            id="pcb",
            filename=Path(consistency_input.hardware.pcb.relative_path).name,
            media_type=AttachmentType.EDA,
            content_type="application/x-kicad-pcb",
            size_bytes=len(pcb_bytes),
            metadata={"category": "eda", "format": "kicad_pcb"},
        )
        board = KiCadBoardParserAdapter(
            KiCadPCBParser(
                RootedPCBSourceResolver(
                    self._root,
                    {"pcb": consistency_input.hardware.pcb.relative_path},
                )
            ),
            {pcb_ref.artifact_id: attachment},
        ).parse(pcb_ref)
        bom = CSVPCBParserAdapter({bom_ref.artifact_id: bom_bytes}).parse(bom_ref)
        return VirtualAssemblySource(
            consistency_input=consistency_input,
            consistency_result=consistency_result,
            schematic=schematic,
            board=board,
            bom=bom,
            artifact_reader=reader,
        )


__all__ = (
    "CanonicalVirtualAssemblyAdapter",
    "RootedArtifactReader",
)
