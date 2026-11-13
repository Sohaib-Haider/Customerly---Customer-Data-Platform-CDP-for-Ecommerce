"""Prepare prediction inputs inside segments without changing stored data."""

from dataclasses import dataclass
from decimal import Decimal
from numbers import Number

import numpy as np
import pandas as pd
from pandas.api.types import is_scalar


@dataclass(frozen=True)
class FittedNumericPreparation:
    features: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def __post_init__(self):
        for name in ("features", "medians", "means", "scales"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("Saved features must be nonempty and unique.")
        for name in ("medians", "means", "scales"):
            values = np.asarray(getattr(self, name), dtype=float)
            if values.shape != (len(self.features),) or not np.isfinite(values).all():
                raise ValueError("Saved numeric statistics must be finite and match the feature order.")
        if (np.asarray(self.scales, dtype=float) <= 0).any():
            raise ValueError("Saved scales must be positive; constant training features use scale 1.")


def apply_fitted_numeric(frame: pd.DataFrame, preparation: FittedNumericPreparation) -> pd.DataFrame:
    """Fill with saved medians, then apply (value - saved mean) / saved scale.

    Never infer columns, estimate statistics, drop rows, or run the generic
    cleaning pipeline here. Return only the model features, in training order.
    """
    if any(key not in frame for key in preparation.features):
        raise ValueError("Required fitted-preparation inputs are missing.")
    selected = frame.loc[:, list(preparation.features)].copy()
    for key in preparation.features:
        for value in selected[key]:
            if not is_scalar(value):
                raise ValueError("Fitted numeric preparation requires one value per input.")
            if pd.isna(value):
                continue
            if not isinstance(value, (Number, Decimal, np.bool_)) or isinstance(value, complex):
                raise ValueError("Fitted numeric preparation requires numeric or missing inputs.")
    try:
        values = selected.to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Fitted numeric preparation could not represent the inputs.") from None
    if np.isinf(values).any():
        raise ValueError("Infinite model inputs are unsupported.")
    medians = np.asarray(preparation.medians, dtype=float)
    values = np.where(np.isnan(values), medians, values)
    transformed = (values - np.asarray(preparation.means, dtype=float)) / np.asarray(preparation.scales, dtype=float)
    if not np.isfinite(transformed).all():
        raise ValueError("Saved preprocessing produced invalid values.")
    return pd.DataFrame(transformed, columns=preparation.features, index=frame.index)
