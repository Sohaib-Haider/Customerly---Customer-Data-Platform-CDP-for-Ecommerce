from django.urls import path
from .views import PreprocessingConfigView, PreprocessingRunView

app_name = "preprocessing"
urlpatterns = [
    path("config/", PreprocessingConfigView.as_view(), name="config"),
    path("run/", PreprocessingRunView.as_view(), name="run"),
]
