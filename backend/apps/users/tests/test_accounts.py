"""Accounts: Google sign-in, email + password, lockout, sessions and store access."""
from unittest.mock import patch
import uuid

from django.core import signing
from django.db import connection
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import TOKEN_SALT
from apps.segments.models import Customer, CustomerMembership, SegmentDefinition, Store
from apps.users.models import Account

PASSWORD = "Blue-Kettle-42"
EMAIL = "zoha@example.com"


@pytest.fixture
def tables(transactional_db):
    """Canonical tables are unmanaged; create them as the segments tests do, plus store_users."""
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:
        for model in (Store, Customer, SegmentDefinition, CustomerMembership):
            if model._meta.db_table not in existing:
                editor.create_model(model)
    with connection.cursor() as cursor:
        have = {c.name for c in connection.introspection.get_table_description(cursor, "stores")}
        if "plan" not in have:
            cursor.execute("ALTER TABLE stores ADD COLUMN plan varchar(50)")
        cursor.execute("CREATE TABLE IF NOT EXISTS store_users (id varchar(36) PRIMARY KEY, store_id varchar(36), "
                       "email varchar(255), role varchar(50))")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_store_email ON store_users (store_id, email)")
    yield
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM store_users")
        cursor.execute("DELETE FROM stores")


def google(client, email=EMAIL, sub="google-123", verified=True):
    """Sign in with a (simulated) verified Google ID token."""
    claims = {"sub": sub, "email": email, "email_verified": verified, "iss": "https://accounts.google.com"}
    with patch("apps.users.services.verify_google_credential", return_value=claims):
        return client.post("/api/auth/google/", {"credential": "google-id-token"}, format="json")


