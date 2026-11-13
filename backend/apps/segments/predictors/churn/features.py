"""Validate and order raw inputs before applying the saved fitted preparation."""

import numpy as np
import pandas as pd

from ...services.eligibility import check_eligibility
from .contract import FEATURES, POLICY


def prepare_inputs(values):
    eligibility = check_eligibility(values, POLICY)
    if not eligibility.eligible:
        return None, eligibility
    return pd.DataFrame([{key: np.nan if key in eligibility.allowed_missing else float(values[key])
                          for key in FEATURES}], columns=FEATURES), eligibility
