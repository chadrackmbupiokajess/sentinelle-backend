"""Erreurs de l'API : toujours {"detail": "message en français"}, comme l'ancienne API."""

from __future__ import annotations

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from ..outils import ErreurMetier


def _premier_message(erreurs, champ: str | None = None) -> str:
    if isinstance(erreurs, dict):
        for cle, valeur in erreurs.items():
            return _premier_message(valeur, None if cle == "non_field_errors" else cle)
    if isinstance(erreurs, list) and erreurs:
        return _premier_message(erreurs[0], champ)
    texte = str(erreurs)
    return f"{champ} : {texte}" if champ else texte


def gestionnaire(exc, contexte):
    if isinstance(exc, ErreurMetier):
        return Response({"detail": exc.message}, status=exc.code)
    if isinstance(exc, exceptions.ValidationError):
        return Response(
            {"detail": _premier_message(exc.detail)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    if isinstance(exc, exceptions.ParseError):
        return Response({"detail": "Requête illisible."}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        return Response({"detail": "Session expirée. Reconnectez-vous."}, status=status.HTTP_401_UNAUTHORIZED)
    reponse = exception_handler(exc, contexte)
    if reponse is not None and isinstance(reponse.data, dict) and "detail" in reponse.data:
        reponse.data = {"detail": str(reponse.data["detail"])}
    return reponse
