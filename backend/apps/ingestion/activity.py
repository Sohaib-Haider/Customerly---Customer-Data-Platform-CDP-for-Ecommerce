"""Durable stage attempts and truthful reports, consumable by cursor polling."""
from contextlib import contextmanager
from datetime import timedelta
import uuid

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from .models import ActivityEvent, Batch

SAVED = {"saved", "completed", "segments_pending", "segments_failed"}


def emit(batch, stage, code, message, *, level="info", metadata=None, operation_id=None):
    return ActivityEvent.objects.create(batch=batch, stage=stage, code=code, message=message,
        level=level, metadata=metadata or {}, operation_id=operation_id or batch.active_operation_id)


@contextmanager
def operation(principal, batch_id, revision, stage):
    """Commit a visible start before the long action, then a durable outcome.

    A lease prevents two HTTP actions claiming the same batch. A crashed
    request can be retried after the lease; canonical transaction locks and
    revision validation remain authoritative while any transaction is alive.
    """
    from .services import Conflict, batch_for, check_revision, writable
    token = None
    with transaction.atomic():
        batch = batch_for(principal, batch_id, lock=True)
        check_revision(batch, revision)
        saved_replay = stage == "saving" and batch.status in SAVED
        if not saved_replay:
            writable(batch)
            if stage == "preprocessing" and not batch.review:
                raise Conflict("Review the mappings before preprocessing.")
            if stage == "saving" and batch.status != "validated":
                raise Conflict("Validate the current review before saving.")
            now = timezone.now()
            timeout = timedelta(seconds=getattr(settings, "INGESTION_OPERATION_TIMEOUT_SECONDS", 1800))
            if batch.active_operation_id:
                if batch.operation_started_at and now - batch.operation_started_at < timeout:
                    raise Conflict("A batch operation is already running. Poll its events before retrying.")
                emit(batch, batch.active_stage, "operation.interrupted", "The previous operation did not finish; a new attempt is starting.", level="warning")
            token = uuid.uuid4()
            batch.active_operation_id, batch.active_stage, batch.operation_started_at = token, stage, now
            batch.save(update_fields=["active_operation_id", "active_stage", "operation_started_at"])
            emit(batch, stage, f"{stage}.started", {"mapping": "Mapping uploaded files.",
                "preprocessing": "Preprocessing and validating approved data.", "saving": "Validating and saving approved data."}[stage])
    try:
        yield token
    except Exception:
        if token:
            finish(batch_id, token, stage, failed=True)
        raise
    else:
        if token:
            finish(batch_id, token, stage)


def finish(batch_id, token, stage, failed=False):
    with transaction.atomic():
        batch = Batch.objects.select_for_update().get(id=batch_id)
        if batch.active_operation_id != token:
            return
        failed = failed or (stage == "preprocessing" and batch.status == "validation_failed")
        message = ("This operation failed; inspect the validation report or request error." if failed else
                   {"mapping": "Mapping completed. Review the detected fields.",
                    "preprocessing": "Preprocessing and validation completed. Data is ready to save.",
                    "saving": "Data saved to the database."}[stage])
        emit(batch, stage, f"{stage}.{'failed' if failed else 'completed'}", message,
             level="error" if failed else "success", metadata={"revision": batch.revision, "status": batch.status})
        batch.active_operation_id, batch.active_stage, batch.operation_started_at = None, "", None
        batch.save(update_fields=["active_operation_id", "active_stage", "operation_started_at"])


def file_progress(batch, file, report, stage="preprocessing"):
    metadata = {"file_id": str(file.id), "rows_before": report["rows_before"], "rows_after": report["rows_after"],
                "removed_duplicate_rows": report["removed_duplicate_rows"], "policy": report["policy"]}
    emit(batch, stage, "cleaning.file_completed", "Approved file data processed with identifiers and amounts protected.", metadata=metadata)
    if report.get("cleaning"):
        for step, details in report["cleaning"]["per_step"].items():
            emit(batch, stage, "cleaning.step_completed", f"Completed {step.replace('_', ' ')}.",
                 metadata={"file_id": str(file.id), "step": step, "status": details["status"]})
    if report["removed_duplicate_rows"]:
        emit(batch, stage, "cleaning.duplicates_removed", "Removed explicitly approved duplicate rows.", metadata=metadata)
