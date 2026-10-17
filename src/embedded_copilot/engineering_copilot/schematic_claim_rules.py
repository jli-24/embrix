"""Narrow offline proof rules for current schematic parse and ERC claims.

The Catalog Owner must fetch and authenticate every input before calling this
module. A claim string, legacy evidence reference, or model output is never a
receipt. These rules only describe what the two canonical receipts prove.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel

from embedded_copilot.engineering_copilot.contracts import (
    SchematicErcReceipt,
    SchematicGenerationReceipt,
    SchematicParseValidationEvidence,
)
from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlan
from embedded_copilot.engineering_runtime.objects.models import (
    CircuitIntentObject,
    VerificationRequirement,
)

ClaimStatus = Literal["PASS", "BLOCKED"]
ClaimReason = Literal[
    "TRUSTED_CANONICAL_PROOF",
    "NO_TRUSTED_RESOLVER",
    "EVIDENCE_UNAVAILABLE",
    "EVIDENCE_MISMATCH",
    "SOURCE_STALE",
]
ProofScope = Literal["SCHEMATIC_PARSE", "SCHEMATIC_ERC"]

_PARSE_CLAIM = (
    "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT",
)
_ERC_CLAIM = (
    "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT",
)
_Model = TypeVar("_Model", bound=BaseModel)


@dataclass(frozen=True)
class ClaimProof:
    proof_scope: ProofScope
    source_receipt_id: str
    source_receipt_fingerprint: str


@dataclass(frozen=True)
class ClaimRuleResult:
    status: ClaimStatus
    reason: ClaimReason
    proof: ClaimProof | None = None


def _state(value: object) -> object:
    return getattr(value, "value", value)


def _blocked(reason: ClaimReason) -> ClaimRuleResult:
    return ClaimRuleResult(status="BLOCKED", reason=reason)


def _revalidate(value: object, model: type[_Model]) -> _Model | None:
    """Force model validators on Owner data, including copied/constructed models."""
    if not isinstance(value, model):
        return None
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        return None


def resolve_schematic_claim(
    claim: VerificationRequirement,
    *,
    intent: CircuitIntentObject,
    plan: SchematicPlan,
    generation: SchematicGenerationReceipt,
    parse: SchematicParseValidationEvidence | None,
    erc: SchematicErcReceipt | None,
    project_id: str,
    conversation_id: str,
    artifact_fingerprint: str,
    source_current: bool,
) -> ClaimRuleResult:
    """Resolve only the two exact approved-intent receipt claims.

    ``source_current`` must be freshly derived by the Catalog Owner; it is not
    a request parameter. The caller also checks current artifact bytes before
    this pure function runs. No engineering power, mode, or connection claim is
    covered by either receipt.
    """
    checked_claim = _revalidate(claim, VerificationRequirement)
    if checked_claim is None:
        return _blocked("EVIDENCE_MISMATCH")
    signature = (checked_claim.verification_type, checked_claim.claim, checked_claim.method)
    if signature not in {_PARSE_CLAIM, _ERC_CLAIM}:
        return _blocked("NO_TRUSTED_RESOLVER")
    if source_current is not True:
        return _blocked("SOURCE_STALE")
    checked_intent = _revalidate(intent, CircuitIntentObject)
    checked_plan = _revalidate(plan, SchematicPlan)
    checked_generation = _revalidate(generation, SchematicGenerationReceipt)
    if checked_intent is None or checked_plan is None or checked_generation is None:
        return _blocked("EVIDENCE_MISMATCH")
    intent, plan, generation = checked_intent, checked_plan, checked_generation
    if (
        checked_claim not in intent.verification_requirements
        or intent.project_id != project_id
        or plan.project_id != project_id
        or plan.conversation_id != conversation_id
        or _state(plan.status) != "APPROVED"
        or plan.circuit_intent_id != intent.object_id
        or plan.circuit_intent_revision != intent.revision
        or plan.circuit_intent_fingerprint != intent.fingerprint
        or generation.project_id != project_id
        or generation.conversation_id != conversation_id
        or generation.schematic_plan_id != plan.plan_id
        or generation.schematic_plan_fingerprint != plan.fingerprint
        or generation.artifact_fingerprint != artifact_fingerprint
        or _state(generation.status) != "SUCCESS"
        or generation.validation_status != "PARSE_VERIFIED"
    ):
        return _blocked("EVIDENCE_MISMATCH")
    if parse is None:
        return _blocked("EVIDENCE_UNAVAILABLE")
    checked_parse = _revalidate(parse, SchematicParseValidationEvidence)
    if checked_parse is None:
        return _blocked("EVIDENCE_MISMATCH")
    parse = checked_parse
    if (
        generation.parse_validation != parse
        or parse.project_id != project_id
        or parse.conversation_id != conversation_id
        or parse.operation_id != generation.operation_id
        or parse.artifact_path != generation.artifact_path
        or parse.artifact_fingerprint != artifact_fingerprint
        or parse.validation_status != "PARSE_VERIFIED"
        or parse.exit_code != 0
        or parse.artifact_unchanged is not True
    ):
        return _blocked("EVIDENCE_MISMATCH")
    if signature == _PARSE_CLAIM:
        return ClaimRuleResult(
            status="PASS", reason="TRUSTED_CANONICAL_PROOF",
            proof=ClaimProof(
                proof_scope="SCHEMATIC_PARSE", source_receipt_id=parse.validation_id,
                source_receipt_fingerprint=parse.fingerprint,
            ),
        )
    if erc is None:
        return _blocked("EVIDENCE_UNAVAILABLE")
    checked_erc = _revalidate(erc, SchematicErcReceipt)
    if checked_erc is None:
        return _blocked("EVIDENCE_MISMATCH")
    erc = checked_erc
    if (
        erc.project_id != project_id
        or erc.conversation_id != conversation_id
        or erc.schematic_plan_fingerprint != plan.fingerprint
        or erc.artifact_path != generation.artifact_path
        or erc.artifact_fingerprint != artifact_fingerprint
        or erc.result.operation_id != erc.operation_id
        or erc.result.schematic_artifact_ref != generation.artifact_path
        or erc.result.schematic_content_fingerprint != artifact_fingerprint
        or _state(erc.result.status) != "ERC_PASS"
        or _state(erc.engineering_status) != "PASS"
        or erc.tool_execution_status != "SUCCEEDED"
    ):
        return _blocked("EVIDENCE_MISMATCH")
    return ClaimRuleResult(
        status="PASS", reason="TRUSTED_CANONICAL_PROOF",
        proof=ClaimProof(
            proof_scope="SCHEMATIC_ERC", source_receipt_id=erc.receipt_id,
            source_receipt_fingerprint=erc.fingerprint,
        ),
    )
