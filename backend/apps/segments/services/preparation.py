"""Build audience memberships from saved model results, without rescoring."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import CustomerMembership, SegmentPrediction
from .logging import after_commit, logged, progress


@logged('audience_assignment')
def assign_saved(store_id, key, clv_threshold, after=None):
    rows = SegmentPrediction.objects.filter(store_id=store_id, customer__store_id=store_id,
        segment_key=key, status='predicted').order_by('id')
    if after:
        rows = rows.filter(id__gt=after)
    with transaction.atomic():
        page = list(rows.select_for_update()[:100])
        progress('audience.results.loaded', 'Loaded saved predictions for audience assignment; models will not be rerun.', segment=key, count=len(page))
        for row in page:
            prediction = row.result.get('prediction') or {}
            if key == 'churn':
                # Use the classifier's actual label, not an invented probability cutoff.
                if prediction.get('predicted_class') not in (0, 1):
                    progress('audience.assignment.skipped', 'Saved churn result has no usable classifier label.', customer_id=row.customer_id)
                    continue
                member = prediction['predicted_class'] == 1
                score = Decimal(str(prediction['churn_probability']))
            else:
                if prediction.get('predicted_revenue') is None:
                    progress('audience.assignment.skipped', 'Saved CLV result has no predicted revenue.', customer_id=row.customer_id)
                    continue
                member = Decimal(str(prediction['predicted_revenue'])) >= clv_threshold
                score = None
            lookup = dict(store_id=store_id, customer_id=row.customer_id, segment_id=row.segment_id)
            if member:
                CustomerMembership.objects.update_or_create(**lookup,
                    defaults={'score': score, 'assigned_at': timezone.now()})
            else:
                CustomerMembership.objects.filter(**lookup).delete()
            row.result['membership_policy'] = {'requested': True, 'updated': True,
                'rule': 'predicted_class_1' if key == 'churn' else 'predicted_revenue_at_least',
                'threshold': None if key == 'churn' else str(clv_threshold)}
            row.result['membership'] = {'updated': True, 'is_member': member}
            # Preserve the original model output and its computation timestamp.
            row.save(update_fields=['result'])
            progress('audience.assignment.pending', 'Membership decision written; waiting for transaction commit.',
                     segment=key, customer_id=row.customer_id, included=member)
        after_commit('audience.assignment.committed', 'Audience assignment batch committed.', segment=key, count=len(page))
    more = bool(page) and rows.filter(id__gt=page[-1].id).exists()
    progress('audience.batch.summary', 'Audience batch processed.', segment=key, processed=len(page), remaining=more)
    return {'processed': len(page), 'remaining': more,
            'after': str(page[-1].id) if more else None}
