"""Durable upload, review, validation and transaction orchestration."""
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.http import Http404
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.exceptions import APIException, ValidationError

from core.api import read_csv_upload, InputTooLarge
from apps.mapping.mapper import map_csv
from apps.mapping.models import MappingResult
from apps.mapping.review import apply_decisions, ReviewError
from .compiler import compile_batch, save_records, selection, NATURAL, fetch
from .models import Batch, UploadFile, Source, ReviewVersion, SegmentJob
from .storage import storage
from .activity import emit, file_progress


class Conflict(APIException):
    status_code = 409
    default_detail = "This batch has changed. Reload its current revision."


def serializable(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def batch_for(principal, batch_id, lock=False):
    query = Batch.objects.select_related("source")
    if lock:
        query = query.select_for_update(of=("self",))
    try:
        return query.get(id=batch_id, store_id=principal.store_id)
    except Batch.DoesNotExist:
        raise Http404 from None


def check_revision(batch, revision):
    if batch.revision != revision:
        raise Conflict()


def writable(batch):
    if batch.status in {"saved", "completed", "segments_pending", "segments_failed"}:
        raise Conflict("This batch is already saved. Submit corrections in a new batch.")


def canonical_upload(file, batch, status):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE ingestion_uploads SET status = %s WHERE id = %s AND store_id = %s",
                       [status, str(file.upload_id), str(batch.store_id)])


