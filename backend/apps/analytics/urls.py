from django.urls import path

from .views import KpiCardView, KpiListView

app_name = "analytics"
urlpatterns = [
    path("kpis/", KpiListView.as_view(), name="kpi-list"),
    path("kpis/<str:card>/", KpiCardView.as_view(), name="kpi-card"),
]
