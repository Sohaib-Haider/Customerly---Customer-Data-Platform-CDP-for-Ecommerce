"""Sign-in accounts. Store membership and roles stay in the canonical store_users table."""

import uuid

from django.db import models


class Account(models.Model):
    """One person who can sign in, with Google and/or an email + password."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=255, unique=True)  # stored lower-case
    full_name = models.CharField(max_length=150, blank=True, default="")
    password = models.CharField(max_length=256)  # Django hash; unusable for Google-only accounts
    google_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)  # Google account id
    email_verified_at = models.DateTimeField(null=True, blank=True)  # set when Google proves the email
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_accounts"

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def __str__(self) -> str:
        return self.email


class AccountStore(models.Model):
    """A store membership this account created or was linked to.

    Accounts whose email is not verified only reach these. Verified (Google)
    accounts also reach every store_users row with their email.
    """

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="stores")
    store_user_id = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_account_stores"
