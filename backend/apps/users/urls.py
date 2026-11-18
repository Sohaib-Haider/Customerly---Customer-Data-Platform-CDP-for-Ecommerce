from django.urls import path

from . import views

app_name = "users"
urlpatterns = [
    path("config/", views.ConfigView.as_view(), name="config"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("signin/", views.SignInView.as_view(), name="signin"),
    path("google/", views.GoogleSignInView.as_view(), name="google"),
    path("session/", views.SessionView.as_view(), name="session"),
    path("store/", views.StoreView.as_view(), name="store"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="forgot-password"),
    path("reset-password/", views.ResetPasswordView.as_view(), name="reset-password"),
    path("signout/", views.SignOutView.as_view(), name="signout"),
]
