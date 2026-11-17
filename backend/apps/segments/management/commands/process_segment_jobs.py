from concurrent.futures import ThreadPoolExecutor
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.segments.services.jobs import work_once


class Command(BaseCommand):
    help = 'Process durable audience preparation jobs, with up to two independent workers.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--workers', type=int, default=2)
        parser.add_argument('--batch-size', type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options['workers'] <= 2 or not 1 <= options['batch_size'] <= 200:
            raise CommandError('Use 1–2 workers and a batch size of 1–200.')
        stopped = threading.Event()

        def worker():
            try:
                while not stopped.is_set():
                    try:
                        found = work_once(options['batch_size'])
                    except Exception as exc:
                        # Do not expose connection strings or raw driver messages.
                        self.stderr.write(f'Worker database operation failed ({type(exc).__name__}); retrying after 5 seconds.')
                        connections.close_all()
                        if not options['watch']:
                            raise
                        stopped.wait(5)
                        continue
                    if not found:
                        if not options['watch']:
                            return
                        stopped.wait(1)
            finally:
                connections.close_all()

        self.stdout.write(f"Audience workers started: {options['workers']}; batch size: {options['batch_size']}.")
        with ThreadPoolExecutor(max_workers=options['workers']) as pool:
            tasks = [pool.submit(worker) for _ in range(options['workers'])]
            try:
                for task in tasks:
                    task.result()
            except KeyboardInterrupt:
                stopped.set()
                self.stdout.write('Stopping workers after their current batch.')
