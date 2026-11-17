from django.urls import path

from .views import CustomerSegmentsView, SegmentListView, SegmentResultsView, SegmentRunView, SegmentRefreshView
from .audience_views import AudienceListView, AudienceDetailView, AudienceCustomersView
from .views import PrepareAudienceView, AudiencePreparationJobsView

app_name = "segments"
urlpatterns = [
    path("audiences/prepare/", PrepareAudienceView.as_view(), name="prepare-audiences"),
    path("audiences/jobs/", AudiencePreparationJobsView.as_view(), name="audience-jobs"),
    path("audiences/", AudienceListView.as_view(), name="audiences"),
    path("audiences/<str:key>/", AudienceDetailView.as_view(), name="audience"),
    path("audiences/<str:key>/customers/", AudienceCustomersView.as_view(), name="audience-customers"),
    path("segments/", SegmentListView.as_view(), name="list"),
    path("segments/refresh/", SegmentRefreshView.as_view(), name="refresh"),
    path("segments/<str:key>/run/", SegmentRunView.as_view(), name="run"),
    path("segments/<str:key>/results/", SegmentResultsView.as_view(), name="results"),
    path("customers/<uuid:customer_id>/segments/", CustomerSegmentsView.as_view(), name="customer"),
]
