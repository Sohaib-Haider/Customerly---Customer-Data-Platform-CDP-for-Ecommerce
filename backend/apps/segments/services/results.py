"""In-memory results shared by predictors; no persistence contract is assumed."""

from dataclasses import asdict, dataclass, field

from .eligibility import EligibilityIssue, EligibilityResult


class SegmentError(Exception):
    """An expected availability problem, with a value-free explanation."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PredictionResult:
    segment: str
    status: str
    prediction: dict | None = None
    eligibility: EligibilityResult | None = None
    reasons: tuple[EligibilityIssue, ...] = ()
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "segment": self.segment,
            "status": self.status,
            "prediction": self.prediction,
            "eligibility": self.eligibility.as_dict() if self.eligibility is not None else None,
            "reasons": [asdict(reason) for reason in self.reasons],
            "metadata": self.metadata,
        }


def unavailable(segment, code, message, *, eligibility=None, metadata=None):
    return PredictionResult(
        segment=segment,
        status="unavailable",
        eligibility=eligibility,
        reasons=(EligibilityIssue(segment, code, message),),
        metadata=metadata or {},
    )
