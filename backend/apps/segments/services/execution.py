"""Run, persist, and optionally update explicit memberships in one transaction."""

from decimal import Decimal
import hashlib

from django.db import connection, transaction
from django.utils import timezone

from ..models import CustomerMembership, SegmentPrediction
from ..predictors.clv.features import observation_cutoff
from .catalog import get_definition, registry_matches
from .database import churn_inputs, clv_purchases, get_customer
from .results import SegmentError, unavailable
from .runner import run_segment


def execute_customer(key, store_id, customer_id, *, observation_date=None, currency=None,
                     membership_threshold=None, update_memberships=False, persist=True):
    if update_memberships and (not persist or membership_threshold is None):
        raise ValueError("Membership updates require persistence and an explicit threshold.")
    if membership_threshold is not None:
        threshold = Decimal(str(membership_threshold))
        if not threshold.is_finite() or threshold < 0 or (key == "churn" and threshold > 1):
            raise ValueError("The membership threshold is outside the segment's allowed range.")
    if key == "clv":
        if currency != "GBP":
            raise ValueError("CLV requires an explicit GBP currency declaration.")
        try:
            observation_date = observation_cutoff(observation_date).date()
        except SegmentError:
            raise ValueError("CLV requires an explicit source-local observation date.") from None
    with transaction.atomic():
        if persist and connection.vendor == "postgresql":
            lock_id = int.from_bytes(hashlib.sha256(f"segments:{store_id}:{key}:{customer_id}".encode()).digest()[:8], "big", signed=True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
        customer = get_customer(store_id, customer_id)
        definition = get_definition(key)
        if persist and key == "clv":
            previous_date = SegmentPrediction.objects.filter(store_id=store_id, customer=customer, segment=definition).values_list("observation_date", flat=True).first()
            if previous_date is not None and observation_date < previous_date:
                raise ValueError("An older observation date cannot replace a newer stored prediction; use persist=false for historical evaluation.")
        try:
            if key == "churn":
                result = run_segment(key, values=churn_inputs(customer))
            else:
                result = run_segment(key, purchases=clv_purchases(customer, observation_date),
                                     observation_date=observation_date, currency=currency)
        except SegmentError as exc:
            result = unavailable(key, exc.code, str(exc))
        digest = result.metadata.get("artifact_sha256")
        if result.status == "predicted" and not registry_matches(definition, digest):
            result = unavailable(key, "model_registry_mismatch", "Initialize the registry for the exact local model before scoring.")
        payload = result.as_dict()
        membership = {"updated": False, "is_member": None}
        if persist:
            data_failures = {"data_unavailable", "unsupported_population", "missing_unit_price",
                             "inconsistent_purchase_amount", "inconsistent_source_identity", "inconsistent_purchase_date", "inconsistent_tenant_history", "conflicting_invoice"}
            # Infrastructure/model errors must not erase a previously valid audience.
            can_update = result.status == "predicted" or any(reason.code in data_failures for reason in result.reasons)
            if update_memberships and can_update:
                value = (result.prediction.get("churn_probability") if key == "churn" else result.prediction.get("predicted_revenue")) if result.prediction else None
                is_member = result.status == "predicted" and Decimal(str(value)) >= threshold
                if is_member:
                    CustomerMembership.objects.update_or_create(store_id=store_id, customer=customer, segment=definition,
                        defaults={"score": Decimal(str(value)) if key == "churn" else None, "assigned_at": timezone.now()})
                else:
                    CustomerMembership.objects.filter(store_id=store_id, customer=customer, segment=definition).delete()
                membership = {"updated": True, "is_member": is_member}
            payload["membership_policy"] = {"requested": update_memberships, "updated": membership["updated"],
                "threshold": str(membership_threshold) if update_memberships else None}
            payload["membership"] = membership
            SegmentPrediction.objects.update_or_create(store_id=store_id, customer=customer, segment=definition,
                defaults={"segment_key": key, "status": result.status, "result": payload,
                          "observation_date": observation_date if key == "clv" else None})
        return {"customer_id": str(customer.id), "persisted": persist, "membership": membership, **payload}
