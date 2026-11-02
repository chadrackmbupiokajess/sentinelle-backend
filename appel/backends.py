"""Connexion à l'admin Django (/admin/ du backend) : comptes de la plateforme uniquement."""

from django.contrib.auth.backends import ModelBackend


class ComptesPlateforme(ModelBackend):
    """Comme ModelBackend ; la recherche du compte (get_by_natural_key) ignore les comptes
    des établissements, dont les identifiants ne sont uniques que dans leur établissement."""
