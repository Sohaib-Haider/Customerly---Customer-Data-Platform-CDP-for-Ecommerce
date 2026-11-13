"""Eligibility contracts use synthetic logical keys, not database fields."""

from copy import deepcopy
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from apps.segments.services.eligibility import (
    EligibilityPolicy,
    HistoryRequirement,
    ValueRequirement,
    check_eligibility,
)


@pytest.mark.parametrize("value", [0, False, Decimal("0"), np.int64(0), "present"])
def test_present_values_are_not_treated_as_missing(value):
    policy = EligibilityPolicy(values=(ValueRequirement("input_a"),))
    assert check_eligibility({"input_a": value}, policy).eligible


@pytest.mark.parametrize("value", [None, "", "  ", float("nan"), pd.NA, pd.NaT, Decimal("NaN")])
def test_null_or_blank_values_are_unavailable(value):
    result = check_eligibility({"input_a": value}, EligibilityPolicy(values=(ValueRequirement("input_a"),)))
    assert not result.eligible
    assert result.issues[0].code == "missing_value"


def test_absent_input_is_distinct_from_supported_missing_value():
    policy = EligibilityPolicy(values=(ValueRequirement("input_a", allow_missing=True),))
    assert check_eligibility({}, policy).issues[0].code == "missing_input"
    result = check_eligibility({"input_a": None}, policy)
    assert result.eligible
    assert result.allowed_missing == ("input_a",)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), Decimal("Infinity"), 1j, [], {}, [1, 2]])
def test_invalid_values_are_not_eligible_even_if_missing_is_supported(value):
    policy = EligibilityPolicy(values=(ValueRequirement("input_a", allow_missing=True),))
    result = check_eligibility({"input_a": value}, policy)
    assert not result.eligible
    assert result.issues[0].code == "invalid_value"


def test_configured_validator_checks_type_and_range_without_coercion():
    policy = EligibilityPolicy(values=(ValueRequirement(
        "input_a", validator=lambda value: type(value) is int and 0 <= value <= 10,
    ),))
    for value in ("5", -1, 11, True):
        assert not check_eligibility({"input_a": value}, policy).eligible
    assert check_eligibility({"input_a": 5}, policy).eligible


def test_history_threshold_and_missing_history():
    policy = EligibilityPolicy(histories=(HistoryRequirement("window_a", minimum_records=2),))
    assert check_eligibility({}, policy).issues[0].code == "missing_history"
    for count in (0, 1):
        result = check_eligibility({}, policy, history_counts={"window_a": count})
        assert result.issues[0].code == "insufficient_history"
    assert check_eligibility({}, policy, history_counts={"window_a": 2}).eligible
    assert check_eligibility({}, policy, history_counts={"window_a": np.int64(3)}).eligible


@pytest.mark.parametrize("count", [None, True, -1, 1.5, "2"])
def test_invalid_history_count_does_not_pass(count):
    policy = EligibilityPolicy(histories=(HistoryRequirement("window_a"),))
    assert check_eligibility({}, policy, history_counts={"window_a": count}).issues[0].code == "invalid_history"


def test_all_requirements_must_pass_and_all_reasons_are_returned():
    policy = EligibilityPolicy(
        values=(ValueRequirement("input_a"), ValueRequirement("input_b")),
        histories=(HistoryRequirement("window_a"),),
    )
    result = check_eligibility({"input_b": None}, policy, history_counts={"window_a": 0})
    assert not result.eligible
    assert [issue.code for issue in result.issues] == ["missing_input", "missing_value", "insufficient_history"]


def test_one_segment_can_pass_while_another_is_unavailable():
    values = {"input_a": 4}
    snapshot_policy = EligibilityPolicy(values=(ValueRequirement("input_a"),))
    history_policy = EligibilityPolicy(histories=(HistoryRequirement("window_a"),))
    assert check_eligibility(values, snapshot_policy).eligible
    assert not check_eligibility(values, history_policy).eligible


def test_inputs_are_preserved_and_reasons_do_not_expose_values():
    values = {"input_a": "private-customer-value", "extra": 2}
    before = deepcopy(values)
    policy = EligibilityPolicy(values=(ValueRequirement("input_a", validator=lambda value: False),))
    result = check_eligibility(values, policy)
    assert values == before
    assert "private-customer-value" not in str(result.as_dict())
    assert result.as_dict()["reasons"][0]["key"] == "input_a"


def test_invalid_policy_fails_instead_of_qualifying_every_customer():
    with pytest.raises(ValueError):
        EligibilityPolicy()
    with pytest.raises(ValueError):
        EligibilityPolicy(values=(ValueRequirement("input_a"), ValueRequirement("input_a")))
    with pytest.raises(ValueError):
        HistoryRequirement("window_a", minimum_records=0)
    with pytest.raises(ValueError):
        ValueRequirement("")


def test_validator_programming_errors_are_not_silenced():
    def broken_validator(value):
        raise RuntimeError("Incorrect model rule")

    with pytest.raises(RuntimeError, match="Incorrect model rule"):
        check_eligibility({"input_a": 1}, EligibilityPolicy(values=(ValueRequirement("input_a", validator=broken_validator),)))
