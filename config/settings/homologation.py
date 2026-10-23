"""Isolated payment QA installation; never load this profile in the live checkout."""
from django.core.exceptions import ImproperlyConfigured
from .production import *

if BASE_DIR.name != "equipemorcego-homologacao" or os.getenv("MP_ENVIRONMENT") != "sandbox":
    raise ImproperlyConfigured("Homologation requires its own installation and sandbox credentials.")

DEBUG = False
ALLOWED_HOSTS = ["homologacao.equipemorcego.nabio.pro", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["https://homologacao.equipemorcego.nabio.pro"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "homologation.sqlite3", "OPTIONS": {"timeout": 30}}}
MEDIA_ROOT = BASE_DIR / "homologation-media"
SESSION_COOKIE_NAME = "morcego_homologation_session"
CSRF_COOKIE_NAME = "morcego_homologation_csrf"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PUBLIC_SITE_URL = "https://homologacao.equipemorcego.nabio.pro"
MP_WEBHOOK_URL = PUBLIC_SITE_URL + "/integracoes/mercadopago/webhook/"
MIDDLEWARE = [item for item in MIDDLEWARE if item != "apps.core.middleware.EventSubdomainMiddleware"]
