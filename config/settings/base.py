import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-morcego-trilhas-2026-super-secret-key-prod-dev")
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "*,equipemorcego.nabio.pro,.equipemorcego.nabio.pro,3trilhaotiangua.equipemorcego.nabio.pro,.nabio.pro,localhost,127.0.0.1",
    ).split(",")
    if h.strip()
]
if ".equipemorcego.nabio.pro" not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.extend([".equipemorcego.nabio.pro", "3trilhaotiangua.equipemorcego.nabio.pro"])

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "https://equipemorcego.nabio.pro,https://*.equipemorcego.nabio.pro,https://3trilhaotiangua.equipemorcego.nabio.pro,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]
for origin in ("https://equipemorcego.nabio.pro", "https://*.equipemorcego.nabio.pro", "https://3trilhaotiangua.equipemorcego.nabio.pro"):
    if origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    # Local Apps
    "apps.core.apps.CoreConfig",
    "apps.identity.apps.IdentityConfig",
    "apps.events.apps.EventsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.checkout.apps.CheckoutConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.registrations.apps.RegistrationsConfig",
    "apps.operations.apps.OperationsConfig",
    "apps.expeditions.apps.ExpeditionsConfig",
    "apps.mediaframes.apps.MediaframesConfig",
    "apps.finance.apps.FinanceConfig",
    "apps.reporting.apps.ReportingConfig",
    "apps.communications.apps.CommunicationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.EventSubdomainMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.global_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
# Default: SQLite for immediate development; PostgreSQL in staging/production when DATABASE_URL is set
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    # If psycopg or dj-database-url is configured
    try:
        import dj_database_url
        DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
    except ImportError:
        # standard postgres fallback
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Custom User Model
AUTH_USER_MODEL = "identity.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("TIME_ZONE", "America/Fortaleza")
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# Mercado Pago Configuration
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_PUBLIC_KEY = os.getenv("MP_PUBLIC_KEY", "")
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")
MP_ENVIRONMENT = os.getenv("MP_ENVIRONMENT", "sandbox")
MP_API_BASE = os.getenv("MP_API_BASE", "https://api.mercadopago.com")
MP_COLLECTOR_ID = os.getenv("MP_COLLECTOR_ID", "")
MP_WEBHOOK_URL = os.getenv("MP_WEBHOOK_URL", "")
MP_WEBHOOK_MAX_AGE_SECONDS = int(os.getenv("MP_WEBHOOK_MAX_AGE_SECONDS", "900"))

# Business Rule Timing Defaults (in minutes/hours)
QUOTE_TTL_MINUTES = int(os.getenv("QUOTE_TTL_MINUTES", "5"))
HOLD_TTL_MINUTES = int(os.getenv("HOLD_TTL_MINUTES", "35"))
PIX_EXPIRES_MINUTES = int(os.getenv("PIX_EXPIRES_MINUTES", "30"))

# Login URLs
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/minha-conta/"
LOGOUT_REDIRECT_URL = "/"
