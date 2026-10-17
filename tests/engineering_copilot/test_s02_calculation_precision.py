"""Exact arithmetic of the existing synthetic RC rule; no device qualification."""

from __future__ import annotations

from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    Decimal,
    Inexact,
    localcontext,
)

import pytest

from embedded_copilot.engineering_copilot.schematic_evidence import finite_rc_decoupling


def _inputs(**changes: str) -> dict[str, Decimal]:
    values = {
        "r_ohm": "10000", "r_tolerance": ".01", "cbus_f": ".00000000005",
        "rise_limit_s": ".000001", "frequency_hz": "100000",
        "frequency_limit_hz": "100000", "c_effective_f": ".0000001",
        "c_required_f": ".00000008", "vol_max_v": ".4", "iol_min_a": ".003",
        "leakage_max_a": ".000001", "vil_max_v": ".8", "vmax_v": "3.4",
    }
    return {key: Decimal(value) for key, value in {**values, **changes}.items()}


@pytest.mark.parametrize("precision", [6, 28, 80])
@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_CEILING])
@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, True),
        # 0.8473 * 10100 * 5e-11 is exactly 0.0000004278865.
        ({"rise_limit_s": ".0000004278865"}, True),
        ({"rise_limit_s": ".00000042788649"}, False),
        # 3 / 9900 exceeds 0.00030303, however it is rounded.
        ({"iol_min_a": ".00030303"}, False),
        # 0.4 + 10100 * 0.000001000001 is exactly 0.4101000101.
        ({"leakage_max_a": ".000001000001", "vil_max_v": ".41010001009999"}, False),
        ({"c_effective_f": ".000000079999999999999999"}, False),
    ],
)
def test_existing_rule_is_independent_of_decimal_context(
    precision: int, rounding: str, changes: dict[str, str], expected: bool,
) -> None:
    values = _inputs(**changes)
    before = dict(values)
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        assert finite_rc_decoupling(values) is expected
    assert values == before


def test_inexact_decimal_trap_does_not_turn_valid_inputs_into_exception() -> None:
    values = _inputs()
    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        assert finite_rc_decoupling(values) is True


def test_existing_rule_does_not_set_callers_decimal_flags() -> None:
    values = _inputs()
    with localcontext() as context:
        context.clear_flags()
        before = context.flags.copy()
        assert finite_rc_decoupling(values) is True
        assert context.flags == before


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "-1"])
def test_invalid_inputs_still_fail_closed(value: str) -> None:
    assert finite_rc_decoupling(_inputs(c_effective_f=value)) is False


def test_missing_or_extra_inputs_still_fail_closed() -> None:
    values = _inputs()
    del values["c_required_f"]
    assert finite_rc_decoupling(values) is False
    assert finite_rc_decoupling({**_inputs(), "unapproved_input": Decimal(0)}) is False


def test_default_precision_does_not_hide_positive_product_remainder() -> None:
    # (1 + 1e-24)^2 is strictly larger than 1 + 2e-24: the 1e-48 term
    # must not vanish into a passing low-level voltage comparison.
    values = _inputs(
        r_ohm="1.000000000000000000000001",
        r_tolerance=".000000000000000000000001",
        leakage_max_a="1", vol_max_v="0", iol_min_a="10",
        vil_max_v="1.000000000000000000000002",
    )
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        assert finite_rc_decoupling(values) is False
