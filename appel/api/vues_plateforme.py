"""
Routes de l'espace propriétaire (page /plateforme du frontend) :

  POST /api/plateforme/connexion                          → jeton (comptes superutilisateur uniquement)
  GET  /api/plateforme/moi
  GET  /api/plateforme/etablissements                     → résumé + liste avec statistiques
  POST /api/plateforme/etablissements                     → créer {nom}
  GET  /api/plateforme/etablissements/{id}                → détail + comptes
  POST /api/plateforme/etablissements/{id}                → modifier {nom?, actif?} (suspendre / réactiver)
  POST /api/plateforme/etablissements/{id}/activation      → nouveau code d'activation (7 jours)
  POST /api/plateforme/etablissements/{id}/comptes/{cid}/reinitialiser → mot de passe provisoire
  GET  /api/plateforme/types                              → types d'établissement et leur vocabulaire
  POST /api/plateforme/types                              → créer {nom, vocabulaire}
  POST /api/plateforme/types/{id}  · DELETE                → modifier / supprimer
"""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .. import plateforme, services, temps_reel
from ..models import Compte
from ..outils import ErreurMetier, ip, lim_connexion_ip
from .authentification import jeton_de
from .serializers import Connexion, valider


class EstProprietaire(BasePermission):
    def has_permission(self, requete, vue):
        compte = plateforme.proprietaire_par_jeton(jeton_de(requete))
        if not compte:
            raise ErreurMetier("Session expirée. Reconnectez-vous.", 401)
        requete.proprietaire = compte
        return True


class Modification(serializers.Serializer):
    nom = serializers.CharField(min_length=2, max_length=80, required=False)
    actif = serializers.BooleanField(required=False)
    type = serializers.IntegerField(required=False)


class NouvelEtablissement(serializers.Serializer):
    nom = serializers.CharField(min_length=2, max_length=80)
    type = serializers.IntegerField(required=False, allow_null=True, default=None)


class TypeSaisi(serializers.Serializer):
    nom = serializers.CharField(min_length=2, max_length=60, required=False)
    vocabulaire = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=120), required=False)


@api_view(["POST"])
def connexion(requete):
    if not lim_connexion_ip.autoriser(ip(requete)):
        raise ErreurMetier("Trop de tentatives depuis cet appareil. Réessayez dans quelques minutes.", 429)
    d = valider(Connexion, requete.data)
    compte = plateforme.authentifier(d["identifiant"], d["mot_de_passe"])
    jeton = services.creer_session(compte, requete.META.get("HTTP_USER_AGENT", ""))
    return Response({"jeton": jeton, "compte": plateforme.proprietaire_public(compte)})


@api_view(["GET"])
@permission_classes([EstProprietaire])
def moi(requete):
    return Response(plateforme.proprietaire_public(requete.proprietaire))


@api_view(["GET", "POST"])
@permission_classes([EstProprietaire])
def etablissements(requete):
    if requete.method == "POST":
        d = valider(NouvelEtablissement, requete.data)
        return Response(plateforme.creer(d["nom"], d["type"]), status=201)
    return Response(plateforme.lister_etablissements())


@api_view(["GET", "POST"])
@permission_classes([EstProprietaire])
def etablissement(requete, etab_id: int):
    if requete.method == "POST":
        d = valider(Modification, requete.data)
        etab = plateforme.modifier(etab_id, d.get("nom"), d.get("actif"), d.get("type"))
        if d.get("actif") is False:
            # Suspension : tous les écrans de l'établissement sont déconnectés immédiatement.
            for compte_id in Compte.objects.filter(etablissement=etab).values_list("id", flat=True):
                temps_reel.fermer_compte(etab.id, compte_id)
    return Response(plateforme.detail(etab_id))


@api_view(["POST"])
@permission_classes([EstProprietaire])
def reinitialiser_compte(requete, etab_id: int, compte_id: int):
    resultat = plateforme.reinitialiser_compte(etab_id, compte_id)
    temps_reel.fermer_compte(etab_id, compte_id)
    return Response(resultat)


@api_view(["POST"])
@permission_classes([EstProprietaire])
def nouveau_code_activation(requete, etab_id: int):
    return Response(plateforme.nouveau_code_activation(etab_id))


@api_view(["GET", "POST"])
@permission_classes([EstProprietaire])
def types(requete):
    if requete.method == "POST":
        d = valider(TypeSaisi, requete.data)
        return Response(plateforme.creer_type(d.get("nom", ""), d.get("vocabulaire")), status=201)
    return Response(plateforme.lister_types())


@api_view(["POST", "DELETE"])
@permission_classes([EstProprietaire])
def type_etablissement(requete, type_id: int):
    if requete.method == "DELETE":
        plateforme.supprimer_type(type_id)
        return Response(status=204)
    d = valider(TypeSaisi, requete.data)
    return Response(plateforme.modifier_type(type_id, d.get("nom"), d.get("vocabulaire")))
