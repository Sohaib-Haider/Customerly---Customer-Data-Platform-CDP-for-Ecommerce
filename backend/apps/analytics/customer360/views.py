"""Authenticated GET endpoints for the frontend Customer 360 view."""
from django.db import DatabaseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.segments.authentication import SegmentTokenAuthentication
from . import selectors, services
from .serializers import CustomerQuerySerializer, PageQuerySerializer


class CustomerAPIView(APIView):
    authentication_classes = [SegmentTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            return Response({"detail": "Customer data is unavailable; check database configuration and migrations."}, status=503)
        return super().handle_exception(exc)

    def respond(self, data):
        response = Response(services.json_values(data))
        # Profiles contain contact details; shared browser/proxy caches must not retain them.
        response["Cache-Control"] = "private, no-store"
        return response

    def query(self, request, serializer_class=PageQuerySerializer):
        query = serializer_class(data=request.query_params)
        query.is_valid(raise_exception=True)
        return query.validated_data


class CustomerListView(CustomerAPIView):
    def get(self, request):
        return self.respond(services.customer_list(request.user.store_id, **self.query(request, CustomerQuerySerializer)))


class Customer360View(CustomerAPIView):
    def get(self, request, customer_id):
        return self.respond(services.customer360(request.user.store_id, customer_id))


class CustomerHistoryView(CustomerAPIView):
    def get(self, request, customer_id, kind):
        query = self.query(request)
        store_id = request.user.store_id
        selectors.profile(store_id, customer_id)  # Same 404 for absent and foreign customers.
        if kind == "orders":
            data = selectors.orders(store_id, customer_id, **query)
            for order in data["results"]:
                order["source"] = services.source_info(order["source"])
        else:
            data = selectors.history(kind, store_id, customer_id, **query)
        return self.respond({"customer_id": customer_id, **data})


class CustomerOrderView(CustomerAPIView):
    def get(self, request, customer_id, order_id):
        query = self.query(request)
        store_id = request.user.store_id
        selectors.profile(store_id, customer_id)
        order = selectors.order(store_id, customer_id, order_id)
        order["source"] = services.source_info(order["source"])
        return self.respond({"customer_id": customer_id, "order": order,
                             "items": selectors.order_items(store_id, customer_id, order_id, **query)})
