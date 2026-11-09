"""``python manage.py map_columns <verify|map|review|apply> ...``

Thin Django wrapper around the mapping CLI, so the same commands work inside
the Django project once settings are configured. Arguments are passed through
unchanged; ``python -m apps.mapping`` (run from backend/) behaves identically.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.mapping.cli import main


class Command(BaseCommand):
    help = "Map store-upload CSV columns onto the canonical schema (verify/map/review/apply)."

    def add_arguments(self, parser):
        parser.add_argument("args", nargs="*", help="arguments for the mapping CLI, e.g. map upload.csv -o out.json")

    def run_from_argv(self, argv):
        # Hand everything after the command name straight to the mapping CLI so
        # its own flags (--no-llm, --kind, ...) are not parsed by Django.
        self.execute(*argv[2:])

    def handle(self, *args, **options):
        code = main(list(args))
        if code:
            raise CommandError(f"map_columns exited with status {code}")
