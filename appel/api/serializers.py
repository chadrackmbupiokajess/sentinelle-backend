"""Validation des données reçues (mêmes limites que l'ancienne API)."""

from rest_framework import serializers


class Inscription(serializers.Serializer):
    """Demande de compte du personnel, depuis l'adresse de l'établissement."""

    identifiant = serializers.CharField(min_length=3, max_length=30)
    nom = serializers.CharField(min_length=2, max_length=60)
    mot_de_passe = serializers.CharField(min_length=1, max_length=200, trim_whitespace=False)
    etablissement = serializers.CharField(max_length=60)  # adresse (ex. « jessna-hopital »)


class Connexion(serializers.Serializer):
    identifiant = serializers.CharField(min_length=1, max_length=30)
    mot_de_passe = serializers.CharField(min_length=1, max_length=200, trim_whitespace=False)
    # Sur la page d'un établissement : seuls ses comptes peuvent s'y connecter.
    etablissement = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")


class CodeActivation(serializers.Serializer):
    code = serializers.CharField(min_length=1, max_length=20)


class Activation(serializers.Serializer):
    code = serializers.CharField(min_length=1, max_length=20)
    identifiant = serializers.CharField(min_length=3, max_length=30)
    nom = serializers.CharField(min_length=2, max_length=60)
    mot_de_passe = serializers.CharField(min_length=1, max_length=200, trim_whitespace=False)


class ChangementMotDePasse(serializers.Serializer):
    actuel = serializers.CharField(min_length=1, max_length=200, trim_whitespace=False)
    nouveau = serializers.CharField(min_length=1, max_length=200, trim_whitespace=False)


class NouveauStatut(serializers.Serializer):
    statut = serializers.CharField(max_length=20)


class NouveauRole(serializers.Serializer):
    role = serializers.CharField(max_length=20)


class NouvelleChambre(serializers.Serializer):
    id = serializers.CharField(min_length=1, max_length=12)
    libelle = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    service = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    lits = serializers.IntegerField(min_value=1, max_value=40, required=False, default=5)


class NombreLits(serializers.Serializer):
    lits = serializers.IntegerField(min_value=1, max_value=40)


class Admission(serializers.Serializer):
    initiales = serializers.CharField(max_length=10, required=False, allow_blank=True, default="")
    sortie_prevue = serializers.CharField(max_length=40, required=False, allow_blank=True, allow_null=True, default=None)


class SortiePrevue(serializers.Serializer):
    sortie_prevue = serializers.CharField(max_length=40, required=False, allow_blank=True, allow_null=True, default=None)


class CodeSejour(serializers.Serializer):
    code = serializers.CharField(min_length=1, max_length=10)


class NouveauMessage(serializers.Serializer):
    texte = serializers.CharField(min_length=1, max_length=500)


class ClesPush(serializers.Serializer):
    p256dh = serializers.CharField(min_length=20, max_length=256)
    auth = serializers.CharField(min_length=8, max_length=128)


class AbonnementPush(serializers.Serializer):
    endpoint = serializers.CharField(min_length=1, max_length=2048)
    keys = ClesPush()


class SuppressionAbonnementPush(serializers.Serializer):
    endpoint = serializers.CharField(min_length=1, max_length=2048)


class NomEtablissement(serializers.Serializer):
    nom = serializers.CharField(min_length=2, max_length=80)


class Logo(serializers.Serializer):
    """Images en base64 (préparées par le navigateur : logo, icône carrée, icône « maskable »)."""

    image = serializers.CharField(max_length=1_500_000)
    icone = serializers.CharField(max_length=1_500_000)
    icone_masquable = serializers.CharField(max_length=1_500_000)


def valider(classe, donnees) -> dict:
    s = classe(data=donnees)
    s.is_valid(raise_exception=True)
    return s.validated_data
