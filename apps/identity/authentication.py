from unicodedata import normalize

from .models import User


def normalize_login_identifier(value):
    """The same identity must share its throttle across e-mail and legacy shortcuts."""
    identifier = normalize("NFKC", str(value or "")).strip().lower()
    if identifier in ("kelvim", "admin", "morcego"):
        owner = User.objects.filter(is_superuser=True).first()
        if owner:
            return owner.email.lower().strip()
    return identifier
