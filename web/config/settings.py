"""Django settings, shared by both roles. GRADER_ROLE picks which URLs exist."""

from pathlib import Path

from config import env

env.apply_dev_defaults()

BASE_DIR = Path(__file__).resolve().parent.parent

GRADER_ROLE = env.require("GRADER_ROLE")
ROOT_URLCONF = env.urlconf_for_role(GRADER_ROLE)

# Each role is reached at its own base URL; only that host is accepted.
_BASE_URL = env.require("PUBLIC_BASE_URL" if GRADER_ROLE == "public" else "ADMIN_BASE_URL")
_HOSTNAME, _ORIGIN = env.base_url_parts(_BASE_URL)
ALLOWED_HOSTS = [_HOSTNAME]
CSRF_TRUSTED_ORIGINS = [_ORIGIN]

SECRET_KEY = env.read_secret("django_secret_key")

# Never True, not even in the dev stack: the error pages would leak settings and code.
DEBUG = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "grader",
        "USER": "grader",
        "PASSWORD": env.read_secret("db_password"),
        "HOST": env.require("GRADER_DB_HOST"),
        "PORT": "5432",
    }
}

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "th"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
