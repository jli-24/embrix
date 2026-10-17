"""Offline checks for the documented EasyEDA Pro V3 log envelope."""

from __future__ import annotations

from hashlib import sha256

import pytest

from embedded_copilot.engineering_eda.adapters.jlceda_pro_v3 import (
    JLCEDAV3FormatError,
    observe_jlceda_entry,
    observe_jlceda_schematic_components,
    read_jlceda_pro_v3,
)


def header(client: str, uuid: str = "page-1") -> str:
    return f'{{"type":"DOCHEAD"}}||{{"docType":"SCH_PAGE","uuid":"{uuid}","client":"{client}"}}|'


def record(ticket: int, value: str, *, native_id: str = "object-1") -> str:
    return (
        f'{{"type":"UNKNOWN_PRIMITIVE","id":"{native_id}","ticket":{ticket}}}'
        f'||{{"value":"{value}"}}|'
    )


def log(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_two_clients_higher_ticket_wins_without_losing_opaque_records() -> None:
    source = log(header("2"), record(1, "old"), header("1"), record(2, "new"))
    before = bytes(source)

    result = read_jlceda_pro_v3(source)

    assert source == before
    assert result.source_sha256 == sha256(source).hexdigest()
    assert len(result.documents) == 1
    document = result.documents[0]
    assert (document.doc_type, document.uuid, document.deleted) == (
        "SCH_PAGE",
        "page-1",
        False,
    )
    assert len(document.records) == 2
    assert document.current_records[0].payload_json == '{"value":"new"}'
    assert document.current_records[0].native_id == "object-1"
    assert result.unsupported_types == ("UNKNOWN_PRIMITIVE",)


def test_larger_client_wins_equal_ticket() -> None:
    source = log(header("2"), record(4, "client-2"), header("1"), record(4, "client-1"))
    result = read_jlceda_pro_v3(source)
    assert result.documents[0].current_records[0].payload_json == '{"value":"client-2"}'


def test_unpublished_client_ordering_is_rejected() -> None:
    with pytest.raises(JLCEDAV3FormatError, match="unsupported client"):
        read_jlceda_pro_v3(log(header("client-A"), record(1, "value")))


def test_unknown_document_type_remains_visible() -> None:
    source = log(
        '{"type":"DOCHEAD"}||{"docType":"FUTURE","uuid":"future-1","client":"1"}|',
        '{"type":"FUTURE_RECORD","id":"native-1","ticket":1}||{"opaque":true}|',
    )
    result = read_jlceda_pro_v3(source)
    assert result.unsupported_document_types == ("FUTURE",)
    assert result.unsupported_types == ("FUTURE_RECORD",)
    assert result.documents[0].current_records[0].native_id == "native-1"


def test_atomic_and_document_tombstones_are_retained() -> None:
    source = log(
        header("1"),
        record(1, "live"),
        '{"type":"UNKNOWN_PRIMITIVE","id":"object-1","ticket":2}||""',
        '{"type":"DELETE_DOC","ticket":3}||{"isDelete":true}|',
    )
    document = read_jlceda_pro_v3(source).documents[0]
    assert document.deleted is True
    assert document.current_records[0].deleted is True
    assert len(document.records) == 3


@pytest.mark.parametrize(
    "source",
    [
        log(record(1, "missing header")),
        log(header("1"), '{"type":"X","ticket":1}||{bad}|'),
        log(header("1"), '{"type":"X","ticket":1}||{"a":1}||{"b":2}|'),
        log(header("1"), '{"type":"X","ticket":true}||{}|'),
        log(header("1"), '{"type":"X","type":"Y","ticket":1}||{}|'),
        log(header("1"), '{"type":"DELETE_DOC","ticket":1}||{}|'),
        log(
            '{"type":"DOCHEAD"}||{"docType":"SCH_PAGE","uuid":"x","client":"1","extra":1}|'
        ),
        log(
            header("1"),
            '{"type":"DELETE_DOC","ticket":1}||{"isDelete":true,"extra":1}|',
        ),
    ],
)
def test_malformed_records_fail_closed(source: bytes) -> None:
    with pytest.raises(JLCEDAV3FormatError):
        read_jlceda_pro_v3(source)


def test_same_ordering_key_with_conflicting_payload_fails_closed() -> None:
    source = log(header("1"), record(3, "a"), record(3, "b"))
    with pytest.raises(JLCEDAV3FormatError, match="conflict"):
        read_jlceda_pro_v3(source)


def test_document_uuid_cannot_change_type() -> None:
    source = log(
        header("1"),
        '{"type":"META","ticket":1}||{}|',
        '{"type":"DOCHEAD"}||{"docType":"PCB","uuid":"page-1","client":"1"}|',
    )
    with pytest.raises(JLCEDAV3FormatError, match="document type"):
        read_jlceda_pro_v3(source)


def test_size_line_count_line_length_and_nesting_limits() -> None:
    source = log(header("1"), record(1, "ok"))
    with pytest.raises(JLCEDAV3FormatError, match="size"):
        read_jlceda_pro_v3(source, max_bytes=len(source) - 1)
    with pytest.raises(JLCEDAV3FormatError, match="line count"):
        read_jlceda_pro_v3(source, max_lines=1)
    with pytest.raises(JLCEDAV3FormatError, match="line length"):
        read_jlceda_pro_v3(source, max_line_bytes=20)
    nested = log(header("1"), '{"type":"X","ticket":1}||{"a":[[[1]]]}|')
    with pytest.raises(JLCEDAV3FormatError, match="nesting"):
        read_jlceda_pro_v3(nested, max_depth=3)


def test_synthetic_component_and_exact_parent_attributes_are_observations() -> None:
    """Synthetic V3 subset; this is not a third-party format oracle."""
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"component-1","ticket":1}||'
        '{"partId":"part-1","positionX":300,"positionY":200,"rotation":15,"isMirror":false}|',
        '{"type":"ATTR","id":"attr-1","ticket":1}||'
        '{"parentId":"component-1","key":"Designator","value":"U1"}|',
        '{"type":"ATTR","id":"attr-2","ticket":1}||'
        '{"parentId":"component-1","key":"Symbol","value":"symbol-1"}|',
        '{"type":"ATTR","id":"pin-override","ticket":1}||'
        '{"parentId":"component-1pin-1","key":"NUMBER","value":"1"}|',
        '{"type":"FUTURE","id":"future-1","ticket":1}||{"raw":true}|',
    )
    snapshot = read_jlceda_pro_v3(source)
    observation = observe_jlceda_schematic_components(snapshot)

    assert len(observation.components) == 1
    component = observation.components[0]
    assert (component.document_uuid, component.native_id, component.source_line) == (
        "page-1",
        "component-1",
        2,
    )
    assert (component.part_id, component.position_x, component.position_y) == (
        "part-1",
        300,
        200,
    )
    assert component.coordinate_unit == "UNVERIFIED_NATIVE_UNIT"
    assert [
        (item.native_id, item.key, item.value, item.source_line)
        for item in component.attributes
    ] == [
        ("attr-1", "Designator", "U1", 3),
        ("attr-2", "Symbol", "symbol-1", 4),
    ]
    assert [(issue.kind, issue.code) for issue in observation.issues] == [
        ("UNKNOWN", "UNSUPPORTED_RECORD_TYPE"),
        ("UNKNOWN", "UNMAPPED_ATTRIBUTE"),
    ]
    assert snapshot.documents[0].records[-1].payload_json == '{"raw":true}'


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        (
            '{"type":"COMPONENT","id":"component-1","ticket":2}||{"partId":"other"}|',
            "REPEATED_NATIVE_ID",
        ),
        (
            '{"type":"ATTR","id":"component-1","ticket":2}||{"parentId":"component-1","key":"Designator","value":"U1"}|',
            "REPEATED_NATIVE_ID",
        ),
        ('{"type":"COMPONENT","id":"component-2","ticket":1}||""', "TOMBSTONE_PRESENT"),
        (
            '{"type":"ATTR","id":"orphan","ticket":1}||{"parentId":"missing","key":"Designator","value":"U2"}|',
            "ORPHAN_ATTRIBUTE",
        ),
    ],
)
def test_synthetic_ambiguous_document_has_no_component_observations(
    extra: str, code: str
) -> None:
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"component-1","ticket":1}||'
        '{"partId":"part-1","positionX":1,"positionY":2,"rotation":0,"isMirror":false}|',
        extra,
    )
    observation = observe_jlceda_schematic_components(read_jlceda_pro_v3(source))
    assert observation.components == ()
    assert code in {issue.code for issue in observation.issues}


