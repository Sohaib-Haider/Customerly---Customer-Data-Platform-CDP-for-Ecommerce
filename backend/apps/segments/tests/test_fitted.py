"""Saved statistics must produce the same value regardless of upload batch."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from apps.segments.services.fitted import FittedNumericPreparation, apply_fitted_numeric


@pytest.fixture
def preparation():
    return FittedNumericPreparation(("input_a", "input_b"), (4., 2.), (2., 1.), (2., 1.))


def test_uses_saved_statistics_and_preserves_input(preparation):
    frame = pd.DataFrame({"input_b": [False, None], "input_a": [Decimal("6"), None], "extra": [99, 88]}, index=[7, 9])
    before = frame.copy(deep=True)
    result = apply_fitted_numeric(frame, preparation)
    np.testing.assert_allclose(result, [[2., -1.], [1., 1.]])
    assert list(result.columns) == ["input_a", "input_b"]
    assert list(result.index) == [7, 9]
    pd.testing.assert_frame_equal(frame, before)


def test_prediction_input_does_not_depend_on_batch(preparation):
    one = pd.DataFrame({"input_a": [6.], "input_b": [2.]})
    many = pd.concat([one, pd.DataFrame({"input_a": [99999.], "input_b": [-99999.]})], ignore_index=True)
    pd.testing.assert_frame_equal(apply_fitted_numeric(one, preparation), apply_fitted_numeric(many, preparation).iloc[:1])


@pytest.mark.parametrize("value", ["6", float("inf"), complex(1, 2), [1, 2]])
def test_invalid_inputs_are_not_coerced(preparation, value):
    with pytest.raises(ValueError):
        apply_fitted_numeric(pd.DataFrame({"input_a": [value], "input_b": [1]}), preparation)


def test_missing_column_and_invalid_statistics_are_rejected(preparation):
    with pytest.raises(ValueError):
        apply_fitted_numeric(pd.DataFrame({"input_a": [1]}), preparation)
    for scale in (0., -1., float("nan")):
        with pytest.raises(ValueError):
            FittedNumericPreparation(("input_a",), (1.,), (1.,), (scale,))
