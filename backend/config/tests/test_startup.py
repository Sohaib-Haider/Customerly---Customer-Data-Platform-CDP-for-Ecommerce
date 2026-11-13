"""Database-free checks for the backend foundation."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from django.apps import apps
from django.test import SimpleTestCase
from django.urls import reverse


class StartupTests(SimpleTestCase):
    def test_health_does_not_access_database(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "customerly-backend"})

    def test_health_rejects_mutations(self):
        self.assertEqual(self.client.post(reverse("health")).status_code, 405)

    def test_existing_apps_are_registered(self):
        self.assertTrue(apps.is_installed("apps.mapping"))
        self.assertTrue(apps.is_installed("apps.preprocessing"))
        self.assertTrue(apps.is_installed("apps.segments"))

    def test_application_entry_points_import(self):
        from config.asgi import application as asgi_application
        from config.wsgi import application as wsgi_application

        self.assertTrue(callable(asgi_application))
        self.assertTrue(callable(wsgi_application))

    def test_mapping_command_forwards_cli_flags(self):
        from apps.mapping.management.commands.map_columns import Command

        with patch("apps.mapping.management.commands.map_columns.main", return_value=0) as main:
            Command().run_from_argv([
                "manage.py", "map_columns", "map", "example.csv", "--no-llm", "--kind", "transactions",
            ])
        main.assert_called_once_with(["map", "example.csv", "--no-llm", "--kind", "transactions"])


class EnvironmentSettingsTests(SimpleTestCase):
    def settings_process(self, module, **overrides):
        # Fresh imports exercise real settings selection, not pytest's configured
        # singleton. All values here are synthetic and no connection is opened.
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": module,
            "DJANGO_SECRET_KEY": "",
            "DJANGO_ALLOWED_HOSTS": "",
            "DATABASE_URL": "",
            **overrides,
        }
        return subprocess.run(
            [sys.executable, "-c", (
                "import json; from django.conf import settings; "
                "d=settings.DATABASES['default']; "
                "print(json.dumps({'engine': d['ENGINE'], 'debug': settings.DEBUG, "
                "'hosts': settings.ALLOWED_HOSTS, 'sslmode': d.get('OPTIONS', {}).get('sslmode')}))"
            )],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_development_starts_without_credentials(self):
        result = self.settings_process("config.settings.dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertEqual(settings["engine"], "django.db.backends.dummy")
        self.assertTrue(settings["debug"])

    def test_postgresql_configuration_defaults_to_ssl(self):
        result = self.settings_process(
            "config.settings.dev",
            DATABASE_URL="postgresql://example:fake@localhost:5432/postgres",
            DJANGO_ALLOWED_HOSTS="example.test, localhost",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertEqual(settings["engine"], "django.db.backends.postgresql")
        self.assertEqual(settings["sslmode"], "require")
        self.assertEqual(settings["hosts"], ["example.test", "localhost"])

    def test_test_settings_override_shared_database(self):
        result = self.settings_process(
            "config.settings.test",
            DATABASE_URL="postgresql://example:fake@localhost:5432/postgres",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["engine"], "django.db.backends.sqlite3")

    def test_production_requires_configuration(self):
        valid = {
            "DJANGO_SECRET_KEY": "synthetic-test-secret-" * 4,
            "DJANGO_ALLOWED_HOSTS": "example.test",
            "DATABASE_URL": "postgresql://example:fake@localhost:5432/postgres",
        }
        for missing in valid:
            with self.subTest(missing=missing):
                result = self.settings_process("config.settings.prod", **{**valid, missing: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
        result = self.settings_process("config.settings.prod", **valid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["debug"])

    def test_non_postgresql_urls_are_rejected(self):
        result = self.settings_process("config.settings.dev", DATABASE_URL="sqlite:///:memory:")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use PostgreSQL", result.stderr)