def test_synthetic_multiple_clients_do_not_create_semantic_fact() -> None:
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"component-1","ticket":1}||{"partId":"part-1"}|',
        header("2"),
        '{"type":"ATTR","id":"attr-1","ticket":1}||'
        '{"parentId":"component-1","key":"Designator","value":"U1"}|',
    )
    observation = observe_jlceda_schematic_components(read_jlceda_pro_v3(source))
    assert observation.components == ()
    assert "MULTIPLE_CLIENTS" in {issue.code for issue in observation.issues}


def test_synthetic_malformed_attribute_key_stays_unknown() -> None:
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"c","ticket":1}||'
        '{"partId":"p","positionX":1,"positionY":2,"rotation":0,"isMirror":false}|',
        '{"type":"ATTR","id":"a","ticket":1}||'
        '{"parentId":"c","key":["Designator"],"value":"U1"}|',
    )
    observation = observe_jlceda_schematic_components(read_jlceda_pro_v3(source))
    assert len(observation.components) == 1
    assert observation.components[0].attributes == ()
    assert "UNMAPPED_ATTRIBUTE" in {issue.code for issue in observation.issues}


def test_synthetic_invalid_component_is_loss_without_semantic_projection() -> None:
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"c","ticket":1}||'
        '{"partId":"p","positionX":"300","positionY":2,"rotation":0,"isMirror":false}|',
    )
    snapshot = read_jlceda_pro_v3(source)
    observation = observe_jlceda_schematic_components(snapshot)
    assert observation.source_sha256 == sha256(source).hexdigest()
    assert observation.components == ()
    assert [
        (issue.kind, issue.code, issue.native_id) for issue in observation.issues
    ] == [
        ("LOSS", "INVALID_COMPONENT", "c"),
    ]
    assert snapshot.documents[0].records[0].payload_json.startswith('{"partId":"p"')


