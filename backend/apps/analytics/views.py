"""KPI card API, tenant-scoped like the segments API.

The store comes from the signed access token, never from the request, so a
caller can only read its own store's KPIs.
"""
from django.db import DatabaseError, connection
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.segments.authentication import SegmentTokenAuthentication

from .kpis.cards import CARDS
from .kpis.engine import resolve, run_card


def describe(card):
    return {
        "id": card.id, "key": card.key, "name": card.name, "category": card.category,
        "kind": card.kind, "unit": card.unit, "sources": list(card.sources),
        "dimensions": list(card.dimensions),
    }


class KpiAPIView(APIView):
    # Same store-scoped service token as the segments API until platform login exists.
    authentication_classes = [SegmentTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            return Response({"detail": "KPI data is unavailable; check the database configuration."}, status=503)
        return super().handle_exception(exc)


class KpiListView(KpiAPIView):
    """GET /api/kpis/ - the 19 FYP1 cards and their breakdown options."""

    def get(self, request):
        return Response({"cards": [describe(card) for card in CARDS]})


class KpiCardView(KpiAPIView):
    """GET /api/kpis/<id or key>/?by=<dimension> - one computed card."""

    def get(self, request, card):
        try:
            definition = resolve(card)
        except (KeyError, ValueError):
            raise Http404 from None
        dimension = request.query_params.get("by") or None
        if dimension and dimension not in definition.dimensions:
            options = ", ".join(definition.dimensions) or "none"
            return Response({"detail": f"'{dimension}' is not a breakdown of this card. Options: {options}."}, status=400)
        result = run_card(connection, definition, store_id=str(request.user.store_id), dimension=dimension)
        return Response(result)