def create_batch(principal, source_id, request_key, files):
    try:
        source = Source.objects.get(id=source_id, store_id=principal.store_id)
    except Source.DoesNotExist:
        raise Http404 from None
    contents = []
    total = 0
    for upload in files:
        raw, columns, rows = read_csv_upload(upload)
        total += len(raw)
        if total > 20 * 1024 * 1024:
            raise InputTooLarge("A batch may contain at most 20 MiB of CSV content.")
        contents.append((upload.name, raw, hashlib.sha256(raw).hexdigest(), len(rows)))
    hashes = [item[2] for item in contents]
    if len(set(hashes)) != len(hashes):
        raise ValidationError("The same CSV content appears more than once in this batch.")
    fingerprint = hashlib.sha256(json.dumps([str(source_id), sorted(hashes)]).encode()).hexdigest()
    saved_names = []
    try:
        with transaction.atomic():
            # Serialize creation for this store, including idempotency-key races.
            if connection.vendor == "postgresql":
                lock_id = int.from_bytes(hashlib.sha256(f"ingestion:create:{principal.store_id}".encode()).digest()[:8], "big", signed=True)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
            existing = Batch.objects.filter(store_id=principal.store_id, request_key=request_key).first()
            if existing:
                if existing.fingerprint != fingerprint:
                    raise Conflict("This request_key already belongs to a different source or file set.")
                return existing, False
            batch = Batch.objects.create(store_id=principal.store_id, uploaded_by=principal.store_user_id,
                                         source=source, request_key=request_key, fingerprint=fingerprint)
            from django.utils import timezone
            for name, raw, digest, count in contents:
                file_id, upload_id = uuid.uuid4(), uuid.uuid4()
                storage_name = storage().save(f"{file_id.hex}.csv", ContentFile(raw))
                saved_names.append(storage_name)
                UploadFile.objects.create(id=file_id, batch=batch, upload_id=upload_id, original_name=Path(name).name,
                                          storage_name=storage_name, sha256=digest, size=len(raw), row_count=count)
                with connection.cursor() as cursor:
                    cursor.execute("""INSERT INTO ingestion_uploads
                        (id, store_id, uploaded_by, file_name, file_type, row_count, status, uploaded_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        [str(upload_id), str(principal.store_id), str(principal.store_user_id), Path(name).name,
                         "csv", count, "uploaded", timezone.now()])
            emit(batch, "upload", "upload.completed", "CSV files uploaded and stored privately.", level="success",
                 metadata={"files": len(contents), "rows": sum(item[3] for item in contents)})
            return batch, True
    except Exception:
        for name in saved_names:
            storage().delete(name)
        raise


def map_batch(principal, batch_id, revision, use_llm=True):
    with transaction.atomic():
        batch = batch_for(principal, batch_id, lock=True)
        check_revision(batch, revision)
        writable(batch)
        for file in batch.files.order_by("id"):
            from .compiler import read_file
            read_file(file)  # checksum validation before mapper touches content
            with tempfile.TemporaryDirectory(prefix="customerly-ingestion-") as directory:
                path = Path(directory) / "upload.csv"
                with storage().open(file.storage_name, "rb") as handle:
                    path.write_bytes(handle.read())
                result = map_csv(path, use_llm=use_llm)
            result.source_file = file.original_name
            file.document, file.approved_document, file.status = result.to_dict(), {}, "mapped"
            file.save(update_fields=["document", "approved_document", "status"])
            canonical_upload(file, batch, "mapped")
            emit(batch, "mapping", "mapping.file_completed", "File fields mapped; approval is required.",
                 metadata={"file_id": str(file.id), "summary": result.summary(), "llm_used": bool(result.llm_info.get("used"))})
        batch.review, batch.report = {}, {}
        batch.status, batch.revision = "mapped", batch.revision + 1
        batch.save()
        return batch


def review_batch(principal, batch_id, revision, files, segment_options):
    with transaction.atomic():
        batch = batch_for(principal, batch_id, lock=True)
        check_revision(batch, revision)
        writable(batch)
        if batch.status not in {"mapped", "reviewed", "validated", "validation_failed"}:
            raise Conflict("Map all files before reviewing this batch.")
        if batch.active_operation_id:
            raise Conflict("Wait for the current batch operation to finish before changing its review.")
        uploaded = {str(file.id): file for file in batch.files.all()}
        reviewed = [str(item["file_id"]) for item in files]
        if len(set(reviewed)) != len(reviewed) or set(reviewed) != set(uploaded):
            raise ValidationError("Review must include each file exactly once.")
        if segment_options.get("clv", {}).get("enabled") and batch.source.currency != "GBP":
            raise ValidationError("The current CLV model requires the source's verified currency to be GBP.")
        for decisions in files:
            file = uploaded[str(decisions["file_id"])]
            result = MappingResult.from_dict(file.document)
            try:
                corrected = [item["source_column"] for item in decisions["set_targets"]]
                if len(corrected) != len(set(corrected)):
                    raise ReviewError("Correct each source column at most once per review.")
                for group in (decisions["bindings"], decisions["calculations"]):
                    keys = [(item["target_table"], item["target_column"]) for item in group]
                    if len(keys) != len(set(keys)):
                        raise ReviewError("Each relationship/calculation target must appear at most once.")
                if set(decisions["reject"]) & (set(decisions["approve"]) | set(corrected)):
                    raise ReviewError("A column cannot be approved and rejected together.")
                apply_decisions(result, approve=decisions["approve"], reject=decisions["reject"],
                    set_targets=[(item["source_column"], item["target_table"], item["target_column"]) for item in decisions["set_targets"]],
                    approve_exact=decisions["approve_exact"], approve_suggested=decisions["approve_suggested"])
            except ReviewError as exc:
                raise ValidationError({"review": str(exc)}) from None
            targets = [(item.target_table, item.target_column) for item in result.mappings if item.status == "approved"]
            if len(targets) != len(set(targets)):
                raise ValidationError("Approved mappings contain duplicate canonical targets.")
            file.approved_document = result.to_dict()
            selection(file, decisions)  # validate relationship/calculation requests immediately
            file.status = "reviewed"
            file.save(update_fields=["approved_document", "status"])
            canonical_upload(file, batch, "reviewed")
        options = {"churn": {"enabled": True, "update_memberships": False}, **segment_options}
        batch.review = serializable({"files": files, "segment_options": options})
        batch.revision += 1
        batch.status, batch.report = "reviewed", {}
        batch.save()
        ReviewVersion.objects.create(batch=batch, revision=batch.revision,
                                     reviewed_by=principal.store_user_id, decisions=batch.review)
        emit(batch, "review", "review.completed", "Mapping decisions and file relationships approved.", level="success",
             metadata={"revision": batch.revision, "files": len(files)})
        return batch


def validate_batch(principal, batch_id, revision):
    with transaction.atomic():
        batch = batch_for(principal, batch_id, lock=True)
        check_revision(batch, revision)
        writable(batch)
        if not batch.review:
            raise Conflict("Review the mappings and relationships first.")
        try:
            records, report = compile_batch(batch, progress=lambda file, report: file_progress(batch, file, report))
        except ValidationError as exc:
            batch.status = "validation_failed"
            batch.report = {"errors": serializable(exc.detail)}
            batch.save()
            return batch, False
        batch.status, batch.report = "validated", report
        batch.save()
        return batch, True


def process_batch(principal, batch_id, revision):
    with transaction.atomic():
        batch = batch_for(principal, batch_id, lock=True)
        check_revision(batch, revision)
        if batch.status in {"saved", "completed", "segments_pending", "segments_failed"}:
            return batch  # Same revision: processing is idempotent.
        if batch.status != "validated":
            raise Conflict("Validate the current review successfully before processing.")
        if connection.vendor == "postgresql":
            lock_id = int.from_bytes(hashlib.sha256(f"ingestion:source:{batch.store_id}:{batch.source_id}".encode()).digest()[:8], "big", signed=True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
        records, report = compile_batch(batch, progress=lambda file, report: file_progress(batch, file, report, stage="saving"))
        save_records(records)
        customers = {item["id"] for item in records["customers"].values()}
        for table in ("orders", "events", "message_sends", "coupon_redemptions"):
            customers.update(item["customer_id"] for item in records[table].values() if item.get("customer_id"))
            customers.update(item["_previous_customer_id"] for item in records[table].values() if item.get("_previous_customer_id"))
        for item in records["order_items"].values():
            for order_id in {item["order_id"], item.get("_previous_order_id", item["order_id"])}:
                order = fetch("orders", '"id" = %s AND "store_id" = %s', [order_id, str(batch.store_id)])
                if order:
                    customers.add(str(order[0]["customer_id"]))
        jobs = 0
        for customer_id in sorted(customers):
            for key, options in batch.review["segment_options"].items():
                if not options.get("enabled", True):
                    continue
                job_options = {name: value for name, value in options.items() if name != "enabled"}
                if key == "clv":
                    job_options["currency"] = batch.source.currency
                SegmentJob.objects.get_or_create(batch=batch, customer_id=customer_id, segment_key=key,
                                                 defaults={"options": job_options})
                jobs += 1
        with connection.cursor() as cursor:
            for file in batch.files.all():
                cursor.execute("DELETE FROM ingestion_column_mappings WHERE upload_id = %s", [str(file.upload_id)])
                document = MappingResult.from_dict(file.approved_document)
                for item in document.mappings:
                    if item.status == "approved" and item.has_target:
                        cursor.execute("""INSERT INTO ingestion_column_mappings
                            (id, upload_id, source_column, target_table, target_column, confidence, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            [str(uuid.uuid4()), str(file.upload_id), item.source_column, item.target_table,
                             item.target_column, item.confidence, batch.updated_at])
                file.status = "saved"
                file.save(update_fields=["status"])
                canonical_upload(file, batch, "saved")
        batch.report = {**report, "affected_customers": len(customers), "segment_jobs": jobs}
        batch.status = "segments_pending" if jobs else "completed"
        batch.save()
        emit(batch, "segmentation", "segmentation.queued" if jobs else "segmentation.skipped",
             "Prediction work queued after saving." if jobs else "No prediction work was requested or needed.",
             metadata={"jobs": jobs, "affected_customers": len(customers)})
        # Durable jobs are committed first. The HTTP layer runs them afterwards;
        # a worker can also resume pending work if the request/server disappears.
        return batch


def batch_detail(batch):
    return {"id": str(batch.id), "source_id": str(batch.source_id), "status": batch.status,
            "revision": batch.revision, "uploaded_by": str(batch.uploaded_by),
            "created_at": batch.created_at, "updated_at": batch.updated_at,
            "activity": {"operation_id": str(batch.active_operation_id) if batch.active_operation_id else None,
                         "stage": batch.active_stage or None, "started_at": batch.operation_started_at,
                         "latest_event_id": batch.events.order_by("-id").values_list("id", flat=True).first()},
            "review": batch.review, "report": batch.report,
            "files": [{"id": str(file.id), "name": file.original_name, "sha256": file.sha256,
                       "rows": file.row_count, "status": file.status, "document": file.document,
                       "approved_document": file.approved_document} for file in batch.files.order_by("id")],
            "segmentation": {status: batch.jobs.filter(status=status).count() for status in ["pending", "completed", "failed", "superseded"]}}
