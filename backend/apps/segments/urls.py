from django.urls import path

from .views import CustomerSegmentsView, SegmentListView, SegmentResultsView, SegmentRunView
from .audience_views import AudienceListView, AudienceDetailView, AudienceCustomersView

app_name = "segments"
urlpatterns = [
    path("audiences/", AudienceListView.as_view(), name="audiences"),
    path("audiences/<str:key>/", AudienceDetailView.as_view(), name="audience"),
    path("audiences/<str:key>/customers/", AudienceCustomersView.as_view(), name="audience-customers"),
    path("segments/", SegmentListView.as_view(), name="list"),
    path("segments/<str:key>/run/", SegmentRunView.as_view(), name="run"),
    path("segments/<str:key>/results/", SegmentResultsView.as_view(), name="results"),
    path("customers/<uuid:customer_id>/segments/", CustomerSegmentsView.as_view(), name="customer"),
]
