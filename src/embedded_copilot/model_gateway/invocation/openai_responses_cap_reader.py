"""Interpret one pinned native OpenAI Python SDK cap witness.

The host supplies the current witness and its authority. This reader only checks
the original SDK bytes and maps their documented field into an existing premise.
It never imports or executes the SDK source and does not establish account access.
"""

from __future__ import annotations

import ast

from .budget import digest, reject
from .openai_responses_qualification import _current_header, count_request_from_final
from .qualified_budget_producer import CapPremise, QualificationContext, WitnessHeader

_SDK_COMMIT = "2c1ae5266a67303d82ea46c57be854deaa6e5676"
_SDK_URL = (
    "https://raw.githubusercontent.com/openai/openai-python/"
    f"{_SDK_COMMIT}/src/openai/types/responses/response_create_params.py"
)
# Producer witness identities are bounded identifiers, so the host uses this ID
# for the pinned URL above. The host remains responsible for source origin.
_SDK_EVIDENCE_ID = f"openai-responses-cap-sdk-{_SDK_COMMIT}"
_SDK_SHA256 = "sha256:ebaf13e93a42bb971307a5b4f8e11967a3cc033313ab0433365a95b9c1819544"
_CAP_DOCUMENTATION = (
    "An upper bound for the number of tokens that can be generated for a response, "
    "including visible output tokens and "
    "[reasoning tokens](https://developers.openai.com/api/docs/guides/reasoning)."
)


def _documented_cap(raw: bytes) -> bool:
    """Read only the exact field and adjoining documentation from Python syntax."""
    try:
        tree = ast.parse(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, SyntaxError):
        reject()
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ResponseCreateParamsBase"
    ]
    if len(classes) != 1:
        reject()
    body = classes[0].body
    fields = [
        index
        for index, node in enumerate(body)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "max_output_tokens"
    ]
    if len(fields) != 1:
        reject()
    index = fields[0]
    annotation = body[index].annotation
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Optional"
        and isinstance(annotation.slice, ast.Name)
        and annotation.slice.id == "int"
        and body[index].value is None
        and index + 1 < len(body)
        and isinstance(body[index + 1], ast.Expr)
        and isinstance(body[index + 1].value, ast.Constant)
        and isinstance(body[index + 1].value.value, str)
    ):
        reject()
    documentation = " ".join(body[index + 1].value.value.split())
    if documentation != _CAP_DOCUMENTATION:
        reject()
    return True


class OpenAIResponsesSDKCapReader:
    """Adapt a host-held raw SDK witness to the existing cap premise contract."""

    def __init__(self, authority_id: str, witness_reader):
        self.authority_id = authority_id
        self._witness_reader = witness_reader

    def read(self, context: QualificationContext) -> CapPremise:
        count_request_from_final(context)
        if self._witness_reader is None or not callable(
            getattr(self._witness_reader, "read", None)
        ):
            reject()
        header = self._witness_reader.read(context)
        if type(header) is not WitnessHeader:
            reject()
        _current_header(header, context)
        if (
            header.authority_id != self.authority_id
            or header.evidence_identity != _SDK_EVIDENCE_ID
            or header.revision != _SDK_COMMIT
            or header.raw_sha256 != _SDK_SHA256
            or digest(header.raw_witness) != _SDK_SHA256
        ):
            reject()
        _documented_cap(header.raw_witness)
        return CapPremise(header, 2048, "max_output_tokens", True, True)
