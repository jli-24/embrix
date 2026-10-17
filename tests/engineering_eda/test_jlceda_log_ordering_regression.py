"""Synthetic regression cases; these do not qualify an actual native dialect."""

from __future__ import annotations

import json

import pytest

from embedded_copilot.engineering_eda.adapters.jlceda_pro_v3 import (
    JLCEDAV3FormatError,
    observe_jlceda_schematic_components,
    read_jlceda_pro_v3,
)


def _log(records: list[tuple[str, int, str, str | None, dict[str, object]]]) -> bytes:
    lines = []
    for client, ticket, kind, native_id, payload in records:
        lines.append('{"type":"DOCHEAD"}||' + json.dumps({
            "docType": "SCH_PAGE", "uuid": "page-1", "client": client,
        }))
        outer = {"type": kind, "ticket": ticket}
        if native_id is not None:
            outer["id"] = native_id
        lines.append(json.dumps(outer) + "||" + json.dumps(payload))
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("clients,winner", [(('1', '2'), '2'), (('2', '10'), '2'), (('01', '1'), '1')])
@pytest.mark.parametrize("reverse", [False, True])
def test_equal_ticket_uses_larger_raw_client_lexicographically(
    clients: tuple[str, str], winner: str, reverse: bool
) -> None:
    records = [(c, 4, "OPAQUE", "object-1", {"client": c}) for c in clients]
    source = _log(records[::-1] if reverse else records)
    snapshot = read_jlceda_pro_v3(source)
    selected = snapshot.documents[0].current_records
    assert len(selected) == 1
    assert selected[0].client == winner
    assert len(snapshot.documents[0].records) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_same_native_id_competes_across_record_types(reverse: bool) -> None:
    records = [('1', 1, "OLD_TYPE", "object-1", {"old": True}),
               ('1', 2, "NEW_TYPE", "object-1", {"new": True})]
    source = _log(records[::-1] if reverse else records)
    snapshot = read_jlceda_pro_v3(source)
    document = snapshot.documents[0]
    assert len(document.current_records) == 1
    assert document.current_records[0].record_type == "NEW_TYPE"
    assert len(document.records) == 2
    assert snapshot.unsupported_types == ("NEW_TYPE", "OLD_TYPE")
    # Internal observation still refuses ambiguous histories as engineering facts.
    assert observe_jlceda_schematic_components(snapshot).components == ()


@pytest.mark.parametrize("reverse", [False, True])
def test_same_ordering_key_with_changed_type_is_rejected_even_for_same_payload(
    reverse: bool,
) -> None:
    records = [('1', 1, "OLD_TYPE", "object-1", {}),
               ('1', 1, "NEW_TYPE", "object-1", {})]
    with pytest.raises(JLCEDAV3FormatError, match="conflict"):
        read_jlceda_pro_v3(_log(records[::-1] if reverse else records))


@pytest.mark.parametrize("winner_first", [False, True])
def test_conflicting_historical_ordering_key_is_not_hidden_by_winner(
    winner_first: bool,
) -> None:
    conflicting = [('1', 1, "OPAQUE", "object-1", {"value": "a"}),
                   ('1', 1, "OPAQUE", "object-1", {"value": "b"})]
    winner = [('1', 3, "OPAQUE", "object-1", {"value": "winner"})]
    with pytest.raises(JLCEDAV3FormatError, match="conflict"):
        read_jlceda_pro_v3(_log(winner + conflicting if winner_first else conflicting + winner))


def test_document_deletion_singleton_does_not_collide_with_native_id() -> None:
    source = _log([('1', 1, "DELETE_DOC", None, {"isDelete": True}),
                   ('2', 9, "OPAQUE", "DELETE_DOC", {"value": "opaque"})])
    document = read_jlceda_pro_v3(source).documents[0]
    assert document.deleted is True
    assert len(document.current_records) == 2
    assert len(document.records) == 2


@pytest.mark.parametrize("field", ["pend", "revoke"])
def test_collaborative_state_is_still_outside_accepted_envelope(field: str) -> None:
    source = _log([('1', 1, "OPAQUE", "object-1", {})])
    source = source.replace(b'"ticket": 1', ('"ticket": 1, "' + field + '": true').encode())
    with pytest.raises(JLCEDAV3FormatError, match="outer layer"):
        read_jlceda_pro_v3(source)
