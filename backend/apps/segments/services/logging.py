"""Value-free progress logs shared by API, CLI, ingestion and predictors."""
from contextvars import ContextVar, copy_context
from functools import wraps
import inspect
import logging
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger('apps.segments')
_run = ContextVar('segment_log_run', default='standalone')


def progress(step, message, *, level=logging.INFO, **fields):
    details = ' '.join(f'{name}={value}' for name, value in fields.items())
    logger.log(level, '[run=%s] %s | %s%s', _run.get(), step, message,
               f' | {details}' if details else '')


def after_commit(step, message, **fields):
    from django.db import transaction
    context = copy_context()
    transaction.on_commit(lambda: context.run(progress, step, message, **fields))


def logged(operation):
    """Log duration/failure without dumping arguments, values or exception text."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            token = _run.set(uuid4().hex[:12]) if _run.get() == 'standalone' else None
            bound = signature.bind_partial(*args, **kwargs).arguments
            # These identifiers aid tracing; source data and predictions are excluded.
            context = {key: bound[key] for key in ('key', 'store_id', 'customer_id') if key in bound}
            started = perf_counter()
            progress(operation + '.started', 'Started.', **context)
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                progress(operation + '.failed', 'Stopped because of an unexpected error; the operation did not complete.',
                         level=logging.ERROR, error_type=type(exc).__name__, **context)
                raise
            else:
                progress(operation + '.finished', 'Finished processing; enclosing transactions may still be pending.',
                         elapsed_ms=round((perf_counter() - started) * 1000), **context)
                return result
            finally:
                if token is not None:
                    _run.reset(token)
        return wrapped
    return decorate
