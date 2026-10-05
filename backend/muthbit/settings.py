import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "verifier",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "muthbit.urls"
WSGI_APPLICATION = "muthbit.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=600
    )
}

LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [o for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # الواجهة البرمجية عامة لا تحتاج تسجيل دخول. بدون هذا، من سجّل الدخول إلى /admin
    # على النطاق نفسه يُصادَق بالجلسة فيطلب DRF رمز CSRF ويرفض الطلب.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("THROTTLE_RATE", "60/min")},
}

# ---- إعدادات مُثبِت ----
MUTHBIT = {
    "INDEX_DIR": Path(os.environ.get("MUTHBIT_INDEX_DIR", BASE_DIR / "index")),
    # البحث الدلالي اختياري: 1 لتفعيله بعد تثبيت requirements-embeddings.txt
    "USE_EMBEDDINGS": os.environ.get("MUTHBIT_USE_EMBEDDINGS", "0") == "1",
    "EMBEDDING_MODEL": os.environ.get("MUTHBIT_EMBEDDING_MODEL", "intfloat/multilingual-e5-base"),
    # العتبات تُضبط بسكربت التقييم (--sweep) ثم تُثبّت هنا
    "THRESHOLD_EXACT": float(os.environ.get("MUTHBIT_THRESHOLD_EXACT", "0.85")),
    "THRESHOLD_MATCH": float(os.environ.get("MUTHBIT_THRESHOLD_MATCH", "0.55")),
    "LEXICAL_WEIGHT": float(os.environ.get("MUTHBIT_LEXICAL_WEIGHT", "0.6")),
    "MAX_INPUT_CHARS": int(os.environ.get("MUTHBIT_MAX_INPUT_CHARS", "4000")),
    # احتياط فقط إن لم يُضف أي نموذج من لوحة الإدارة ووُجد ANTHROPIC_API_KEY
    "LLM_MODEL": os.environ.get("MUTHBIT_LLM_MODEL", "claude-haiku-4-5-20251001"),
    # الدرر السنية
    "DORAR_ENABLED": os.environ.get("MUTHBIT_DORAR_ENABLED", "1") == "1",
    "DORAR_TIMEOUT": int(os.environ.get("MUTHBIT_DORAR_TIMEOUT", "8")),
    "DORAR_CACHE_HOURS": int(os.environ.get("MUTHBIT_DORAR_CACHE_HOURS", "168")),
    "GRADE_MAP_REVIEWED_ONLY": os.environ.get("MUTHBIT_GRADE_MAP_REVIEWED_ONLY", "1") == "1",
    "DORAR_QUERY_WORDS": int(os.environ.get("MUTHBIT_DORAR_QUERY_WORDS", "8")),
}
