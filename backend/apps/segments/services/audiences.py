"""Read actual memberships, not every customer with a prediction."""
import json
import uuid
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import Max
from django.http import Http404

from ..models import Customer, CustomerMembership, SegmentPrediction
from .catalog import DEFINITIONS, get_definition
from .database import db_uuid


def definition(key):
    from ..models import SegmentDefinition
    if key not in DEFINITIONS:
        raise Http404
    try:
        return get_definition(key)
    except SegmentDefinition.DoesNotExist:
        return None


def describe(store_id, key):
    from apps.ingestion.models import SegmentJob
    item = definition(key)
    customers = Customer.objects.filter(store_id=store_id)
    memberships = CustomerMembership.objects.filter(store_id=store_id, customer__store_id=store_id, segment=item) if item else CustomerMembership.objects.none()
    predictions = SegmentPrediction.objects.filter(store_id=store_id, customer__store_id=store_id, segment=item) if item else SegmentPrediction.objects.none()
    member_count, total = memberships.count(), customers.count()
    predicted, unavailable = predictions.filter(status="predicted").count(), predictions.filter(status="unavailable").count()
    configured = predictions.filter(result__membership_policy__requested=True).exists()
    pending = SegmentJob.objects.filter(batch__store_id=store_id, segment_key=key, status="pending").count()
    state = ("unregistered" if item is None else "processing" if pending else "ready" if member_count else
             "not_configured" if not configured else "unavailable" if unavailable and not predicted else "empty")
    return {"key": key, "name": DEFINITIONS[key]["name"], "description": DEFINITIONS[key]["description"],
            "registered": item is not None, "id": str(item.id) if item else None, "state": state,
            "member_count": member_count, "total_customers": total, "predicted_count": predicted,
            "unavailable_count": unavailable, "not_scored_count": max(0, total - predicted - unavailable),
            "membership_configured": configured, "pending_jobs": pending,
            "last_computed_at": predictions.aggregate(value=Max("computed_at"))["value"],
            "last_membership_at": memberships.aggregate(value=Max("assigned_at"))["value"],
            "campaign": {"enabled": False, "label": "Create campaign", "reason": "Coming soon"},
            "note": "Membership uses saved assignments and explicit per-customer policies. Audiences may overlap."}


def members(store_id, key, *, limit, offset, search="", source=None):
    item = definition(key)
    if item is None:
        return None
    params = [db_uuid(store_id), db_uuid(item.id), db_uuid(store_id)]
    where = "cs.store_id = %s AND cs.segment_id = %s AND c.store_id = %s"
    if search:
        escaped = search.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        where += " AND (lower(c.source_customer_id) LIKE %s ESCAPE '!' OR lower(c.email) LIKE %s ESCAPE '!' OR lower(c.phone_number) LIKE %s ESCAPE '!')"
        params += [f"%{escaped}%"] * 3
    if source:
        escaped = source.replace("_", "!_")
        where += " AND c.source_customer_id LIKE %s ESCAPE '!'"
        params.append(escaped + ":%")
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM customer_segments cs JOIN customers c ON c.id = cs.customer_id WHERE " + where, params)
        count = cursor.fetchone()[0]
        cursor.execute("""SELECT c.id, c.source_customer_id, c.email, c.phone_number, cs.score, cs.assigned_at,
            sp.status, sp.observation_date, sp.computed_at, sp.result
            FROM customer_segments cs JOIN customers c ON c.id = cs.customer_id
            LEFT JOIN segment_predictions sp ON sp.customer_id = c.id AND sp.store_id = cs.store_id AND sp.segment_id = cs.segment_id
            WHERE """ + where + " ORDER BY cs.assigned_at DESC, c.id LIMIT %s OFFSET %s", [*params, limit, offset])
        results = []
        for customer_id, source_id, email, phone, score, assigned, status, cutoff, computed, result in cursor.fetchall():
            if isinstance(result, str):
                result = json.loads(result)
            results.append({"customer_id": str(uuid.UUID(str(customer_id))), "source_customer_id": source_id,
                "email": email, "phone_number": phone, "score": score, "assigned_at": assigned,
                "prediction": {"status": status, "observation_date": cutoff, "computed_at": computed, "result": result} if status else None})
    return json.loads(json.dumps({"audience": key, "count": count, "limit": limit, "offset": offset, "results": results}, cls=DjangoJSONEncoder))
