from django.urls import path

from .views import CustomerSegmentsView, SegmentListView, SegmentResultsView, SegmentRunView

app_name = "segments"
urlpatterns = [
    path("segments/", SegmentListView.as_view(), name="list"),
    path("segments/<str:key>/run/", SegmentRunView.as_view(), name="run"),
    path("segments/<str:key>/results/", SegmentResultsView.as_view(), name="results"),
    path("customers/<uuid:customer_id>/segments/", CustomerSegmentsView.as_view(), name="customer"),
]
