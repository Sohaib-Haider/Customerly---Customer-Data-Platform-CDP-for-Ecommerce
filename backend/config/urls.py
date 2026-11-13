"""Project routing. Domain APIs will be added by their respective apps."""

from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    """Process liveness only; deliberately does not query the database."""
    return JsonResponse({"status": "ok", "service": "customerly-backend"})


urlpatterns = [path("health/", health, name="health"), path("api/", include("apps.segments.urls"))]
