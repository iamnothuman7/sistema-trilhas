"""Sensitive financial documents are never part of the public media tree."""
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateDocumentStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def base_location(self):
        root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
        for setting in ("MEDIA_ROOT", "STATIC_ROOT"):
            public_root = getattr(settings, setting, None)
            if public_root and root.is_relative_to(Path(public_root).resolve()):
                raise ImproperlyConfigured("PRIVATE_MEDIA_ROOT must be outside public media and static roots.")
        return str(root)

    @property
    def location(self):
        return self.base_location

    def path(self, name):
        path = Path(super().path(name))
        if not path.resolve().is_relative_to(Path(self.location)):
            raise SuspiciousFileOperation("Private document path is outside its storage root.")
        return str(path)

    def url(self, name):
        raise ValueError("Private documents require an authorized download view.")


private_document_storage = PrivateDocumentStorage()
