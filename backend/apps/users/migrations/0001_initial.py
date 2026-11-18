import uuid

import django.db.models.deletion
from django.db import migrations, models

TABLES = ("user_accounts", "user_account_stores")


def enable_rls(apps, schema_editor):
    # Password hashes must never be reachable through Supabase's Data API roles.
    if schema_editor.connection.vendor == "postgresql":
        for table in TABLES:
            schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table in TABLES:
            schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(max_length=255, unique=True)),
                ("password", models.CharField(max_length=256)),
                ("google_sub", models.CharField(blank=True, max_length=255, null=True, unique=True)),
                ("email_verified_at", models.DateTimeField(blank=True, null=True)),
                ("failed_logins", models.PositiveSmallIntegerField(default=0)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "user_accounts"},
        ),
        migrations.CreateModel(
            name="AccountStore",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("store_user_id", models.UUIDField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stores", to="users.account")),
            ],
            options={"db_table": "user_account_stores"},
        ),
        migrations.RunPython(enable_rls, disable_rls),
    ]
