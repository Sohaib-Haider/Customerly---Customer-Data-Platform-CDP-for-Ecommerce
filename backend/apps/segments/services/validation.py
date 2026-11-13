"""Input checks shared by model contracts; these perform no coercion."""

from decimal import Decimal
from numbers import Real

import numpy as np


def numeric(value):
    return not isinstance(value, (bool, np.bool_)) and isinstance(value, (Real, Decimal))
