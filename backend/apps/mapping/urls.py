from django.urls import path
from .views import MappingApplyView, MappingProposeView, MappingReviewView, MappingTargetsView

app_name = "mapping"
urlpatterns = [
    path("targets/", MappingTargetsView.as_view(), name="targets"),
    path("propose/", MappingProposeView.as_view(), name="propose"),
    path("review/", MappingReviewView.as_view(), name="review"),
    path("apply/", MappingApplyView.as_view(), name="apply"),
]
