"""
Outils communs : erreurs métier, dates, jetons, codes, mots de passe, limiteur de débit.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from django.conf import settings


class ErreurMetier(Exception):
    """Erreur prévue, renvoyée telle quelle à l'interface : {"detail": message}."""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


# ------------------------------------------------------------------ dates
def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Format des dates dans l'API : « 2026-11-02T08:30:00+00:00 »."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ jetons
def nouveau_jeton() -> str:
    return secrets.token_urlsafe(32)


def empreinte_jeton(jeton: str) -> str:
    """Seule l'empreinte est stockée : un vol de la base ne permet pas d'usurper une session."""
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- codes
# Sans caractères ambigus (0/O, 1/I/L) : faciles à recopier depuis une affiche.
ALPHABET_CODE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def nouveau_code(longueur: int = 4) -> str:
    return "".join(secrets.choice(ALPHABET_CODE) for _ in range(longueur))


# --------------------------------------------------------- mots de passe
LONGUEUR_MIN_MOT_DE_PASSE = 8


def mot_de_passe_valide(mot_de_passe: str) -> str | None:
    """Renvoie un message d'erreur, ou None si le mot de passe convient."""
    if len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
        return f"Le mot de passe doit contenir au moins {LONGUEUR_MIN_MOT_DE_PASSE} caractères."
    if mot_de_passe.isdigit() or mot_de_passe.isalpha():
        return "Le mot de passe doit mélanger des lettres et des chiffres."
    return None


def mot_de_passe_temporaire() -> str:
    """Mot de passe provisoire lisible, donné par l'administrateur (ex. « Kq7m-Xp3t »)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    while True:
        brut = "".join(secrets.choice(alphabet) for _ in range(8))
        if mot_de_passe_valide(brut) is None:
            return f"{brut[:4]}-{brut[4:]}"


# ------------------------------------------------------------ adresse IP
def ip(requete) -> str:
    """Adresse du visiteur (requête Django ou scope Channels), derrière un proxy si configuré."""
    meta = getattr(requete, "META", None)
    if meta is not None:
        if settings.SENTINELLE_IP_PROXY:
            reelle = meta.get("HTTP_X_REAL_IP") or meta.get("HTTP_X_FORWARDED_FOR", "").split(",")[0]
            if reelle.strip():
                return reelle.strip()
        return meta.get("REMOTE_ADDR") or "inconnue"
    # scope Channels
    entetes = {k.decode().lower(): v.decode() for k, v in requete.get("headers", [])}
    if settings.SENTINELLE_IP_PROXY:
        reelle = entetes.get("x-real-ip") or entetes.get("x-forwarded-for", "").split(",")[0]
        if reelle.strip():
            return reelle.strip()
    client = requete.get("client")
    return client[0] if client else "inconnue"


# ------------------------------------------------------- limiteur de débit
class Limiteur:
    """Au plus `maximum` événements par `fenetre` secondes et par clé (en mémoire, par processus)."""

    def __init__(self, maximum: int, fenetre: float):
        self.maximum = maximum
        self.fenetre = fenetre
        self._evenements: dict[str, deque[float]] = defaultdict(deque)
        self._verrou = threading.Lock()

    def _purger(self, file: deque[float], t: float) -> None:
        while file and t - file[0] > self.fenetre:
            file.popleft()

    def depasse(self, cle: str) -> bool:
        """Vrai si la clé a déjà atteint sa limite (sans compter de nouvel événement)."""
        with self._verrou:
            file = self._evenements[cle]
            self._purger(file, time.monotonic())
            return len(file) >= self.maximum

    def compter(self, cle: str) -> None:
        with self._verrou:
            t = time.monotonic()
            file = self._evenements[cle]
            self._purger(file, t)
            file.append(t)

    def autoriser(self, cle: str) -> bool:
        """Compte l'événement s'il est permis ; renvoie False si la limite est atteinte."""
        with self._verrou:
            t = time.monotonic()
            file = self._evenements[cle]
            self._purger(file, t)
            if len(file) >= self.maximum:
                return False
            file.append(t)
            return True

    def vider(self) -> None:
        with self._verrou:
            self._evenements.clear()


# Limites anti-abus (par adresse IP, par lit)
lim_connexion_ip = Limiteur(20, 600)  # 20 tentatives de connexion / 10 min / IP
lim_inscription_ip = Limiteur(5, 3600)  # 5 inscriptions / heure / IP
lim_appel_lit = Limiteur(6, 600)  # 6 appels / 10 min / lit
lim_appel_ip = Limiteur(30, 600)  # 30 appels / 10 min / IP (opérateurs mobiles : IP partagées)
lim_code_faux_ip = Limiteur(20, 600)  # 20 codes de chambre faux / 10 min / IP
lim_sejour_faux_ip = Limiteur(8, 900)  # 8 codes de séjour faux / 15 min / IP
lim_etablissement_faux_ip = Limiteur(10, 900)  # 10 codes d'établissement faux / 15 min / IP

TOUS_LES_LIMITEURS = (
    lim_connexion_ip, lim_inscription_ip, lim_appel_lit, lim_appel_ip,
    lim_code_faux_ip, lim_sejour_faux_ip, lim_etablissement_faux_ip,
)
