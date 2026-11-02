"""Authentification de l'API : jeton de session « Authorization: Bearer … »."""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission

from .. import services
from ..outils import ErreurMetier


def jeton_de(requete) -> str | None:
    entete = requete.META.get("HTTP_AUTHORIZATION", "")
    return entete[7:].strip() if entete.lower().startswith("bearer ") else None


class JetonSoignant(BaseAuthentication):
    """Un jeton absent ou invalide laisse la requête anonyme : les routes protégées répondent 401."""

    def authenticate(self, requete):
        jeton = jeton_de(requete)
        compte = services.compte_par_jeton(jeton)
        return (compte, jeton) if compte else None

    def authenticate_header(self, requete):
        return "Bearer"


def _compte(requete):
    compte = getattr(requete, "user", None)
    return compte if compte is not None and getattr(compte, "etablissement_id", None) else None


class EstSoignant(BasePermission):
    def has_permission(self, requete, vue):
        if not _compte(requete):
            raise ErreurMetier("Session expirée. Reconnectez-vous.", 401)
        return True


class EstAdministrateur(BasePermission):
    def has_permission(self, requete, vue):
        compte = _compte(requete)
        if not compte:
            raise ErreurMetier("Session expirée. Reconnectez-vous.", 401)
        if compte.role != "admin":
            raise ErreurMetier("Réservé aux administrateurs.", 403)
        return True