def sign_up(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/signup/", {"email": email, "password": password}, format="json")


def sign_in(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/signin/", {"email": email, "password": password}, format="json")


def add_store(name, email, role="owner"):
    store, member = uuid.uuid4(), uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO stores (id, name, plan) VALUES (%s, %s, 'free')", [str(store), name])
        cursor.execute("INSERT INTO store_users (id, store_id, email, role) VALUES (%s, %s, %s, %s)",
                       [str(member), str(store), email, role])
    return str(store), str(member)


def test_config_reports_google_client_id(settings):
    settings.GOOGLE_CLIENT_ID = "123.apps.googleusercontent.com"
    assert APIClient().get("/api/auth/config/").json() == {"google_client_id": "123.apps.googleusercontent.com", "password": True}
    settings.GOOGLE_CLIENT_ID = ""
    assert APIClient().get("/api/auth/config/").json()["google_client_id"] is None


# ---------------------------------------------------------- email + password
def test_sign_up_signs_in_immediately_and_stores_only_a_hash(tables):
    client = APIClient()
    response = sign_up(client, " Zoha@Example.com ")
    assert response.status_code == 201 and response.json() == {"status": "needs_store", "email": EMAIL}
    account = Account.objects.get()
    assert account.password != PASSWORD and not account.email_verified and account.google_sub is None
    cookie = response.cookies["customerly_session"]
    assert cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/api/auth/"


@pytest.mark.parametrize("password", ["short1", "12345678901", "password123", "zoha@example.com"])
def test_weak_passwords_are_rejected(tables, password):
    response = sign_up(APIClient(), password=password)
    assert response.status_code == 400 and response.json()["password"]


def test_invalid_and_duplicate_emails_are_rejected(tables):
    assert sign_up(APIClient(), email="not-an-email").status_code == 400
    sign_up(APIClient())
    duplicate = sign_up(APIClient(), password="Other-Pass-77")
    assert duplicate.status_code == 409 and Account.objects.count() == 1


def test_wrong_password_and_unknown_email_get_the_same_answer(tables):
    sign_up(APIClient())
    wrong, unknown = sign_in(APIClient(), password="Nope-Nope-1"), sign_in(APIClient(), "ghost@example.com", "Nope-Nope-1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Incorrect email or password."


def test_account_locks_after_five_failed_attempts(tables):
    sign_up(APIClient())
    for _ in range(5):
        sign_in(APIClient(), password="Wrong-Pass-9")
    locked = sign_in(APIClient())
    assert locked.status_code == 429 and locked.json()["code"] == "locked"


def test_full_journey_create_store_and_use_the_api(tables):
    client = APIClient()
    sign_up(client)
    assert client.post("/api/auth/store/", {"name": " "}, format="json").status_code == 400
    created = client.post("/api/auth/store/", {"name": "Zoha Store"}, format="json")
    assert created.status_code == 201
    body = created.json()
    assert body["store"]["name"] == "Zoha Store" and body["role"] == "owner"
    assert client.post("/api/auth/store/", {"name": "Second"}, format="json").status_code == 409
    api = APIClient()  # the workspace token works on the existing APIs
    api.credentials(HTTP_AUTHORIZATION="Bearer " + body["token"])
    assert api.get("/api/kpis/").status_code == 200
    again = APIClient()  # signing in later goes straight to the store
    assert sign_in(again).json()["store"]["id"] == body["store"]["id"]
    assert again.post("/api/auth/session/", {}, format="json").json()["store"]["id"] == body["store"]["id"]


def test_session_requires_the_signed_in_cookie_and_sign_out_clears_it(tables):
    assert APIClient().post("/api/auth/session/", {}, format="json").status_code == 401
    forged = APIClient()
    forged.cookies["customerly_session"] = "forged"
    assert forged.post("/api/auth/session/", {}, format="json").status_code == 401
    client = APIClient()
    sign_up(client)
    assert client.post("/api/auth/signout/").status_code == 200
    assert client.post("/api/auth/session/", {}, format="json").status_code == 401


# ------------------------------------------------- unproven emails are limited
def test_password_account_cannot_claim_someone_elses_store(tables):
    """Anyone can type any email; without proof it must not open that person's store."""
    add_store("Sohaib Store", "sohaib@example.com", role="owner")
    client = APIClient()
    response = sign_up(client, "sohaib@example.com")
    assert response.json() == {"status": "needs_store", "email": "sohaib@example.com"}


# ----------------------------------------------------------------- Google
def test_google_creates_a_verified_account(tables):
    client = APIClient()
    response = google(client)
    assert response.status_code == 200 and response.json()["status"] == "needs_store"
    account = Account.objects.get()
    assert account.email_verified and account.google_sub == "google-123" and account.password.startswith("!")
    assert sign_in(APIClient(), password="anything-Else-1").json()["detail"] == "This account signs in with Google. Use Continue with Google."


def test_google_opens_stores_a_teammate_was_added_to(tables):
    store, member = add_store("Team Store", EMAIL, role="member")
    body = google(APIClient()).json()
    assert body["status"] == "ok" and body["store"]["id"] == store and body["role"] == "member"
    assert signing.loads(body["token"], salt=TOKEN_SALT)["store_user_id"] == member


def test_google_links_a_password_account_and_is_the_forgot_password_path(tables):
    team_store, _ = add_store("Team Store", EMAIL, role="admin")
    client = APIClient()
    sign_up(client)
    own = client.post("/api/auth/store/", {"name": "Own Store"}, format="json").json()["store"]["id"]
    for _ in range(5):  # forgot the password and got locked out
        sign_in(APIClient(), password="Wrong-Pass-9")
    body = google(APIClient()).json()
    account = Account.objects.get()
    assert account.google_sub == "google-123" and account.email_verified and account.locked_until is None
    assert body["status"] == "choose_store" and {s["id"] for s in body["stores"]} == {own, team_store}
    assert sign_in(APIClient()).status_code == 200  # the lock is cleared and the password still works


def test_google_requires_a_verified_email(tables):
    response = google(APIClient(), verified=False)
    assert response.status_code == 401 and Account.objects.count() == 0


def test_returning_google_user_is_found_by_google_id(tables):
    google(APIClient(), email="old@example.com", sub="same-person")
    google(APIClient(), email="old@example.com", sub="same-person")
    assert Account.objects.count() == 1


def test_several_stores_must_be_chosen(tables):
    first, _ = add_store("A Store", EMAIL)
    second, _ = add_store("B Store", EMAIL, role="admin")
    other, _ = add_store("Not mine", "bob@example.com")
    client = APIClient()
    choose = google(client).json()
    assert choose["status"] == "choose_store" and {s["id"] for s in choose["stores"]} == {first, second}
    picked = client.post("/api/auth/session/", {"store_id": second}, format="json").json()
    assert picked["store"]["id"] == second and picked["role"] == "admin"
    assert client.post("/api/auth/session/", {"store_id": other}, format="json").status_code == 403


def test_google_not_configured_returns_503(tables, settings):
    settings.GOOGLE_CLIENT_ID = ""
    assert APIClient().post("/api/auth/google/", {"credential": "x"}, format="json").status_code == 503


def test_forged_google_token_is_rejected(tables, settings):
    settings.GOOGLE_CLIENT_ID = "123.apps.googleusercontent.com"
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("bad signature")):
        response = APIClient().post("/api/auth/google/", {"credential": "forged"}, format="json")
    assert response.status_code == 401 and Account.objects.count() == 0


# ---------------------------------------------------------- forgot password
def reset_link_token():
    import re
    from django.core import mail
    found = re.search(r"/reset-password\?token=(\S+)", mail.outbox[-1].body)
    assert found, "no reset email"
    return found.group(1)


def test_forgot_password_never_reveals_whether_an_email_exists(tables):
    from django.core import mail
    response = APIClient().post("/api/auth/forgot-password/", {"email": "ghost@example.com"}, format="json")
    assert response.status_code == 200 and response.json() == {"status": "sent_if_exists"}
    assert len(mail.outbox) == 0
    sign_up(APIClient())
    known = APIClient().post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
    assert known.json() == response.json() and mail.outbox[-1].to == [EMAIL]
    assert "Customerly" in mail.outbox[-1].subject


def test_reset_works_once_ends_old_sessions_unlocks_and_proves_the_email(tables):
    team_store, _ = add_store("Team Store", EMAIL, role="member")
    client = APIClient()
    sign_up(client)
    old_session = client.cookies["customerly_session"].value
    for _ in range(5):
        sign_in(APIClient(), password="Wrong-Pass-9")
    APIClient().post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
    token = reset_link_token()
    assert APIClient().post("/api/auth/reset-password/", {"token": token, "password": "123"}, format="json").status_code == 400
    assert APIClient().post("/api/auth/reset-password/", {"token": token, "password": "New-Kettle-77"}, format="json").status_code == 200
    # the link works once
    assert APIClient().post("/api/auth/reset-password/", {"token": token, "password": "Other-Kettle-88"}, format="json").status_code == 400
    # old sessions end, the old password stops working, the lock is gone
    stale = APIClient()
    stale.cookies["customerly_session"] = old_session
    assert stale.post("/api/auth/session/", {}, format="json").status_code == 401
    assert sign_in(APIClient()).status_code == 401
    body = sign_in(APIClient(), password="New-Kettle-77").json()
    # the email link proved the email, so the teammate's store now opens
    assert Account.objects.get().email_verified
    assert body["status"] == "ok" and body["store"]["id"] == team_store


def test_reset_rejects_forged_links(tables):
    response = APIClient().post("/api/auth/reset-password/", {"token": "forged", "password": "New-Kettle-77"}, format="json")
    assert response.status_code == 400


def test_google_only_account_can_add_a_password_by_reset(tables):
    google(APIClient())
    APIClient().post("/api/auth/forgot-password/", {"email": EMAIL}, format="json")
    APIClient().post("/api/auth/reset-password/", {"token": reset_link_token(), "password": "New-Kettle-77"}, format="json")
    assert sign_in(APIClient(), password="New-Kettle-77").status_code == 200
