from rest_framework.response import Response
from .views import SegmentAPIView
from .serializers import AudienceQuerySerializer
from .services import audiences
from .services.catalog import DEFINITIONS


class AudienceAPIView(SegmentAPIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class AudienceListView(AudienceAPIView):
    def get(self, request):
        store_id = self.store_id(request)
        return Response({"audiences": [audiences.describe(store_id, key) for key in DEFINITIONS]})


class AudienceDetailView(AudienceAPIView):
    def get(self, request, key):
        return Response(audiences.describe(self.store_id(request), key))


class AudienceCustomersView(AudienceAPIView):
    def get(self, request, key):
        store_id = self.store_id(request)
        query = AudienceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        result = audiences.members(store_id, key, **query.validated_data)
        if result is None:
            return Response({"detail": "Initialize the segment registry before reading this audience."}, status=503)
        return Response(result)
