"""Create display images on upload and compensate storage writes on rollback."""
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
import logging
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps

from .validators import validate_sponsor_logo_dimensions


logger = logging.getLogger(__name__)
_upload_attempts = ContextVar("sponsor_upload_attempts", default=None)
_image_fields = ("logo_image", "logo_preview")


def make_sponsor_preview(upload):
    """Return a small WebP without modifying the source upload or its position."""
    position = upload.tell()
    try:
        upload.seek(0)
        with Image.open(upload) as source:
            validate_sponsor_logo_dimensions(*source.size)
            # JPEG draft decoding and thumbnailing precede the orientation/mode
            # copies, so these copies only contain the small display image.
            source.draft("RGB", (800, 800))
            source.thumbnail((800, 800), Image.Resampling.LANCZOS)
            oriented = ImageOps.exif_transpose(source)
            mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            preview = oriented.convert(mode)
            output = BytesIO()
            preview.save(output, format="WEBP", quality=85, method=6)
            return ContentFile(output.getvalue(), name=f"{uuid4().hex}.webp"), preview.width, preview.height
    finally:
        upload.seek(position)


def image_references(sponsor):
    return {name: (getattr(sponsor, name).storage, getattr(sponsor, name).name) for name in _image_fields}


def track_sponsor_upload(sponsor, previous):
    attempts = _upload_attempts.get()
    if attempts is not None:
        attempts.append((sponsor, previous))


def _remove_new_files(attempts):
    removed = set()
    for sponsor, previous in reversed(attempts):
        for name in _image_fields:
            uploaded = getattr(sponsor, name)
            old_storage, old_name = previous[name]
            reference = (id(uploaded.storage), uploaded.name)
            if (uploaded and uploaded._committed and reference not in removed
                    and (uploaded.storage is not old_storage or uploaded.name != old_name)):
                try:
                    uploaded.storage.delete(uploaded.name)
                    removed.add(reference)
                except Exception:
                    logger.exception("Could not remove a failed sponsor upload from storage.")


@contextmanager
def sponsor_upload_transaction():
    """Include audit/admin logging in the rollback boundary for new files only."""
    if _upload_attempts.get() is not None:
        yield
        return
    attempts = []
    token = _upload_attempts.set(attempts)
    try:
        with transaction.atomic():
            yield
    except Exception:
        _remove_new_files(attempts)
        raise
    finally:
        _upload_attempts.reset(token)
