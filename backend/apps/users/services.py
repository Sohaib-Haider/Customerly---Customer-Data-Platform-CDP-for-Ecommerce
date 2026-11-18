"""Accounts, Google sign-in, password sign-in security and store membership.

Two ways to sign in:
* **Google**: Google proves who the person is and that they own the email.
* **Email + password**: no email is sent, so the email is *not* proven.

Because of that difference, a password-only account reaches only the stores it
created itself; an account whose email Google has verified also reaches every
``store_users`` row with that email (for example a teammate added to a store).
Signing in with Google using the same email links it to the existing account.

Forgot password: a one-time reset link (valid 1 hour) is emailed. Opening it
proves the email, so after a reset the account counts as verified too.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import get_password_validators, validate_password
from django.core import signing
from django.core.mail import send_mail
from django.db import connection, transaction
from django.utils import timezone

from apps.segments.authentication import TOKEN_SALT

from .models import Account, AccountStore

ROLES = ("owner", "admin", "member")
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
SESSION_MAX_AGE = 30 * 24 * 3600  # stay signed in: 30 days
RESET_MAX_AGE = 3600  # password reset link: 1 hour
PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator", "OPTIONS": {"user_attributes": ("email",)}},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Same cost as a real check, so unknown emails take as long as wrong passwords.
_DUMMY_HASH = make_password("customerly-timing-equaliser")


class SignInError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GoogleUnavailable(Exception):
    """Google sign-in is not configured, or Google could not be reached."""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def check_new_password(password: str, email: str = "") -> None:
    """Raise django ValidationError with readable messages if the password is weak."""
    validate_password(password, user=Account(email=email), password_validators=get_password_validators(PASSWORD_VALIDATORS))


# ----------------------------------------------------------- email + password
def create_account(email: str, password: str) -> Account | None:
    """New password account, or None if the email is already registered."""
    email = normalize_email(email)
    if Account.objects.filter(email=email).exists():
        return None
    return Account.objects.create(email=email, password=make_password(password))


def sign_in(email: str, password: str) -> Account:
    """Check the password, locking the account after repeated failures."""
    account = Account.objects.filter(email=normalize_email(email)).first()
    if account is None:
        check_password(password, _DUMMY_HASH)
        raise SignInError("invalid", "Incorrect email or password.")
    now = timezone.now()
    if account.locked_until and account.locked_until > now:
        raise SignInError("locked", f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes, or continue with Google.")
    if not check_password(password, account.password):
        account.failed_logins += 1
        if account.failed_logins >= MAX_FAILED_LOGINS:
            account.failed_logins, account.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
        account.save(update_fields=["failed_logins", "locked_until", "updated_at"])
        google_only = account.google_sub and account.password.startswith("!")  # "!" = Django's unusable password
        raise SignInError("invalid", "This account signs in with Google. Use Continue with Google."
                          if google_only else "Incorrect email or password.")
    if account.failed_logins or account.locked_until:
        account.failed_logins, account.locked_until = 0, None
        account.save(update_fields=["failed_logins", "locked_until", "updated_at"])
    return account


# --------------------------------------------------------------------- Google
def verify_google_credential(credential: str) -> dict:
    """Check a Google ID token (signature, audience, expiry, issuer); return its claims."""
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise GoogleUnavailable("Google sign-in is not configured on the backend.")
    from google.auth import exceptions as google_errors
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except google_errors.TransportError:  # could not download Google's public keys
        raise GoogleUnavailable("Google sign-in is temporarily unavailable.") from None
    except (ValueError, google_errors.GoogleAuthError):  # bad signature, audience, issuer, expiry or format
        raise SignInError("invalid", "Google sign-in could not be verified. Please try again.") from None


def google_account(claims: dict) -> Account:
    """Find, link or create the account for a verified Google identity."""
    sub, email = str(claims.get("sub") or ""), normalize_email(claims.get("email", ""))
    if not sub or not email or claims.get("email_verified") is not True:
        raise SignInError("invalid", "Your Google account has no verified email address.")
    now = timezone.now()
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(google_sub=sub).first()
        if account is None:
            account = Account.objects.select_for_update().filter(email=email).first()
        if account is None:
            return Account.objects.create(email=email, password=make_password(None), google_sub=sub, email_verified_at=now)
        # Google proved this email: link it, mark it verified and clear any lock.
        account.google_sub = sub
        account.email_verified_at = account.email_verified_at or now
        account.failed_logins, account.locked_until = 0, None
        account.save()
        return account


# ---------------------------------------------- signed tokens (session, reset)
def _fingerprint(account: Account) -> str:
    """Changes when the password changes, ending older sessions and reset links."""
    return hashlib.sha256(account.password.encode()).hexdigest()[:16]


def _sign(purpose: str, account: Account) -> str:
    return signing.dumps({"a": str(account.id), "p": _fingerprint(account)}, salt=f"customerly.users.{purpose}")


def _unsign(purpose: str, token: str, max_age: int) -> Account | None:
    if not token:
        return None
    try:
        data = signing.loads(token, salt=f"customerly.users.{purpose}", max_age=max_age)
        account = Account.objects.get(id=uuid.UUID(data["a"]))
    except (signing.BadSignature, Account.DoesNotExist, KeyError, TypeError, ValueError):
        return None
    return account if data.get("p") == _fingerprint(account) else None


def session_token(account: Account) -> str:
    return _sign("session", account)


def account_from_session(token: str) -> Account | None:
    return _unsign("session", token, SESSION_MAX_AGE)


# ----------------------------------------------------------- forgot password
def send_password_reset(email: str) -> None:
    """Email a reset link if the account exists. Callers answer the same either way."""
    account = Account.objects.filter(email=normalize_email(email)).first()
    if account is None:
        return
    link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={_sign('reset', account)}"
    send_mail(
        "Reset your Customerly password",
        "We received a request to reset your Customerly password.\n\n"
        f"Choose a new password here:\n{link}\n\n"
        "This link expires in 1 hour and works once. If you did not ask for this, "
        "ignore this email; your password stays the same.\n\n— Customerly",
        settings.DEFAULT_FROM_EMAIL, [account.email], fail_silently=False,
    )


def reset_password(token: str, password: str) -> Account | None:
    """Set a new password from a reset link. None if the link is invalid, used or expired."""
    account = _unsign("reset", token, RESET_MAX_AGE)
    if account is None:
        return None
    check_new_password(password, account.email)
    account.password = make_password(password)  # also invalidates the link and old sessions
    account.email_verified_at = account.email_verified_at or timezone.now()  # the email link proves the email
    account.failed_logins, account.locked_until = 0, None
    account.save()
    return account


# ---------------------------------------------------------- store membership
def memberships(account: Account) -> list[dict]:
    """Stores this account can open (see the module docstring for the rule)."""
    linked = [str(value) for value in account.stores.values_list("store_user_id", flat=True)]
    clauses, params = [], []
    if linked:
        clauses.append(f"su.id IN ({', '.join(['%s'] * len(linked))})")
        params += linked
    if account.email_verified:
        clauses.append("lower(su.email) = %s")
        params.append(account.email)
    if not clauses:
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT su.id, su.store_id, su.role, s.name FROM store_users su JOIN stores s ON s.id = su.store_id "
            f"WHERE {' OR '.join(clauses)} ORDER BY s.name",
            params,
        )
        rows = cursor.fetchall()
    return [{"store_user_id": str(r[0]), "store_id": str(r[1]), "role": r[2], "store_name": r[3]}
            for r in rows if r[2] in ROLES]


def create_store(account: Account, name: str) -> dict:
    """Create a store with this account as its owner."""
    store_id, store_user_id = uuid.uuid4(), uuid.uuid4()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO stores (id, name, plan) VALUES (%s, %s, %s)", [str(store_id), name, "free"])
            cursor.execute("INSERT INTO store_users (id, store_id, email, role) VALUES (%s, %s, %s, %s)",
                           [str(store_user_id), str(store_id), account.email, "owner"])
        AccountStore.objects.create(account=account, store_user_id=store_user_id)
    return {"store_user_id": str(store_user_id), "store_id": str(store_id), "role": "owner", "store_name": name}


def session_payload(membership: dict) -> dict:
    """The signed store-user workspace token that every existing API accepts."""
    token = signing.dumps({"store_id": membership["store_id"], "store_user_id": membership["store_user_id"],
                           "purpose": "segments"}, salt=TOKEN_SALT)
    return {"status": "ok", "token": token, "expires_in": int(getattr(settings, "SEGMENTS_TOKEN_MAX_AGE", 3600)),
            "store": {"id": membership["store_id"], "name": membership["store_name"]}, "role": membership["role"]}