def test_unknown_real_entry_format_is_inventory_only_even_when_legacy_log_parses() -> (
    None
):
    """Synthetic legacy-shaped bytes do not identify a real .eprj3 dialect."""
    source = log(
        header("1"),
        '{"type":"COMPONENT","id":"c","ticket":1}||'
        '{"partId":"p","positionX":1,"positionY":2,"rotation":0,"isMirror":false}|',
    )

    result = observe_jlceda_entry("hardware/demo.eprj3", source)

    assert result.entry_name == "hardware/demo.eprj3"
    assert result.source_sha256 == sha256(source).hexdigest()
    assert result.native_format_version == "UNKNOWN"
    assert result.components == ()
    assert [(item.kind, item.code, item.blocks_mapping) for item in result.issues] == [
        ("UNKNOWN", "NATIVE_FORMAT_UNCONFIRMED", True),
        ("UNKNOWN", "INDEX_JSON_INVALID", True),
    ]


def test_unrecognized_jlceda_bytes_remain_inventory_only() -> None:
    source = b"not a confirmed eprj3 format\n"

    result = observe_jlceda_entry("demo.eprj3", source)

    assert result.source_sha256 == sha256(source).hexdigest()
    assert result.components == ()
    assert result.native_format_version == "UNKNOWN"
    assert [issue.code for issue in result.issues] == [
        "NATIVE_FORMAT_UNCONFIRMED",
        "INDEX_JSON_INVALID",
    ]


