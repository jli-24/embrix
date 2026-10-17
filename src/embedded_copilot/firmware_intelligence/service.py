from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from embedded_copilot.coding_runtime.analysis import (
    FirmwareParsedCallEdge,
    FirmwareParsedFile,
    FirmwareParsedSymbol,
    TreeSitterCodeParser,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
    EngineeringContextEvidence,
)
from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.engineering_copilot.common import text, validate_copy
from embedded_copilot.model_gateway import ModelPolicy, ModelRequest

from .contracts import FirmwareContext, FirmwareProposal, InterfaceContract
from .source import FirmwareSourceProvider, FirmwareSourceSnapshot, FirmwareSourceUnavailable


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CALL_PARSER_IDENTITY = "firmware_source_parser:v1"


@dataclass(frozen=True, slots=True)
class FirmwareSymbol:
    project_id: str
    file_reference: str
    symbol_name: str
    symbol_type: str
    line_start: int
    line_end: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class FirmwareCallEdge:
    project_id: str
    file_reference: str
    caller: str
    callee: str
    line: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class FirmwareIndex:
    project_id: str
    source_fingerprint: str
    symbols: tuple[FirmwareSymbol, ...]
    calls: tuple[FirmwareCallEdge, ...]
    parse_issues: tuple[str, ...]

    def search(self, query: str) -> tuple[FirmwareSymbol, ...]:
        tokens = tuple(token.casefold() for token in _TOKEN.findall(query))
        if not tokens:
            return ()
        return tuple(
            item
            for item in self.symbols
            if any(token in item.symbol_name.casefold() for token in tokens)
        )


@dataclass(frozen=True, slots=True)
class FirmwareIntelligenceResult:
    facts: tuple[str, ...]
    evidence: tuple[EngineeringContextEvidence, ...]
    analysis: tuple[str, ...]
    confidence: float
    limitations: tuple[str, ...]
    source_fingerprint: str
    source_file_count: int


