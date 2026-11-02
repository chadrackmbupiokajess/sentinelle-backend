"""
Réglages Django de Sentinelle.

Les valeurs sont écrites directement ci-dessous (bloc « RÉGLAGES ») : le dépôt GitHub est PRIVÉ.
Une variable d'environnement, ou un fichier .env à côté de manage.py, reste prioritaire si besoin.
Le chemin de la base et le HTTPS s'adaptent seuls : PythonAnywhere ou PC local.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _charger_env(chemin: Path) -> None:
    """Lit un fichier .env simple (CLE=valeur). Les variables déjà définies sont prioritaires."""
    if not chemin.is_file():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle, valeur = cle.strip(), valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "'\"":
            valeur = valeur[1:-1]
        os.environ.setdefault(cle, valeur)


_charger_env(BASE_DIR / ".env")

# ==========================================================================
# RÉGLAGES (dépôt privé) — à modifier ici
# ==========================================================================
# Sur PythonAnywhere (compte gescmlc) ou sur le PC ?
EN_LIGNE = Path("/home/gescmlc").is_dir()

REGLAGES = {
    # Clé secrète Django (ne la changez pas une fois en ligne : les sessions de l'admin seraient perdues).
    "DJANGO_SECRET_KEY": "jw_KuPkXtNC3gf39FfPz0AshFiCZxm4-pcS46XGws748usvFQ5d4K-zHTN-ekzo_YjQ",
    # Base de données : fichier sentinelle.db à côté de manage.py (PC et PythonAnywhere).
    # Il est exclu de git (.gitignore : *.db) : un « git pull » ne l'écrase jamais.
    "SENTINELLE_BD": str(BASE_DIR / "sentinelle.db"),
    # Adresse(s) du frontend autorisée(s) à appeler l'API (séparées par des virgules).
    # En ligne : le site Vercel (+ localhost:3000 pour tester le frontend du PC contre l'API en ligne).
    # Sur le PC : « * » (toutes), pour ouvrir le site depuis un téléphone du réseau local.
    "SENTINELLE_ORIGINES": "https://jessna.vercel.app,http://localhost:3000" if EN_LIGNE else "*",
    # Site en HTTPS (PythonAnywhere) : cookies sécurisés. 0 sur le PC (http://localhost).
    "SENTINELLE_HTTPS": "1" if EN_LIGNE else "0",
    # Noms de domaine acceptés par Django.
    "SENTINELLE_HOTES": "gescmlc.pythonanywhere.com" if EN_LIGNE else "*",
    # Notifications push (clés VAPID, générées pour Sentinelle).
    "SENTINELLE_VAPID_PUBLIC_KEY": "BCYEeHOZbpwGDZf022weipYC_j4XI1YLLxMqFsMERLot4KhF69226aIFUMsl-8Z5qQv_hIT9fr8jjVeqso5xIYc",
    "SENTINELLE_VAPID_PRIVATE_KEY": "lEIUTgzZCkr2ETMEvCU_M7rxMEkSljhtzVbAOqbBh7s",
    # Contact exigé par les services de notification : mettez votre adresse e-mail.
    "SENTINELLE_VAPID_SUBJECT": "mailto:vous@exemple.com",
    # --- VPS uniquement -------------------------------------------------------
    # WebSockets (Django Channels, serveur uvicorn) : "1" sur le VPS.
    "SENTINELLE_WEBSOCKET": "0",
    # Plusieurs processus uvicorn : Redis obligatoire, ex. "redis://localhost:6379/0".
    "SENTINELLE_REDIS_URL": "",
}


def env(nom: str, defaut: str = "") -> str:
    """Variable d'environnement (ou .env), sinon valeur du bloc RÉGLAGES, sinon la valeur par défaut."""
    return os.environ.get(nom, REGLAGES.get(nom, defaut)).strip()


def env_bool(nom: str, defaut: bool = False) -> bool:
    return env(nom, "1" if defaut else "0").lower() in ("1", "true", "oui", "yes", "on")


def env_liste(nom: str, defaut: str) -> list[str]:
    return [x.strip() for x in env(nom, defaut).split(",") if x.strip()]


