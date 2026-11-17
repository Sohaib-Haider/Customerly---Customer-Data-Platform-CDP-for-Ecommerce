"""Tenant-scoped API; caller-supplied store IDs never authorize access."""

from django.db import DatabaseError, transaction
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import SegmentTokenAuthentication
from .models import Customer, SegmentDefinition, SegmentPrediction, Store
from .serializers import ResultsQuerySerializer, RunSerializer
from .services.catalog import DEFINITIONS, get_definition
from .services.execution import execute_customer
from .services.logging import logged, progress


class SegmentAPIView(APIView):
    authentication_classes = [SegmentTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def store_id(self, request):
        store_id = request.user.store_id
        if not Store.objects.filter(id=store_id).exists():
            raise Http404
        return store_id

    def definition(self, key):
        if key not in DEFINITIONS:
            raise Http404
        try:
            return get_definition(key)
        except SegmentDefinition.DoesNotExist:
            return None

    def handle_exception(self, exc):
        progress('api.request.failed', 'Segmentation API request failed.', error_type=type(exc).__name__)
        if isinstance(exc, DatabaseError):
            return Response({"detail": "Segmentation storage is unavailable; check database configuration and initialization."}, status=503)
        return super().handle_exception(exc)


class SegmentListView(SegmentAPIView):
    def get(self, request):
        self.store_id(request)
        definitions = {definition.name: definition for definition in SegmentDefinition.objects.filter(name__in=[v["name"] for v in DEFINITIONS.values()])}
        return Response({"segments": [{"key": key, "name": spec["name"],
            "id": str(definitions[spec["name"]].id) if spec["name"] in definitions else None,
            "registered": spec["name"] in definitions,
            "required_data": definitions[spec["name"]].required_fields if spec["name"] in definitions else None,
        } for key, spec in DEFINITIONS.items()]})


class SegmentRefreshView(SegmentAPIView):
    @logged('api.refresh')
    def post(self, request):
        store_id = self.store_id(request)
        if any(self.definition(key) is None for key in DEFINITIONS):
            return Response({"detail": "Run initialize_segments before generating predictions."}, status=503)
        from .services.backfill import fill_missing
        return Response(fill_missing(store_id))


class PrepareAudienceView(SegmentAPIView):
    @logged('api.prepare_audiences')
    def post(self, request):
        from .serializers import PrepareAudienceSerializer
        from .services.jobs import enqueue
        store_id = self.store_id(request)
        if any(self.definition(key) is None for key in DEFINITIONS):
            return Response({'detail': 'Run initialize_segments before preparing audiences.'}, status=503)
        serializer = PrepareAudienceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        options = serializer.validated_data
        progress('api.prepare.queued', 'Validated request; enqueueing background jobs without running models in HTTP.', store_id=store_id)
        return Response({'jobs': enqueue(store_id, options['clv_threshold'])}, status=202)


class AudiencePreparationJobsView(SegmentAPIView):
    def get(self, request):
        from .services.jobs import latest_jobs
        return Response({'jobs': latest_jobs(self.store_id(request))}, headers={'Cache-Control': 'private, no-store'})


class SegmentRunView(SegmentAPIView):
    @logged('api.run_segment')
    def post(self, request, key):
        store_id = self.store_id(request)
        if self.definition(key) is None:
            return Response({"detail": "Run initialize_segments before using this predictor."}, status=503)
        serializer = RunSerializer(data=request.data, context={"segment": key})
        serializer.is_valid(raise_exception=True)
        options = dict(serializer.validated_data)
        customer_ids = options.pop("customer_ids")
        # Reject the entire request before scoring if any identifier is outside
        # the authenticated store. Do not leak which other store owns it.
        if Customer.objects.filter(store_id=store_id, id__in=customer_ids).count() != len(customer_ids):
            raise Http404
        with transaction.atomic():
            try:
                results = [execute_customer(key, store_id, customer_id, **options) for customer_id in sorted(customer_ids, key=str)]
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from None
        return Response({"segment": key, "count": len(results), "results": results})


class SegmentResultsView(SegmentAPIView):
    def get(self, request, key):
        store_id = self.store_id(request)
        self.definition(key)
        query = ResultsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        rows = SegmentPrediction.objects.filter(store_id=store_id, segment_key=key).order_by("customer_id")
        if "status" in params:
            rows = rows.filter(status=params["status"])
        count = rows.count()
        page = rows[params["offset"]:params["offset"] + params["limit"]]
        return Response({"count": count, "limit": params["limit"], "offset": params["offset"],
            "results": [{"customer_id": str(row.customer_id), "computed_at": row.computed_at,
                         "observation_date": row.observation_date, **row.result} for row in page]})


class CustomerSegmentsView(SegmentAPIView):
    def get(self, request, customer_id):
        store_id = self.store_id(request)
        if not Customer.objects.filter(store_id=store_id, id=customer_id).exists():
            raise Http404
        rows = SegmentPrediction.objects.filter(store_id=store_id, customer_id=customer_id).order_by("segment_key")
        return Response({"customer_id": str(customer_id), "results": [
            {"computed_at": row.computed_at, "observation_date": row.observation_date, **row.result} for row in rows]})