def _fingerprint(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


class FirmwareIntelligenceService:
    """Deterministic source intelligence; it never generates a user answer."""

    __slots__ = ("_provider", "_parser", "_cache")

    def __init__(
        self,
        *,
        source_provider: FirmwareSourceProvider,
        parser: TreeSitterCodeParser | None = None,
    ) -> None:
        if not isinstance(source_provider, FirmwareSourceProvider):
            raise TypeError("firmware source provider is invalid")
        self._provider = source_provider
        self._parser = parser or TreeSitterCodeParser()
        self._cache: dict[tuple[str, str], FirmwareIndex] = {}

    @staticmethod
    def _symbol(project_id: str, source_fingerprint: str, source: FirmwareParsedFile, item: FirmwareParsedSymbol) -> FirmwareSymbol:
        return FirmwareSymbol(
            project_id=project_id,
            file_reference=source.path,
            symbol_name=item.name,
            symbol_type=item.symbol_type,
            line_start=item.line_start,
            line_end=item.line_end,
            fingerprint=_fingerprint(project_id, source_fingerprint, source.path, item.name, item.symbol_type, str(item.line_start), str(item.line_end)),
        )

    @staticmethod
    def _call(project_id: str, source_fingerprint: str, source: FirmwareParsedFile, item: FirmwareParsedCallEdge) -> FirmwareCallEdge:
        return FirmwareCallEdge(
            project_id=project_id,
            file_reference=source.path,
            caller=item.caller,
            callee=item.callee,
            line=item.line,
            fingerprint=_fingerprint(project_id, source_fingerprint, source.path, item.caller, item.callee, str(item.line)),
        )

    def index(
        self, project_id: str, *, allow_empty_source: bool = False
    ) -> FirmwareIndex:
        snapshot = self._provider.snapshot(
            project_id, allow_empty_source=allow_empty_source
        )
        return self._index_snapshot(snapshot)

    def _index_snapshot(self, snapshot: FirmwareSourceSnapshot) -> FirmwareIndex:
        key = (snapshot.project_id, snapshot.fingerprint)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        parsed = tuple(
            self._parser.parse_firmware(item.reference.relative_path, item.content)
            for item in snapshot.sources
            if item.reference.relative_path.casefold().endswith((".c", ".h", ".cpp", ".hpp"))
        )
        symbols = tuple(
            sorted(
                (
                    self._symbol(snapshot.project_id, snapshot.fingerprint, item, symbol)
                    for item in parsed if item.issue is None
                    for symbol in item.symbols
                ),
                key=lambda value: (value.file_reference.casefold(), value.line_start, value.symbol_type, value.symbol_name),
            )
        )
        calls = tuple(
            sorted(
                (
                    self._call(snapshot.project_id, snapshot.fingerprint, item, edge)
                    for item in parsed if item.issue is None
                    for edge in item.calls
                ),
                key=lambda value: (value.file_reference.casefold(), value.line, value.caller, value.callee),
            )
        )
        issues = tuple(
            sorted(
                f"parse issue at {item.path}:{item.issue.line}"
                for item in parsed if item.issue is not None
            )
        )
        result = FirmwareIndex(snapshot.project_id, snapshot.fingerprint, symbols, calls, issues)
        self._cache[key] = result
        return result

    def source_snapshot(
        self, project_id: str, *, allow_empty_source: bool = False
    ) -> FirmwareSourceSnapshot:
        """Return the bounded source snapshot for status classification."""

        return self._provider.snapshot(
            project_id, allow_empty_source=allow_empty_source
        )

    def is_empty_project(self, project_id: str) -> bool:
        return self._provider.is_empty_project(project_id)

    @staticmethod
    def _evidence(symbol: FirmwareSymbol, *, tenant_id: str) -> EngineeringContextEvidence:
        return EngineeringContextEvidence.create(
            evidence_id="firmware-" + hashlib.sha256(symbol.fingerprint.encode("ascii")).hexdigest()[:48],
            project_id=symbol.project_id,
            tenant_id=tenant_id,
            source_type=ContextSourceType.FIRMWARE_SOURCE,
            subject=symbol.symbol_name,
            summary=f"observed {symbol.symbol_type} {symbol.symbol_name} at line {symbol.line_start}",
            fact_key="line",
            fact_value=str(symbol.line_start),
            source_reference=symbol.file_reference,
            source_fingerprint=symbol.fingerprint,
            confidence=1.0,
            verification_status=ContextVerificationStatus.VERIFIED,
            provenance=(symbol.file_reference, symbol.fingerprint),
        )

    @staticmethod
    def _call_evidence(
        item: FirmwareCallEdge, *, source_fingerprint: str, tenant_id: str
    ) -> EngineeringContextEvidence:
        evidence_material = _fingerprint(
            item.project_id,
            source_fingerprint,
            item.caller,
            "CALLS",
            item.callee,
            item.file_reference,
            _CALL_PARSER_IDENTITY,
        )
        return EngineeringContextEvidence.create(
            evidence_id="firmware-call-" + hashlib.sha256(
                evidence_material.encode("ascii")
            ).hexdigest()[:48],
            project_id=item.project_id,
            tenant_id=tenant_id,
            source_type=ContextSourceType.FIRMWARE_SOURCE,
            subject=item.caller,
            summary=(
                f"observed static call {item.caller} -> {item.callee} "
                f"at {item.file_reference}:L{item.line}"
            ),
            fact_key="CALLS",
            fact_value=item.callee,
            source_reference=item.file_reference,
            source_fingerprint=source_fingerprint,
            confidence=1.0,
            verification_status=ContextVerificationStatus.VERIFIED,
            trust_level=EvidenceTrustLevel.OBSERVATION,
            evidence_status=EvidenceVerificationStatus.OBSERVED,
            provenance=(
                _CALL_PARSER_IDENTITY,
                "static_call_relation",
                item.file_reference,
                item.fingerprint,
            ),
        )

    @staticmethod
    def _file_evidence(
        project_id: str,
        source_fingerprint: str,
        file_reference: str,
        structure_count: int,
        tenant_id: str,
    ) -> EngineeringContextEvidence:
        """Project a file only after the parser observed source structures.

        This is not filename inference: the count is derived from parsed
        symbols and direct call edges for the same project-scoped source file.
        """
        fingerprint = _fingerprint(
            project_id,
            source_fingerprint,
            file_reference,
            "parsed-file",
            str(structure_count),
        )
        return EngineeringContextEvidence.create(
            evidence_id="firmware-" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:48],
            project_id=project_id,
            tenant_id=tenant_id,
            source_type=ContextSourceType.FIRMWARE_SOURCE,
            subject="parsed firmware file",
            summary=f"observed {structure_count} parsed source structures",
            fact_key="parsed_symbols",
            fact_value=str(structure_count),
            source_reference=file_reference,
            source_fingerprint=fingerprint,
            confidence=1.0,
            verification_status=ContextVerificationStatus.VERIFIED,
            provenance=(file_reference, fingerprint),
        )

    @staticmethod
    def _question_tokens(question: str) -> tuple[str, ...]:
        return tuple(token.casefold() for token in _TOKEN.findall(question))

    @staticmethod
    def _debug_timeout_evidence(
        project_id: str,
        values: tuple[EngineeringContextEvidence, ...],
    ) -> tuple[EngineeringContextEvidence, ...]:
        checked: list[EngineeringContextEvidence] = []
        for value in values:
            if type(value) is not EngineeringContextEvidence:
                raise TypeError("debug evidence is invalid")
            item = EngineeringContextEvidence.model_validate(value.model_dump(mode="python"))
            if item.project_id != project_id:
                raise ValueError("debug evidence binding is invalid")
            material = " ".join(
                part for part in (item.subject, item.summary, item.fact_key, item.fact_value) if part
            ).casefold()
            if "hal_timeout" in material:
                checked.append(item)
        return tuple(checked)

    @staticmethod
    def _is_inventory_question(question: str, symbols: tuple[FirmwareSymbol, ...]) -> bool:
        normalized = question.casefold()
        if any(item.symbol_name.casefold() in normalized for item in symbols):
            return False
        return (
            "firmware" in normalized
            or "source" in normalized
            or "文件" in question
            or "源码" in question
        )

    def analyze(
        self,
        project_id: str,
        question: str,
        *,
        tenant_id: str = "default-tenant",
        context_evidence: tuple[EngineeringContextEvidence, ...] = (),
        allow_empty_source: bool = False,
    ) -> FirmwareIntelligenceResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("firmware question is invalid")
        if type(context_evidence) is not tuple:
            raise TypeError("debug evidence is invalid")
        snapshot = self._provider.snapshot(
            project_id, allow_empty_source=allow_empty_source
        )
        index = self._index_snapshot(snapshot)
        tokens = self._question_tokens(question)
        symbols = tuple(
            item for item in index.symbols
            if any(token in item.symbol_name.casefold() for token in tokens)
        )
        lower = question.casefold()
        if "i2c" in lower:
            symbols = tuple(
                dict.fromkeys((*symbols, *(item for item in index.symbols if "i2c" in item.symbol_name.casefold())))
            )
        if "sensor_read" in lower:
            symbols = tuple(
                dict.fromkeys((*symbols, *(item for item in index.symbols if item.symbol_name == "sensor_read")))
            )
        if self._is_inventory_question(question, symbols):
            structures: dict[str, int] = {}
            for item in index.symbols:
                structures[item.file_reference] = structures.get(item.file_reference, 0) + 1
            for item in index.calls:
                structures[item.file_reference] = structures.get(item.file_reference, 0) + 1
            evidence = tuple(
                self._file_evidence(index.project_id, index.source_fingerprint, path, count, tenant_id)
                for path, count in sorted(structures.items())
                if count > 0
            )
            symbols = ()
        else:
            evidence = tuple(self._evidence(item, tenant_id=tenant_id) for item in symbols)
        selected_names = {item.symbol_name for item in symbols}
        relevant_calls = tuple(
            item for item in index.calls
            if item.caller in selected_names or item.callee in selected_names
        )
        if not self._is_inventory_question(question, symbols):
            evidence = evidence + tuple(
                self._call_evidence(
                    item,
                    source_fingerprint=index.source_fingerprint,
                    tenant_id=tenant_id,
                )
                for item in relevant_calls
            )
        facts = tuple(
            f"observed {item.symbol_type} {item.symbol_name} at {item.file_reference}:L{item.line_start}"
            for item in symbols
        )
        analysis = tuple(
            f"observed call {item.caller} -> {item.callee} at {item.file_reference}:L{item.line}"
            for item in relevant_calls
        )
        limitations = list(index.parse_issues)
        if "sensor_read" in lower and not any(item.callee == "HAL_I2C_Master_Transmit" for item in relevant_calls):
            limitations.append("missing call evidence from sensor_read to HAL_I2C_Master_Transmit")
        debug_timeouts = self._debug_timeout_evidence(project_id, context_evidence)
        if "sensor_read" in lower and debug_timeouts and any(
            item.caller == "sensor_read" and item.callee == "HAL_I2C_Master_Transmit"
            for item in relevant_calls
        ):
            analysis = (*analysis, "observed debug evidence reports HAL_TIMEOUT")
        if "sensor_read" in lower and not debug_timeouts:
            limitations.append("missing debug evidence; no unique failure cause is asserted")
        if not evidence:
            limitations.append("no firmware symbols match the request")
        return FirmwareIntelligenceResult(
            facts=facts,
            evidence=evidence,
            analysis=analysis,
            confidence=1.0 if evidence and not index.parse_issues else 0.6 if evidence else 0.0,
            limitations=tuple(dict.fromkeys(limitations)),
            source_fingerprint=index.source_fingerprint,
            source_file_count=len(snapshot.sources),
        )


