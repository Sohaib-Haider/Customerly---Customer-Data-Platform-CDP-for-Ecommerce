import json

from django.core.management.base import BaseCommand

from ...services.catalog import initialize_catalog


class Command(BaseCommand):
    help = "Register the two current segmentation models without modifying business inputs."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(initialize_catalog()))
