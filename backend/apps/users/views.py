"""Account API: Google sign-in, email + password, forgot password, store onboarding.

"Stay signed in" uses an HttpOnly cookie that JavaScript cannot read; the
pages receive only the short-lived workspace token that the ingestion, KPI,
Customer 360 and segment APIs already accept.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .services import GoogleUnavailable, SignInError

COOKIE = "customerly_session"
COOKIE_PATH = "/api/auth/"


def _text(request, name: str, limit: int = 255) -> str:
    return str(request.data.get(name) or "")[:limit]


class AccountView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def handle_exception(self, exc):
        if isinstance(exc, GoogleUnavailable):
            return Response({"detail": str(exc)}, status=503)
        if isinstance(exc, DatabaseError):
            return Response({"detail": "Account storage is unavailable; check the database configuration."}, status=503)
        return super().handle_exception(exc)

    @staticmethod
    def signed_in_response(response, account):
        response.set_cookie(COOKIE, services.session_token(account), max_age=services.SESSION_MAX_AGE, path=COOKIE_PATH,
                            httponly=True, samesite="Strict", secure=getattr(settings, "AUTH_COOKIE_SECURE", True))
        return response

    @staticmethod
    def account(request):
        return services.account_from_session(request.COOKIES.get(COOKIE, ""))

    @staticmethod
    def workspace(account, store_id: str = "", status: int = 200):
        stores = services.memberships(account)
        if not stores:
            return Response({"status": "needs_store", "email": account.email}, status=status)
        if store_id:
            stores = [m for m in stores if m["store_id"] == store_id]
            if not stores:
                return Response({"detail": "You do not belong to that store."}, status=403)
        if len(stores) > 1:
            return Response({"status": "choose_store", "email": account.email,
                             "stores": [{"id": m["store_id"], "name": m["store_name"], "role": m["role"]} for m in stores]},
                            status=status)
        return Response({**services.session_payload(stores[0]), "email": account.email}, status=status)


class ConfigView(AccountView):
    """GET -> which sign-in methods are available (the Google client id is public)."""
    throttle_classes = []

    def get(self, request):
        return Response({"google_client_id": getattr(settings, "GOOGLE_CLIENT_ID", "") or None, "password": True})


class SignUpView(AccountView):
    """POST {email, password} -> account created and signed in (no email is sent)."""

    def post(self, request):
        email, password = services.normalize_email(_text(request, "email")), _text(request, "password", 128)
        try:
            validate_email(email)
        except ValidationError:
            return Response({"email": "Enter a valid email address."}, status=400)
        try:
            services.check_new_password(password, email)
        except ValidationError as error:
            return Response({"password": list(error.messages)}, status=400)
        account = services.create_account(email, password)
        if account is None:
            return Response({"email": "An account with this email already exists. Sign in instead, or continue with Google."}, status=409)
        return self.signed_in_response(self.workspace(account, status=201), account)


class SignInView(AccountView):
    """POST {email, password}."""

    def post(self, request):
        try:
            account = services.sign_in(_text(request, "email"), _text(request, "password", 128))
        except SignInError as error:
            return Response({"detail": str(error), "code": error.code}, status=429 if error.code == "locked" else 401)
        return self.signed_in_response(self.workspace(account), account)


class GoogleSignInView(AccountView):
    """POST {credential} -> the Google ID token from the 'Continue with Google' button."""

    def post(self, request):
        try:
            account = services.google_account(services.verify_google_credential(_text(request, "credential", 8192)))
        except SignInError as error:
            return Response({"detail": str(error), "code": error.code}, status=401)
        return self.signed_in_response(self.workspace(account), account)


class SessionView(AccountView):
    """POST {store_id?} -> fresh workspace token while the stay-signed-in cookie is valid."""

    def post(self, request):
        account = self.account(request)
        if account is None:
            return Response({"detail": "Please sign in."}, status=401)
        return self.workspace(account, _text(request, "store_id", 64))


class StoreView(AccountView):
    """POST {name} -> a new store with the signed-in account as owner."""

    def post(self, request):
        account = self.account(request)
        if account is None:
            return Response({"detail": "Please sign in."}, status=401)
        name = _text(request, "name", 200).strip()
        if not 2 <= len(name) <= 120:
            return Response({"name": "Enter a store name between 2 and 120 characters."}, status=400)
        if services.memberships(account):
            return Response({"detail": "This account already has a store."}, status=409)
        try:
            membership = services.create_store(account, name)
        except IntegrityError:
            return Response({"detail": "This email already belongs to that store."}, status=409)
        return Response({**services.session_payload(membership), "email": account.email}, status=201)


class ForgotPasswordView(AccountView):
    """POST {email} -> always the same answer, so it never reveals which emails have accounts."""

    def post(self, request):
        try:
            services.send_password_reset(_text(request, "email"))
        except OSError:  # SMTP unreachable or rejected the login
            return Response({"detail": "We could not send the email right now. Please try again later."}, status=503)
        return Response({"status": "sent_if_exists"})


class ResetPasswordView(AccountView):
    """POST {token, password} from the emailed link. Signs the account out everywhere."""

    def post(self, request):
        try:
            account = services.reset_password(_text(request, "token", 1024), _text(request, "password", 128))
        except ValidationError as error:
            return Response({"password": list(error.messages)}, status=400)
        if account is None:
            return Response({"detail": "This reset link is invalid, already used or expired. Request a new one."}, status=400)
        response = Response({"status": "password_reset"})
        response.delete_cookie(COOKIE, path=COOKIE_PATH, samesite="Strict")
        return response


class SignOutView(AccountView):
    def post(self, request):
        response = Response({"status": "signed_out"})
        response.delete_cookie(COOKIE, path=COOKIE_PATH, samesite="Strict")
        return response
