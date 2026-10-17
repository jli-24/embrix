from embedded_copilot.engineering_eda.production_capability import (
    EDAValidationEvidence,
    EDACapabilityStatus,
    project_eda_capabilities,
)


def test_eda_structured_changeset_does_not_upgrade_without_real_validation_evidence() -> None:
    projection = project_eda_capabilities(structured_changeset=True, evidence=None)
    assert projection.changeset is EDACapabilityStatus.PARTIAL
    assert projection.drc is EDACapabilityStatus.UNAVAILABLE
    assert projection.erc is EDACapabilityStatus.UNAVAILABLE
    assert projection.apply is EDACapabilityStatus.UNAVAILABLE


def test_eda_apply_requires_real_kicad10_drc_and_erc_evidence() -> None:
    evidence = EDAValidationEvidence(tool_version="kicad-10.0", drc_completed=True, erc_completed=False)
    projection = project_eda_capabilities(structured_changeset=True, evidence=evidence)
    assert projection.drc is EDACapabilityStatus.AVAILABLE
    assert projection.erc is EDACapabilityStatus.UNAVAILABLE
    assert projection.apply is EDACapabilityStatus.UNAVAILABLE
