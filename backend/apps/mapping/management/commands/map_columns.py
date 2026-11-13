"""``python manage.py map_columns <verify|map|review|apply> ...``

Thin Django wrapper around the mapping CLI, so the same commands work inside
the Django project once settings are configured. Arguments are passed through
unchanged; ``python -m apps.mapping`` (run from backend/) behaves identically.
"""
from argparse import REMAINDER

from django.core.management.base import BaseCommand, CommandError

from apps.mapping.cli import main


class Command(BaseCommand):
    help = "Map store-upload CSV columns onto the canonical schema (verify/map/review/apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "mapping_args", nargs=REMAINDER,
            help="arguments for the mapping CLI, e.g. map upload.csv -o out.json",
        )

    def handle(self, *args, **options):
        code = main(options["mapping_args"])
        if code:
            raise CommandError(f"map_columns exited with status {code}")
