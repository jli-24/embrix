from __future__ import annotations

from argparse import Namespace

import pytest

from scripts.v71_greenfield_provider_answer_golden import (
    GoldenUnverified,
    _QUESTIONS,
    _redacted_report,
    _terminal_diagnostics,
    _validate_projection,
    _validate_trace,
    run_live,
)


def _trace(**gateway_updates: object) -> list[dict[str, object]]:
    values = {
        "ROUTE_SELECT": {"route": "GREENFIELD_DISCOVERY"},
        "SOURCE_SNAPSHOT": {"gate_result": "EMPTY_PROJECT"},
        "EVIDENCE_GATE": {"gate_result": "NOT_APPLICABLE_FOR_GREENFIELD"},
        "PROVIDER_RESOLVE": {"gate_result": "AVAILABLE"},
        "MODEL_GATEWAY": {"gate_result": "PASS", "gateway_invocation_count": 1},
        "ANSWER_PERSIST": {"reason_code": "ANSWER_COMPLETED"},
        "SSE_TERMINAL": {"terminal_event": "ANSWER_COMPLETED"},
        "UI_PROJECTION": {"gate_result": "PASS"},
    }
    values["MODEL_GATEWAY"].update(gateway_updates)
    return [{"stage": stage, **fields} for stage, fields in values.items()]


def _item(category: str, value: str, *, provenance: str = "USER_UTTERANCE", status: str = "CONFIRMED") -> dict[str, str]:
    return {
        "category": category,
        "normalized_value": value,
        "provenance": provenance,
        "status": status,
    }


def _detail(turn_count: int) -> dict[str, object]:
    requirements = [_item("TARGET_PLATFORM", "STM32")]
    if turn_count >= 2:
        requirements.append(_item("PRODUCT_GOAL", "温控器"))
    if turn_count >= 3:
        requirements.extend(
            (
                _item("CONTROL_MODE", "HEATING"),
                _item("ACCURACY", "±0.5°C"),
            )
        )
    turns = [
        {
            "request_id": f"request-{index}",
            "route": "GREENFIELD_DISCOVERY",
            "status": "COMPLETED",
            "answer_text": f"answer-{index}",
            "error_code": None,
            "analysis_state": "REQUIREMENTS_PARTIAL",
            "terminal_event": "ANSWER_COMPLETED",
        }
        for index in range(1, turn_count + 1)
    ]
    return {
        "turns": turns,
        "requirement_context": {
            "standard_fields": (
                [{"category": "PRODUCT_GOAL", "status": "UNKNOWN"}]
                if turn_count == 1
                else []
            ),
            "requirements": requirements,
            "constraints": [],
            "proposed_items": [
                _item(
                    "RTOS",
                    "FreeRTOS",
                    provenance="PROVIDER_RESPONSE",
                    status="PROPOSED",
                )
            ],
            "open_questions": [{"category": "PRODUCT_GOAL"}],
            "fingerprint": "sha256:" + "0" * 64,
        },
    }


def test_v71_golden_trace_requires_accepted_single_gateway_invocation() -> None:
    diagnostics = _validate_trace(_trace())

    assert diagnostics == {
        "route": "GREENFIELD_DISCOVERY",
        "source_snapshot": "EMPTY_PROJECT",
        "evidence_gate": "NOT_APPLICABLE_FOR_GREENFIELD",
        "provider_resolve": "AVAILABLE",
        "gateway_invocation_count": 1,
        "answer_persist": "PASS",
    }

    with pytest.raises(GoldenUnverified):
        _validate_trace(_trace(gateway_invocation_count=0))


def test_v71_golden_requires_completed_xor_failed_terminal() -> None:
    events = [
        {
            "event": "ANSWER_COMPLETED",
            "data": {"request_id": "request-1"},
        }
    ]
    assert _terminal_diagnostics(events, "request-1") == {
        "answer_completed_count": 1,
        "answer_failed_count": 0,
    }

    events.append(
        {"event": "ANSWER_FAILED", "data": {"request_id": "request-1"}}
    )
    with pytest.raises(GoldenUnverified):
        _terminal_diagnostics(events, "request-1")


@pytest.mark.parametrize("turn_count", (1, 2, 3))
def test_v71_golden_validates_incremental_requirement_context(turn_count: int) -> None:
    diagnostics = _validate_projection(
        _detail(turn_count),
        request_id=f"request-{turn_count}",
        expected_turn_count=turn_count,
    )

    assert diagnostics["answer_text_present"] is True
    assert diagnostics["provider_confirmed_pollution"] is False


def test_v71_golden_rejects_provider_confirmed_pollution() -> None:
    detail = _detail(1)
    context = detail["requirement_context"]
    assert isinstance(context, dict)
    context["proposed_items"] = [
        _item(
            "RTOS",
            "FreeRTOS",
            provenance="PROVIDER_RESPONSE",
            status="CONFIRMED",
        )
    ]

    with pytest.raises(GoldenUnverified):
        _validate_projection(
            detail,
            request_id="request-1",
            expected_turn_count=1,
        )


def test_v71_live_golden_fails_before_io_without_exact_three_call_approval() -> None:
    args = Namespace(
        approve_external_transmission=False,
        expected_transmissions=0,
    )

    with pytest.raises(GoldenUnverified, match="exactly three"):
        run_live(args)


def test_v71_golden_fixed_questions_and_safe_report_contract() -> None:
    assert _QUESTIONS == (
        "stm32",
        "做一个温控器",
        "只控制加热，精度 ±0.5°C",
    )

    assert _redacted_report(
        {
            "status": "PASS",
            "session_id": "session-secret",
            "nonce": "nonce-secret",
            "nested": {"authorization": "secret", "count": 1},
        }
    ) == {"status": "PASS", "nested": {"count": 1}}
