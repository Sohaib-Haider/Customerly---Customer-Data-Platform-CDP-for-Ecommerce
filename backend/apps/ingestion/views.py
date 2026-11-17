from django.db import DatabaseError, IntegrityError
from django.http import FileResponse, Http404
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from core.api import BoundedJSONParser, ProcessingAPIView
from .authentication import IngestionAuthentication
from .models import Batch, Source
from .serializers import (SourceSerializer, CreateBatchSerializer, MapSerializer,
                          ReviewSerializer, RevisionSerializer, PageSerializer, EventsQuerySerializer)
from . import services
from .jobs import run_jobs
from .storage import storage
from .activity import operation


class IngestionView(ProcessingAPIView):
    authentication_classes = [IngestionAuthentication]
    parser_classes = [BoundedJSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, OSError):
            return Response({"detail": "Private upload storage is unavailable; check its configured path and permissions."}, status=503)
        if isinstance(exc, IntegrityError):
            return Response({"detail": "A canonical constraint or concurrent update blocked this operation; no partial business save was accepted."}, status=409)
        if isinstance(exc, DatabaseError):
            return Response({"detail": "Ingestion storage is unavailable; check database configuration and migrations."}, status=503)
        return super().handle_exception(exc)

    def validated(self, serializer, data):
        value = serializer(data=data)
        value.is_valid(raise_exception=True)
        return dict(value.validated_data)


class SourcesView(IngestionView):
    def get(self, request):
        return Response({"sources": list(Source.objects.filter(store_id=request.user.store_id).order_by("namespace").values(
            "id", "namespace", "name", "currency", "analytics_profile"))})

    def post(self, request):
        data = self.validated(SourceSerializer, request.data)
        source, created = Source.objects.get_or_create(store_id=request.user.store_id, namespace=data["namespace"], defaults=data)
        if any(getattr(source, key) != value for key, value in data.items()):
            raise services.Conflict("This source namespace is already registered with different immutable metadata.")
        return Response({"id": str(source.id), **data}, status=201 if created else 200)


class BatchesView(IngestionView):
    parser_classes = [MultiPartParser]

    def get(self, request):
        query = self.validated(PageSerializer, request.query_params)
        rows = Batch.objects.filter(store_id=request.user.store_id).order_by("-created_at", "id")
        page = rows[query["offset"]:query["offset"] + query["limit"]]
        return Response({"count": rows.count(), **query, "results": list(page.values(
            "id", "source_id", "status", "revision", "created_at", "updated_at"))})

    def post(self, request):
        data = self.validated(CreateBatchSerializer, request.data)
        batch, created = services.create_batch(request.user, **data)
        return Response(services.batch_detail(batch), status=201 if created else 200)


class BatchView(IngestionView):
    def get(self, request, batch_id):
        return Response(services.batch_detail(services.batch_for(request.user, batch_id)))


class JobsView(IngestionView):
    def get(self, request, batch_id):
        batch = services.batch_for(request.user, batch_id)
        query = self.validated(PageSerializer, request.query_params)
        jobs = batch.jobs.order_by("customer_id", "segment_key")
        page = jobs[query["offset"]:query["offset"] + query["limit"]]
        return Response({"count": jobs.count(), **query, "results": list(page.values(
            "id", "customer_id", "segment_key", "status", "attempts", "options", "result", "updated_at"))})


class EventsView(IngestionView):
    def get(self, request, batch_id):
        batch = services.batch_for(request.user, batch_id)
        query = self.validated(EventsQuerySerializer, request.query_params)
        page = list(batch.events.filter(id__gt=query["after"]).order_by("id").values(
            "id", "operation_id", "stage", "code", "level", "message", "metadata", "occurred_at")[:query["limit"] + 1])
        results = page[:query["limit"]]
        return Response({"batch_id": str(batch.id), "results": results, "has_more": len(page) > query["limit"],
                         "next_after": results[-1]["id"] if results else query["after"],
                         "activity": {"operation_id": batch.active_operation_id, "stage": batch.active_stage or None,
                                      "started_at": batch.operation_started_at}, "status": batch.status})


class MapView(IngestionView):
    def post(self, request, batch_id):
        data = self.validated(MapSerializer, request.data)
        with operation(request.user, batch_id, data["revision"], "mapping"):
            batch = services.map_batch(request.user, batch_id, **data)
        batch.refresh_from_db()
        return Response(services.batch_detail(batch))


class ReviewView(IngestionView):
    def post(self, request, batch_id):
        batch = services.review_batch(request.user, batch_id, **self.validated(ReviewSerializer, request.data))
        return Response(services.batch_detail(batch))


class ValidateView(IngestionView):
    def post(self, request, batch_id):
        data = self.validated(RevisionSerializer, request.data)
        with operation(request.user, batch_id, data["revision"], "preprocessing"):
            batch, valid = services.validate_batch(request.user, batch_id, **data)
        batch.refresh_from_db()
        return Response(services.batch_detail(batch), status=200 if valid else 422)


class ProcessView(IngestionView):
    def post(self, request, batch_id):
        data = self.validated(RevisionSerializer, request.data)
        with operation(request.user, batch_id, data["revision"], "saving"):
            batch = services.process_batch(request.user, batch_id, **data)
        run_jobs(batch.id, limit=100)
        batch.refresh_from_db()
        return Response(services.batch_detail(batch), status=202 if batch.status == "segments_pending" else 200)


class RetrySegmentsView(IngestionView):
    def post(self, request, batch_id):
        data = self.validated(RevisionSerializer, request.data)
        batch = services.batch_for(request.user, batch_id)
        services.check_revision(batch, data["revision"])
        if batch.status not in {"segments_pending", "segments_failed", "completed"}:
            raise services.Conflict("Save the batch before retrying predictions.")
        run_jobs(batch.id, limit=100, retry_failed=True)
        batch.refresh_from_db()
        return Response(services.batch_detail(batch), status=202 if batch.status == "segments_pending" else 200)


class DownloadView(IngestionView):
    def get(self, request, batch_id, file_id):
        batch = services.batch_for(request.user, batch_id)
        try:
            file = batch.files.get(id=file_id)
            if not file.storage_name:
                raise Http404
            handle = storage().open(file.storage_name, "rb")
        except (batch.files.model.DoesNotExist, FileNotFoundError):
            raise Http404 from None
        return FileResponse(handle, as_attachment=True, filename=file.original_name, content_type="text/csv")
