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
# This role's own address, for links it prints (the first-run setup link).
BASE_URL = _BASE_URL.rstrip("/")
# The public site's address; the admin role shows it as the Google redirect URI to register.
PUBLIC_BASE_URL = (
    _BASE_URL if GRADER_ROLE == "public" else env.optional("PUBLIC_BASE_URL", "")
).rstrip("/")
ALLOWED_HOSTS = [_HOSTNAME]
CSRF_TRUSTED_ORIGINS = [_ORIGIN]
if GRADER_ROLE == "admin":
    # Other addresses the admin site is opened at, besides ADMIN_BASE_URL: say, the NAS's Tailscale
    # IP or name, "http://100.x.y.z:8081,http://nas.tailnet.ts.net:8081" (docs/DEPLOY.md).
    for _host, _origin in env.extra_base_urls(env.optional("GRADER_ADMIN_EXTRA_URLS", "")):
        ALLOWED_HOSTS.append(_host)
        CSRF_TRUSTED_ORIGINS.append(_origin)
    # The worker calls web-admin directly on judge_net, by its service name. Requests through the
    # edge with that Host header still fail the zone check for /internal/* (see core.middleware).
    ALLOWED_HOSTS.append(env.optional("GRADER_INTERNAL_HOST", "web-admin"))

# The edge's addresses: only requests from these may say who the client is (Cf-Connecting-Ip,
# X-Forwarded-For); see core.ratelimit.client_ip. Empty: never trust those headers.
GRADER_EDGE_ADDRESSES = env.ip_addresses(env.optional("GRADER_EDGE_ADDRESSES", ""))

SECRET_KEY = env.read_secret("django_secret_key")

# The worker's bearer token for /internal/*. Only the admin role routes those URLs, so only it
# reads (and has a copy of) the token.
WORKER_TOKEN = (
    env.read_secret("worker_token", "GRADER_WORKER_SECRETS_DIR") if GRADER_ROLE == "admin" else ""
)

# Never True, not even in the dev stack: the error pages would leak settings and code.
DEBUG = False

INSTALLED_APPS = [
    "config.admin.GraderAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "core",
    "problems",
    "judging",
]

MIDDLEWARE = [
    # First: Docker's healthcheck, which may use any Host header (see the class).
    "core.middleware.HealthzMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.EdgeZoneMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.RoleSessionMiddleware",
    "core.middleware.ThaiByDefaultMiddleware",
    "django.middleware.locale.LocaleMiddleware",
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
                "core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

_DB_USER, _DB_PASSWORD_SECRET = env.database_login(env.optional("GRADER_DB_USER", ""))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "grader",
        "USER": _DB_USER,
        "PASSWORD": env.read_secret(_DB_PASSWORD_SECRET),
        "HOST": env.require("GRADER_DB_HOST"),
        "PORT": "5432",
    }
}

AUTH_USER_MODEL = "accounts.User"

# Sessions live in the database (so "log out everywhere" and the role check can reach them).
# Cookie names differ per role; see config.env.ROLE_COOKIE_NAMES.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 30 * 24 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_NAME = env.role_cookie_names(GRADER_ROLE)["SESSION_COOKIE_NAME"]
CSRF_COOKIE_NAME = env.role_cookie_names(GRADER_ROLE)["CSRF_COOKIE_NAME"]

# HTTPS. The public site is only ever reached over HTTPS (Cloudflare → tunnel → edge), so its
# cookies are Secure, it sends HSTS, and it trusts the edge's X-Forwarded-Proto. The admin site
# is plain HTTP on the home network by design, so none of that applies there. The deploy-check
# warnings this leaves are silenced on purpose; see docs/DECISIONS.md.
if GRADER_ROLE == "public":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    LANGUAGE_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 365 * 24 * 60 * 60
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SILENCED_SYSTEM_CHECKS = [
        "security.W008",  # HTTP→HTTPS redirect: done by Cloudflare; the edge only speaks HTTP
        "security.W021",  # HSTS preload: needs the whole domain on the preload list; not ours
    ]
else:
    SILENCED_SYSTEM_CHECKS = [
        "security.W004",  # no HSTS: the admin site is plain HTTP on the LAN
        "security.W008",  # no HTTPS redirect: same reason
        "security.W012",  # session cookie not Secure: it must work over plain HTTP
        "security.W016",  # CSRF cookie not Secure: same reason
    ]

# Only staff have passwords, and only the admin role has a password login page.
LOGIN_URL = "/admin/login/" if GRADER_ROLE == "admin" else "/"
LOGOUT_REDIRECT_URL = LOGIN_URL

# Argon2 for new passwords; PBKDF2 stays listed only so older hashes can still be checked.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Google sign-in (public role; the client ID and secret are in SiteSetting). The browser goes to
# Google's sign-in page directly; the token and signing-key requests go through the edge's :8090
# listener, the web role's only way out. The dev stack points these at its fake provider.
GOOGLE_AUTHORIZE_URL = env.optional(
    "GRADER_GOOGLE_AUTHORIZE_URL", "https://accounts.google.com/o/oauth2/v2/auth"
)
GOOGLE_TOKEN_URL = env.optional("GRADER_GOOGLE_TOKEN_URL", "http://edge:8090/token")
GOOGLE_JWKS_URL = env.optional("GRADER_GOOGLE_JWKS_URL", "http://edge:8090/certs")
# Google documents both spellings of its issuer. The first one is what we store.
GOOGLE_ISSUERS = env.optional(
    "GRADER_GOOGLE_ISSUERS", "https://accounts.google.com,accounts.google.com"
).split(",")
GOOGLE_REDIRECT_URI = env.require("PUBLIC_BASE_URL").rstrip("/") + "/auth/google/callback"

# Thai first; English when the visitor picks it (the choice is kept in a cookie).
LANGUAGE_CODE = "th"
LANGUAGES = [("th", "ไทย"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "g_lang"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

# The problem store: immutable version folders <store>/<slug>/<version>/ (read-only in the
# public role).
GRADER_PROBLEMS_DIR = Path(env.optional("GRADER_PROBLEMS_DIR", "/data/problems"))
# Admin role only: uploads in progress and SETUP-LINK.txt (readable in the ZimaOS Files app).
GRADER_UPLOADS_DIR = Path(env.optional("GRADER_UPLOADS_DIR", "/data/uploads"))
# Admin role only, read-only: the backup files made by the backup service (Backups page).
GRADER_BACKUPS_DIR = Path(env.optional("GRADER_BACKUPS_DIR", "/data/backups"))
# The largest problem zip the Import page takes: the importer's own limit for everything unpacked.
GRADER_MAX_UPLOAD_BYTES = 1024**3

# Submit limits, per user (judging/views.py): seconds between submissions, submissions per hour,
# and how many may wait for the judge at once.
GRADER_SUBMIT_COOLDOWN_SECONDS = 10
GRADER_SUBMIT_PER_HOUR = 120
GRADER_SUBMIT_MAX_WAITING = 3

SITE_NAME_TH = env.optional("SITE_NAME_TH", "เกรดเดอร์")
SITE_NAME_EN = env.optional("SITE_NAME_EN", "Grader")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# WhiteNoise serves static files (Django itself doesn't, with DEBUG off). Images serve the
# collected STATIC_ROOT; the dev stack and tests serve straight from the apps' static folders.
WHITENOISE_USE_FINDERS = env.optional("GRADER_STATIC_FROM_APPS", "0") == "1"
WHITENOISE_AUTOREFRESH = WHITENOISE_USE_FINDERS

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
