import uuid
from django.db import models


class Source(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_id = models.UUIDField(db_index=True)
    namespace = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    currency = models.CharField(max_length=3, null=True)
    analytics_profile = models.CharField(max_length=20, default="generic")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ingestion_sources"
        constraints = [models.UniqueConstraint(fields=["store_id", "namespace"], name="uq_ingestion_source_namespace")]


class Batch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_id = models.UUIDField(db_index=True)
    uploaded_by = models.UUIDField()
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    request_key = models.CharField(max_length=100)
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=30, default="uploaded")
    revision = models.PositiveIntegerField(default=0)
    review = models.JSONField(default=dict)
    report = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ingestion_batches"
        constraints = [models.UniqueConstraint(fields=["store_id", "request_key"], name="uq_ingestion_batch_request")]


class UploadFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, related_name="files", on_delete=models.CASCADE)
    upload_id = models.UUIDField(unique=True)
    original_name = models.CharField(max_length=255)
    storage_name = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    row_count = models.PositiveIntegerField()
    document = models.JSONField(default=dict)
    approved_document = models.JSONField(default=dict)
    status = models.CharField(max_length=30, default="uploaded")

    class Meta:
        db_table = "ingestion_files"


class ReviewVersion(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField()
    reviewed_by = models.UUIDField()
    decisions = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ingestion_reviews"
        constraints = [models.UniqueConstraint(fields=["batch", "revision"], name="uq_ingestion_review_revision")]


class SegmentJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, related_name="jobs", on_delete=models.CASCADE)
    customer_id = models.UUIDField()
    segment_key = models.CharField(max_length=20)
    options = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ingestion_segment_jobs"
        constraints = [models.UniqueConstraint(fields=["batch", "customer_id", "segment_key"], name="uq_ingestion_segment_job")]