def test_json_index_syntax_does_not_confirm_native_shape() -> None:
    for source in (
        b"{}",
        b"[]",
        b"null",
        b'{"future":{"x":[1,true]}}',
        b'\xef\xbb\xbf{"future":1}',
    ):
        result = observe_jlceda_entry("demo.EPRJ3", source)
        assert result.source_sha256 == sha256(source).hexdigest()
        assert result.native_format_version == "UNKNOWN"
        assert result.components == ()
        assert [
            (issue.kind, issue.code, issue.blocks_mapping) for issue in result.issues
        ] == [
            ("UNKNOWN", "NATIVE_FORMAT_UNCONFIRMED", True),
        ]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (b"", "INDEX_EMPTY"),
        (b" \t\r\n", "INDEX_EMPTY"),
        (b"\xef\xbb\xbf", "INDEX_EMPTY"),
        (b'{"unfinished":', "INDEX_JSON_INVALID"),
        (b"{} trailing", "INDEX_JSON_INVALID"),
        (b'{"value": NaN}', "INDEX_JSON_INVALID"),
        (b'{"value": Infinity}', "INDEX_JSON_INVALID"),
        (b'{"value": 1, "value": 2}', "INDEX_JSON_AMBIGUOUS"),
        (b"\xff", "INDEX_ENCODING_UNCONFIRMED"),
        ('{"label":"中文"}'.encode("gbk"), "INDEX_ENCODING_UNCONFIRMED"),
    ],
)
def test_index_diagnostics_preserve_source_and_unknown_native_status(
    source: bytes, code: str
) -> None:
    result = observe_jlceda_entry("demo.eprj3", source)

    assert result.source_sha256 == sha256(source).hexdigest()
    assert result.native_format_version == "UNKNOWN"
    assert result.components == ()
    assert [
        (issue.kind, issue.code, issue.blocks_mapping) for issue in result.issues
    ] == [
        ("UNKNOWN", "NATIVE_FORMAT_UNCONFIRMED", True),
        ("UNKNOWN", code, True),
    ]


def test_index_size_and_depth_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from embedded_copilot.engineering_eda.adapters import jlceda_pro_v3 as adapter

    monkeypatch.setattr(adapter, "MAX_BYTES", 4)
    source = b'{"a":1}'
    result = observe_jlceda_entry("demo.eprj3", source)
    assert result.source_sha256 == sha256(source).hexdigest()
    assert [issue.code for issue in result.issues] == [
        "NATIVE_FORMAT_UNCONFIRMED",
        "INDEX_SIZE_LIMIT_EXCEEDED",
    ]

    monkeypatch.setattr(adapter, "MAX_BYTES", 8 * 1024 * 1024)
    nested = b"[" * (adapter.MAX_DEPTH + 1) + b"0" + b"]" * (adapter.MAX_DEPTH + 1)
    result = observe_jlceda_entry("demo.eprj3", nested)
    assert result.source_sha256 == sha256(nested).hexdigest()
    assert [issue.code for issue in result.issues] == [
        "NATIVE_FORMAT_UNCONFIRMED",
        "INDEX_DEPTH_LIMIT_EXCEEDED",
    ]


def test_unsupported_extension_keeps_original_inventory_issue() -> None:
    source = b'{"valid":true}'
    result = observe_jlceda_entry("demo.other", source)
    assert result.source_sha256 == sha256(source).hexdigest()
    assert result.native_format_version == "UNKNOWN"
    assert result.components == ()
    assert [
        (issue.kind, issue.code, issue.blocks_mapping) for issue in result.issues
    ] == [
        ("UNKNOWN", "UNSUPPORTED_ENTRY_EXTENSION", True),
    ]
