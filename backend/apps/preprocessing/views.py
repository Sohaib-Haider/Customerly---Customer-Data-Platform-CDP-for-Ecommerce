from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from core.api import BoundedJSONParser, ProcessingAPIView
from .pipeline import DEFAULT_STEPS, default_config
from .serializers import RunSerializer
from .services import run


class PreprocessingConfigView(ProcessingAPIView):
    def get(self, request):
        return Response({"steps": DEFAULT_STEPS, "default_config": default_config(),
                         "limits": {"csv_bytes": 5 * 1024 * 1024, "rows": 10_000, "columns": 200},
                         "model_scaling": False, "category_encoding": False})


class PreprocessingRunView(ProcessingAPIView):
    parser_classes = [BoundedJSONParser, MultiPartParser]

    def post(self, request):
        serializer = RunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(run(**serializer.validated_data))
