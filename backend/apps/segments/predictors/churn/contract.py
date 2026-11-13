"""Names here are estimator input labels, not database field mappings."""

import numpy as np

from ...services.eligibility import EligibilityPolicy, ValueRequirement
from ...services.validation import numeric

FEATURES = (
    "Tenure", "WarehouseToHome", "NumberOfDeviceRegistered",
    "SatisfactionScore", "Complain", "CashbackAmount",
)


def complaint(value):
    return isinstance(value, (bool, np.bool_)) or (numeric(value) and value in (0, 1))


# Permit missingness only in inputs observed missing in the original source.
# All other attributes must have usable data; do not invent an entire profile.
POLICY = EligibilityPolicy(values=tuple(
    ValueRequirement(name, allow_missing=name in {"Tenure", "WarehouseToHome"},
                     validator=complaint if name == "Complain" else numeric)
    for name in FEATURES
))
