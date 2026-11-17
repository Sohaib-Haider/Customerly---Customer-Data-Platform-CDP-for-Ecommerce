"""DB-backed prediction outbox. Locks survive contention; work survives restarts."""
from django.db import transaction
from apps.segments.services.execution import execute_customer
from .models import Batch, SegmentJob
from .activity import emit


def run_jobs(batch_id=None, limit=100, retry_failed=False):
    processed = 0
    query = SegmentJob.objects.filter(status__in=["pending", "failed"] if retry_failed else ["pending"])
    if batch_id is not None:
        query = query.filter(batch_id=batch_id)
    ids = list(query.order_by("batch__created_at", "customer_id", "segment_key").values_list("id", flat=True)[:limit])
    for job_id in ids:
        with transaction.atomic():
            job = SegmentJob.objects.select_for_update().select_related("batch").get(id=job_id)
            if job.status not in ({"pending", "failed"} if retry_failed else {"pending"}):
                continue
            job.attempts += 1
            try:
                # Nested savepoint: a DB failure in scoring must not poison the
                # transaction that records the failed job.
                with transaction.atomic():
                    result = execute_customer(job.segment_key, job.batch.store_id, job.customer_id, **job.options)
                data_codes = {"data_unavailable", "unsupported_population", "unsupported_currency", "missing_unit_price",
                              "inconsistent_purchase_amount", "inconsistent_source_identity", "inconsistent_purchase_date",
                              "inconsistent_tenant_history", "conflicting_invoice", "history_limit_exceeded"}
                failed = result["status"] == "unavailable" and (not result.get("reasons") or
                    any(reason["code"] not in data_codes for reason in result.get("reasons", [])))
                job.status = "failed" if failed else "completed"
                job.result = {"prediction_status": result["status"], "reasons": result.get("reasons", [])}
            except ValueError as exc:
                # The segment service refuses older CLV cutoffs. Keep the newer
                # current prediction and mark this stale work superseded.
                if job.segment_key == "clv" and str(exc).startswith("An older observation date cannot replace"):
                    job.status, job.result = "superseded", {"detail": "A newer result or incompatible CLV request prevents replacement."}
                else:
                    job.status, job.result = "failed", {"detail": "Prediction execution failed; inspect server configuration."}
            except Exception:
                job.status, job.result = "failed", {"detail": "Prediction execution failed; check model registry and database, then retry."}
            job.save()
            emit(job.batch, "segmentation", f"segmentation.job_{job.status}",
                 "Prediction attempt finished; inspect its availability and outcome.",
                 level="error" if job.status == "failed" else "success" if job.status == "completed" else "warning",
                 metadata={"job_id": str(job.id), "customer_id": str(job.customer_id), "segment": job.segment_key,
                           "status": job.status, "attempt": job.attempts, **job.result})
            processed += 1
    batches = [batch_id] if batch_id else list(set(
        SegmentJob.objects.filter(id__in=ids).values_list("batch_id", flat=True)) | set(
        Batch.objects.filter(status="segments_pending").values_list("id", flat=True)))
    for identifier in batches:
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(id=identifier)
            if batch.status not in {"segments_pending", "segments_failed", "completed"}:
                continue
            previous = batch.status
            batch.status = "segments_pending" if batch.jobs.filter(status="pending").exists() else "segments_failed" if batch.jobs.filter(status="failed").exists() else "completed"
            batch.save(update_fields=["status", "updated_at"])
            if previous != batch.status:
                emit(batch, "segmentation", "segmentation.completed" if batch.status == "completed" else "segmentation.failed",
                     "Prediction processing completed." if batch.status == "completed" else "Some prediction jobs failed; saved data is intact.",
                     level="success" if batch.status == "completed" else "error", metadata={"status": batch.status})
    return processed
