from __future__ import annotations

from .models import EngineeringArtifactType

_COMMON = (
    "Return one JSON object only. Do not return markdown, hidden reasoning, provider "
    "metadata, credentials, raw media, executable actions, DRC/ERC claims, or verified "
    "facts. Every observation must include visual evidence locators."
)

_POLICIES = {
    EngineeringArtifactType.PDF: "Extract document sections, tables, and limitations.",
    EngineeringArtifactType.IMAGE: "Extract visible objects, text, and visual issues.",
    EngineeringArtifactType.PCB_IMAGE: (
        "Extract only visible PCB components, interfaces, labels, and possible issues."
    ),
    EngineeringArtifactType.SCHEMATIC_IMAGE: (
        "Extract only visible schematic components, nets, interfaces, and possible issues."
    ),
    EngineeringArtifactType.DATASHEET: (
        "Extract component name, voltages, interfaces, GPIOs, package, and limitations."
    ),
    EngineeringArtifactType.WAVEFORM: (
        "Extract visible channels, displayed measurements, and possible anomalies."
    ),
    EngineeringArtifactType.DEBUG_SCREENSHOT: (
        "Extract visible error codes, messages, and status indicators."
    ),
}


def extraction_policy(artifact_type: EngineeringArtifactType) -> str:
    return f"{_COMMON} {_POLICIES[artifact_type]}"


__all__ = ("extraction_policy",)
