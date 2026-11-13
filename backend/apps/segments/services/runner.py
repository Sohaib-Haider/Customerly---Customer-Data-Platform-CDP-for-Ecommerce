"""Execute one customer and segment in memory, without database or API access."""

from collections.abc import Mapping

from .results import unavailable


def run_segment(segment, *, values=None, purchases=None, observation_date=None,
                currency=None):
    if segment == "churn":
        if not isinstance(values, Mapping):
            return unavailable("churn", "invalid_input", "Provide a mapping of the churn model's customer inputs.")
        from ..predictors.churn.predictor import predict

        return predict(values)
    if segment == "clv":
        from ..predictors.clv.predictor import predict

        return predict(purchases, observation_date, currency=currency)
    return unavailable(str(segment), "unknown_segment", "Only churn and CLV are configured.")
