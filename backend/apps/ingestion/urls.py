from django.urls import path
from .views import (SourcesView, BatchesView, BatchView, MapView, ReviewView,
                    ValidateView, ProcessView, RetrySegmentsView, DownloadView, JobsView)

app_name = "ingestion"
urlpatterns = [
    path("sources/", SourcesView.as_view(), name="sources"),
    path("batches/", BatchesView.as_view(), name="batches"),
    path("batches/<uuid:batch_id>/", BatchView.as_view(), name="batch"),
    path("batches/<uuid:batch_id>/jobs/", JobsView.as_view(), name="jobs"),
    path("batches/<uuid:batch_id>/map/", MapView.as_view(), name="map"),
    path("batches/<uuid:batch_id>/review/", ReviewView.as_view(), name="review"),
    path("batches/<uuid:batch_id>/validate/", ValidateView.as_view(), name="validate"),
    path("batches/<uuid:batch_id>/process/", ProcessView.as_view(), name="process"),
    path("batches/<uuid:batch_id>/retry-segments/", RetrySegmentsView.as_view(), name="retry-segments"),
    path("batches/<uuid:batch_id>/files/<uuid:file_id>/", DownloadView.as_view(), name="download"),
]
