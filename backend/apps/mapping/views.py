from dataclasses import asdict
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from core.api import BoundedJSONParser, ProcessingAPIView
from .schema_catalog import get_catalog
from . import services
from .serializers import ApplySerializer, ProposeSerializer, ReviewSerializer


class MappingProposeView(ProcessingAPIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        serializer = ProposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        upload = data.pop("file")
        return Response(services.propose(request.user.store_id, upload, **data))


class MappingReviewView(ProcessingAPIView):
    parser_classes = [BoundedJSONParser]

    def post(self, request):
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.review(request.user.store_id, **serializer.validated_data))


class MappingTargetsView(ProcessingAPIView):
    def get(self, request):
        catalog = get_catalog()
        return Response({"targets": [asdict(field) for table, columns in sorted(catalog.mappable.items())
                                     for name, field in sorted(columns.items())]})


class MappingApplyView(ProcessingAPIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        serializer = ApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.apply(request.user.store_id, serializer.validated_data["state"], serializer.validated_data["file"]))
