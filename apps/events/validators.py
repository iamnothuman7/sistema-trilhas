"""Validation shared by sponsor forms and model fields."""
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError


SPONSOR_MAX_IMAGE_PIXELS = 24_000_000


def validate_sponsor_logo_dimensions(width, height):
    if width * height > SPONSOR_MAX_IMAGE_PIXELS:
        raise ValidationError("A imagem deve ter no máximo 24 megapixels. Reduza a resolução e tente novamente.")


def validate_instagram_profile(value):
    if not value:
        return
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.netloc in {"instagram.com", "www.instagram.com"}
            and re.fullmatch(r"/[A-Za-z0-9_.]{1,30}/?", parsed.path)
            and not parsed.query
            and not parsed.fragment
            and parsed.path.strip("/").lower() not in {"p", "reel", "reels", "stories", "explore", "accounts", "direct"}
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValidationError("Informe o link do perfil, como https://www.instagram.com/suamarca/.")


def validate_sponsor_logo_size(value):
    # Existing files are not uploads; preserve legacy records without opening storage.
    if getattr(value, "_committed", False):
        return
    if value and value.size > 15 * 1024 * 1024:
        raise ValidationError("A imagem deve ter até 15 MB.")
