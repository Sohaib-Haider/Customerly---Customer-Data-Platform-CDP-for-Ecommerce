"""Evaluate configured data requirements without database or model access.

Keys are logical input identifiers supplied by the caller, not database names.
History counts must describe already validated records within the model's
observation window; this module does not infer dates or purchase policies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import math
from numbers import Integral, Number

import pandas as pd
from pandas.api.types import is_scalar


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Requirement keys must be nonempty strings.")


@dataclass(frozen=True)
class ValueRequirement:
    key: str
    # Enable only when the fitted model contract explicitly supports missingness.
    allow_missing: bool = False
    validator: Callable[[object], bool] | None = None

    def __post_init__(self) -> None:
        _validate_key(self.key)
        if not isinstance(self.allow_missing, bool):
            raise TypeError("allow_missing must be a Boolean.")
        if self.validator is not None and not callable(self.validator):
            raise TypeError("validator must be callable.")


@dataclass(frozen=True)
class HistoryRequirement:
    key: str
    minimum_records: int = 1

    def __post_init__(self) -> None:
        _validate_key(self.key)
        if (isinstance(self.minimum_records, bool)
                or not isinstance(self.minimum_records, Integral)
                or self.minimum_records < 1):
            raise ValueError("minimum_records must be a positive integer.")


@dataclass(frozen=True)
class EligibilityPolicy:
    values: tuple[ValueRequirement, ...] = ()
    histories: tuple[HistoryRequirement, ...] = ()

    def __post_init__(self) -> None:
        # Freeze incoming lists as well, so later edits cannot change a policy.
        object.__setattr__(self, "values", tuple(self.values))
        object.__setattr__(self, "histories", tuple(self.histories))
        if not self.values and not self.histories:
            raise ValueError("An eligibility policy must define at least one requirement.")
        for requirements, kind in ((self.values, ValueRequirement), (self.histories, HistoryRequirement)):
            if any(not isinstance(requirement, kind) for requirement in requirements):
                raise TypeError("Policy entries must be requirement objects of the appropriate type.")
            keys = [requirement.key for requirement in requirements]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate requirement keys in an eligibility policy.")


@dataclass(frozen=True)
class EligibilityIssue:
    key: str
    code: str
    message: str


@dataclass(frozen=True)
class EligibilityResult:
    issues: tuple[EligibilityIssue, ...] = ()
    allowed_missing: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict:
        # Report requirement identifiers and reasons, never customer values.
        return {
            "eligible": self.eligible,
            "reasons": [asdict(issue) for issue in self.issues],
            "allowed_missing": list(self.allowed_missing),
        }


def check_eligibility(
    values: Mapping[str, object],
    policy: EligibilityPolicy,
    *,
    history_counts: Mapping[str, int] | None = None,
) -> EligibilityResult:
    """Check one customer's logical inputs; do not mutate or fill their data.

    Absent keys mean unavailable input. Present null/blank values may be allowed
    explicitly by the fitted contract, but absent keys are always reported.
    Scalar zero and False are present values. Optional validators enforce the
    model's types, ranges or other semantics. Validator errors propagate as
    programming/configuration errors, rather than being hidden as missing data.
    """
    issues: list[EligibilityIssue] = []
    allowed_missing: list[str] = []
    counts = history_counts if history_counts is not None else {}

    for requirement in policy.values:
        key = requirement.key
        if key not in values:
            issues.append(EligibilityIssue(key, "missing_input", "Required input was not provided."))
            continue
        value = values[key]
        if not is_scalar(value):
            issues.append(EligibilityIssue(key, "invalid_value", "Expected a single input value."))
            continue
        missing = value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))
        if missing:
            if requirement.allow_missing:
                allowed_missing.append(key)
            else:
                issues.append(EligibilityIssue(key, "missing_value", "Required input has no usable value."))
            continue
        if isinstance(value, Number):
            try:
                finite = math.isfinite(value)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                issues.append(EligibilityIssue(key, "invalid_value", "Input must be a finite real value."))
                continue
        if requirement.validator is not None and not requirement.validator(value):
            issues.append(EligibilityIssue(key, "invalid_value", "Input does not satisfy its configured rule."))

    for requirement in policy.histories:
        key = requirement.key
        if key not in counts:
            issues.append(EligibilityIssue(key, "missing_history", "Required history has not been supplied."))
            continue
        count = counts[key]
        if isinstance(count, bool) or not isinstance(count, Integral) or count < 0:
            issues.append(EligibilityIssue(key, "invalid_history", "History count must be a nonnegative integer."))
        elif count < requirement.minimum_records:
            issues.append(EligibilityIssue(key, "insufficient_history", "Not enough validated history records."))

    return EligibilityResult(tuple(issues), tuple(allowed_missing))
