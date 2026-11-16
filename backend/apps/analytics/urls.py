from django.urls import include, path

from .views import KpiCardView, KpiListView

app_name = "analytics"
urlpatterns = [
    path("", include("apps.analytics.customer360.urls")),
    path("kpis/", KpiListView.as_view(), name="kpi-list"),
    path("kpis/<str:card>/", KpiCardView.as_view(), name="kpi-card"),
]
