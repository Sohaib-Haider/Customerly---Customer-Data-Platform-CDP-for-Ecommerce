"""Private local storage. No public URL and no client-controlled paths."""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


def storage():
    return FileSystemStorage(location=settings.INGESTION_PRIVATE_ROOT, base_url=None,
                             file_permissions_mode=0o600, directory_permissions_mode=0o700)