def _cle_secrete() -> str:
    """DJANGO_SECRET_KEY (bloc RÉGLAGES), sinon une clé générée une fois et conservée dans .cle_secrete."""
    cle = env("DJANGO_SECRET_KEY")
    if cle:
        return cle
    fichier = BASE_DIR / ".cle_secrete"
    try:
        return fichier.read_text(encoding="utf-8").strip()
    except OSError:
        cle = secrets.token_urlsafe(50)
        try:
            fichier.write_text(cle, encoding="utf-8")
        except OSError:
            pass
        return cle


SECRET_KEY = _cle_secrete()
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env_liste("SENTINELLE_HOTES", "*")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "appel",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "appel.intergiciels.EntetesSecurite",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "sentinelle.urls"
WSGI_APPLICATION = "sentinelle.wsgi.application"
ASGI_APPLICATION = "sentinelle.asgi.application"

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

# --------------------------------------------------------------------------
# Base de données : SQLite par défaut (fichier SENTINELLE_BD, hors du dépôt git).
# Pour PostgreSQL plus tard (SaaS), remplacez simplement ce bloc.
# --------------------------------------------------------------------------
CHEMIN_BD = Path(env("SENTINELLE_BD", str(BASE_DIR / "sentinelle.db")))
try:
    CHEMIN_BD.parent.mkdir(parents=True, exist_ok=True)  # si un autre dossier est choisi
except OSError:
    pass
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(CHEMIN_BD),
        "OPTIONS": {"timeout": 20},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "appel.Compte"
AUTHENTICATION_BACKENDS = ["appel.backends.ComptesPlateforme"]
# L'identifiant n'est unique que par établissement (contraintes du modèle Compte) : voulu.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.ScryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"

# Messages vocaux : jusqu'à 5 Mo par envoi.
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# --------------------------------------------------------------------------
# API (Django REST Framework)
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["appel.api.authentification.JetonSoignant"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "appel.api.erreurs.gestionnaire",
    "UNAUTHENTICATED_USER": None,
}

# CORS : adresse(s) du frontend (ex. https://sentinelle.vercel.app), « * » = toutes.
_origines = env_liste("SENTINELLE_ORIGINES", "*")
CORS_ALLOW_ALL_ORIGINS = "*" in _origines
CORS_ALLOWED_ORIGINS = [o for o in _origines if o != "*"]
CORS_ALLOW_HEADERS = ["authorization", "content-type", "x-sejour"]
CORS_PREFLIGHT_MAX_AGE = 600

# Derrière un proxy (PythonAnywhere, Caddy) : la vraie adresse du visiteur est
# dans X-Real-IP / X-Forwarded-For (sert aux limites anti-abus).
SENTINELLE_IP_PROXY = env_bool("SENTINELLE_IP_PROXY", True)
if SENTINELLE_IP_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# En ligne (HTTPS) : SENTINELLE_HTTPS=1 → cookies sécurisés et HSTS.
# La redirection http → https est faite par l'hébergeur (« Force HTTPS » sur PythonAnywhere,
# Caddy sur le VPS). Laisser à 0 pour tester sur le réseau local en http.
if env_bool("SENTINELLE_HTTPS"):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

# --------------------------------------------------------------------------
# Sentinelle
# --------------------------------------------------------------------------
SENTINELLE_SESSION_JOURS = int(env("SENTINELLE_SESSION_JOURS", "0"))  # 0 = jusqu'à la déconnexion
SENTINELLE_SEJOUR_JOURS = int(env("SENTINELLE_SEJOUR_JOURS", "30"))
SENTINELLE_LITS = int(env("SENTINELLE_LITS", "5"))
SENTINELLE_PRESENCE_S = int(env("SENTINELLE_PRESENCE_S", "90"))

SENTINELLE_VAPID_PUBLIC_KEY = env("SENTINELLE_VAPID_PUBLIC_KEY")
SENTINELLE_VAPID_PRIVATE_KEY = env("SENTINELLE_VAPID_PRIVATE_KEY")
SENTINELLE_VAPID_SUBJECT = env("SENTINELLE_VAPID_SUBJECT")

# WebSockets (Django Channels) : uniquement sur un serveur ASGI (VPS), pas sur PythonAnywhere.
SENTINELLE_WEBSOCKET = env_bool("SENTINELLE_WEBSOCKET")
_redis = env("SENTINELLE_REDIS_URL")
CHANNEL_LAYERS = {
    "default": (
        {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [_redis]}}
        if _redis
        else {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    )
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"sentinelle": {"handlers": ["console"], "level": "INFO"}},
}