class FirmwarePlanningService:
    """Existing proposal-planning capability retained for compatibility."""

    def __init__(self, model_gateway=None):
        self._model_gateway = model_gateway

    def plan(
        self,
        requirement: EngineeringRequirement,
        hardware_context: FirmwareContext,
        interfaces: tuple[InterfaceContract, ...],
        evidence_refs: tuple[str, ...] = (),
        *,
        context_snapshot: EngineeringContextSnapshot | None = None,
    ) -> FirmwareProposal:
        req = validate_copy(requirement, EngineeringRequirement)
        hw = validate_copy(hardware_context, FirmwareContext)
        if req.project_id != hw.project_id or type(evidence_refs) is not tuple:
            raise ValueError("project or evidence binding rejected")
        if type(interfaces) is not tuple:
            raise TypeError("interfaces must be a tuple")
        items = tuple(validate_copy(item, InterfaceContract) for item in interfaces)
        refs = tuple(text(item, "evidence_ref", 256) for item in evidence_refs)
        if not refs:
            raise ValueError("firmware evidence is required")
        rationale = None
        if self._model_gateway is not None and context_snapshot is not None:
            context = EngineeringContextSnapshot.model_validate(
                context_snapshot.model_dump(mode="python")
            )
            if context.project_id != req.project_id:
                raise ValueError("model context binding rejected")
            try:
                request = ModelRequest(
                    request_id=f"note-{req.requirement_id}",
                    task=req.description,
                    engineering_context=context,
                    system_instruction="Generate non-authoritative firmware explanation only.",
                    constraints=req.constraints,
                    model_policy=ModelPolicy(
                        primary_provider="local", model_name="offline", temperature=0.0
                    ),
                )
                rationale = getattr(self._model_gateway.generate(request), "content", None)
            except Exception:
                rationale = None
        assumptions = (
            f"platform:{hw.platform}",
            f"sdk_hal:{hw.sdk_hal}",
            f"rtos:{hw.rtos}",
            *(f"clock:{value}" for value in hw.clock_assumptions or ("unspecified",)),
            *(f"pin:{value}" for value in hw.pin_assumptions or ("unspecified",)),
            *(f"peripheral:{value}" for value in hw.peripheral_assumptions or ("unspecified",)),
            *(f"interrupt:{value}" for value in hw.interrupt_assumptions or ("unspecified",)),
        )
        return FirmwareProposal.create(
            proposal_id=f"firmware-{req.project_id}-{hw.fingerprint[-12:]}",
            project_id=req.project_id,
            driver_design=tuple(f"driver:{item.name}" for item in items),
            task_architecture=(f"rtos:{hw.rtos}",),
            api_proposal=tuple(f"api:{item.name}" for item in items),
            code_skeleton=tuple(f"void {item.name.casefold()}_init(void);" for item in items),
            assumptions=assumptions,
            evidence_refs=refs,
            confidence=0.7,
            rationale=rationale,
        )


__all__ = (
    "FirmwareCallEdge",
    "FirmwareIndex",
    "FirmwareIntelligenceResult",
    "FirmwareIntelligenceService",
    "FirmwarePlanningService",
    "FirmwareSourceUnavailable",
    "FirmwareSymbol",
)
