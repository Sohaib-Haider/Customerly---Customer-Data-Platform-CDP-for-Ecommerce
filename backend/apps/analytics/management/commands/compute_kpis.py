"""``python manage.py compute_kpis [--card 3 9] [--category Revenue] [--by month] [--json]``

Django wrapper around the KPI command line, so the same options work once
config/settings lists ``apps.analytics`` in INSTALLED_APPS.
``python -m apps.analytics.kpis`` (run from backend/) behaves identically.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.analytics.kpis.__main__ import main


class Command(BaseCommand):
    help = "Compute the FYP1 KPI cards (all, or selected with --card / --category)."

    def add_arguments(self, parser):
        parser.add_argument("args", nargs="*", help="arguments for the KPI CLI, e.g. --card 1 --by month")

    def run_from_argv(self, argv):
        self.execute(*argv[2:])

    def handle(self, *args, **options):
        code = main(list(args))
        if code:
            raise CommandError(f"compute_kpis exited with status {code}")
