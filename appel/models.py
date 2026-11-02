"""
Modèles de Sentinelle.

Chaque établissement (hôpital, clinique) a ses propres comptes, chambres, appels
et messages : rien n'est partagé entre établissements.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from .outils import maintenant


class TypeEtablissement(models.Model):
    """Hôpital, centre médical, restaurant… : le vocabulaire affiché dans toutes les pages."""

    nom = models.CharField(max_length=60, unique=True)
    vocabulaire = models.JSONField(default=dict, blank=True)  # voir vocabulaire.py
    cree_le = models.DateTimeField(default=maintenant)

    class Meta:
        verbose_name = "type d'établissement"
        ordering = ["nom"]

    def __str__(self) -> str:
        return self.nom


class Etablissement(models.Model):
    nom = models.CharField(max_length=80)
    type = models.ForeignKey(TypeEtablissement, on_delete=models.PROTECT, related_name="etablissements")
    # Adresse propre : localhost:3000/jessna-hopital
    slug = models.SlugField(max_length=60, unique=True)
    # Code donné par le propriétaire pour créer le premier compte (administrateur) :
    # utilisable une seule fois, expire au bout de 7 jours.
    code_activation = models.CharField(max_length=12, unique=True, null=True, blank=True)
    code_activation_expire = models.DateTimeField(null=True, blank=True)
    active_le = models.DateTimeField(null=True, blank=True)
    actif = models.BooleanField(default=True)
    cree_le = models.DateTimeField(default=maintenant)

    class Meta:
        verbose_name = "établissement"
        ordering = ["nom"]

    def __str__(self) -> str:
        return self.nom

    @property
    def vocabulaire(self) -> dict:
        from .vocabulaire import complet

        return complet(self.type.vocabulaire if self.type_id else None)


# --------------------------------------------------------------------------
# Comptes soignants
# --------------------------------------------------------------------------
class GestionnaireComptes(BaseUserManager):
    def create_user(self, identifiant, password=None, **champs):
        compte = self.model(identifiant=identifiant.strip().lower(), **champs)
        compte.set_password(password)
        compte.save(using=self._db)
        return compte

    def create_superuser(self, identifiant, password=None, **champs):
        """Propriétaire de la plateforme : accès à /admin/ du backend (admin Django) et à /plateforme du site, sans établissement."""
        champs.setdefault("nom", identifiant)
        champs.update(is_staff=True, is_superuser=True, role="admin", statut="actif")
        return self.create_user(identifiant, password, **champs)


class Compte(AbstractBaseUser, PermissionsMixin):
    ROLES = [("infirmier", "Infirmier"), ("medecin", "Médecin"), ("admin", "Administrateur")]
    STATUTS = [
        ("en_attente", "En attente"),
        ("actif", "Actif"),
        ("refuse", "Refusé"),
        ("desactive", "Désactivé"),
    ]

    etablissement = models.ForeignKey(
        Etablissement, null=True, blank=True, on_delete=models.CASCADE, related_name="comptes"
    )
    identifiant = models.CharField(max_length=30, unique=True)
    nom = models.CharField(max_length=60)
    role = models.CharField(max_length=10, choices=ROLES, default="infirmier")
    statut = models.CharField(max_length=12, choices=STATUTS, default="en_attente")
    cree_le = models.DateTimeField(default=maintenant)
    valide_par = models.CharField(max_length=60, null=True, blank=True)
    echecs = models.PositiveIntegerField(default=0)
    bloque_jusqua = models.DateTimeField(null=True, blank=True)
    doit_changer = models.BooleanField(default=False)
    is_staff = models.BooleanField("accès à la gestion de la plateforme", default=False)

    objects = GestionnaireComptes()

    USERNAME_FIELD = "identifiant"
    REQUIRED_FIELDS = ["nom"]

    class Meta:
        verbose_name = "compte"
        indexes = [models.Index(fields=["etablissement", "statut"])]

    def __str__(self) -> str:
        return f"{self.nom} ({self.identifiant})"

    @property
    def is_active(self) -> bool:  # utilisé par l'admin Django
        return self.statut == "actif"


class SessionSoignant(models.Model):
    """Session d'un appareil. Seule l'empreinte du jeton est stockée."""

    empreinte = models.CharField(max_length=64, primary_key=True)
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="sessions")
    cree_le = models.DateTimeField(default=maintenant)
    vu_le = models.DateTimeField(default=maintenant)
    expire_le = models.DateTimeField()
    appareil = models.CharField(max_length=120, blank=True, default="")


class AbonnementPush(models.Model):
    endpoint = models.CharField(max_length=2048, primary_key=True)
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="abonnements_push")
    p256dh = models.CharField(max_length=256)
    auth = models.CharField(max_length=128)
    cree_le = models.DateTimeField(default=maintenant)


# --------------------------------------------------------------------------
# Chambres, séjours, appels
# --------------------------------------------------------------------------
class Chambre(models.Model):
    etablissement = models.ForeignKey(Etablissement, on_delete=models.CASCADE, related_name="chambres")
    numero = models.CharField(max_length=12)  # « id » dans l'API (ex. « 104 »)
    libelle = models.CharField(max_length=60)
    service = models.CharField(max_length=60, blank=True, default="")
    code = models.CharField(max_length=8)  # code imprimé sur la porte (lien /c/104-K7QM)
    lits = models.PositiveSmallIntegerField(default=5)
    essais_sejour = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["etablissement", "numero"], name="chambre_unique_par_etablissement")
        ]
        indexes = [models.Index(fields=["numero", "code"])]

    def __str__(self) -> str:
        return f"{self.libelle} — {self.etablissement}"


class Sejour(models.Model):
    id = models.CharField(max_length=32, primary_key=True)
    chambre = models.ForeignKey(Chambre, on_delete=models.CASCADE, related_name="sejours")
    lit = models.PositiveSmallIntegerField(default=1)
    code = models.CharField(max_length=8)
    initiales = models.CharField(max_length=10, blank=True, default="")
    admis_le = models.DateTimeField()
    admis_par = models.CharField(max_length=60)
    expire_le = models.DateTimeField()
    sortie_prevue = models.DateTimeField(null=True, blank=True)
    sortie_le = models.DateTimeField(null=True, blank=True)
    sortie_par = models.CharField(max_length=60, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["chambre", "sortie_le"])]


class AppareilPatient(models.Model):
    """Téléphone d'un patient, lié à son séjour après saisie du code."""

    empreinte = models.CharField(max_length=64, primary_key=True)
    sejour = models.ForeignKey(Sejour, on_delete=models.CASCADE, related_name="appareils")
    cree_le = models.DateTimeField(default=maintenant)


class Alerte(models.Model):
    STATUTS_ACTIFS = ("en_attente", "prise_en_charge")

    id = models.CharField(max_length=32, primary_key=True)
    chambre = models.ForeignKey(Chambre, on_delete=models.CASCADE, related_name="alertes")
    lit = models.PositiveSmallIntegerField(null=True)
    sejour = models.ForeignKey(Sejour, null=True, on_delete=models.SET_NULL, related_name="alertes")
    statut = models.CharField(max_length=16)
    cree_le = models.DateTimeField()
    accuse_le = models.DateTimeField(null=True)
    accuse_par = models.CharField(max_length=60, null=True)
    termine_le = models.DateTimeField(null=True)
    termine_par = models.CharField(max_length=60, null=True)
    annule_le = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["statut"]), models.Index(fields=["cree_le"])]


# --------------------------------------------------------------------------
# Messagerie
# --------------------------------------------------------------------------
class Message(models.Model):
    """Message d'équipe, visible par tous les soignants de l'établissement."""

    etablissement = models.ForeignKey(Etablissement, on_delete=models.CASCADE, related_name="messages")
    auteur = models.CharField(max_length=60)
    texte = models.CharField(max_length=500)
    cree_le = models.DateTimeField(default=maintenant)


class MessagePrive(models.Model):
    TYPES = [("texte", "Texte"), ("audio", "Vocal"), ("appel_manque", "Appel manqué")]

    expediteur = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="messages_envoyes")
    destinataire = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="messages_recus")
    texte = models.CharField(max_length=500, blank=True, default="")
    cree_le = models.DateTimeField(default=maintenant)
    lu_le = models.DateTimeField(null=True)
    type = models.CharField(max_length=14, choices=TYPES, default="texte")
    audio = models.BinaryField(null=True)
    audio_type = models.CharField(max_length=40, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["expediteur", "destinataire", "id"]),
            models.Index(fields=["destinataire", "expediteur", "id"]),
        ]


class Urgence(models.Model):
    """Urgence privée (« Vous êtes demandé(e) ») : sonne jusqu'au « J'arrive »."""

    message = models.OneToOneField(MessagePrive, primary_key=True, on_delete=models.CASCADE)
    destinataire = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="urgences")
    actif = models.BooleanField(default=True)
    cree_le = models.DateTimeField(default=maintenant)
    ferme_par = models.CharField(max_length=60, null=True)
    ferme_le = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["destinataire", "actif"])]


# --------------------------------------------------------------------------
# Temps réel (AJAX) : journal des événements et présence — voir temps_reel.py
# --------------------------------------------------------------------------
class EvenementTempsReel(models.Model):
    etablissement = models.ForeignKey(Etablissement, on_delete=models.CASCADE)
    compte = models.ForeignKey(Compte, null=True, on_delete=models.CASCADE)  # None = tous les soignants
    donnees = models.TextField()
    cree_le = models.FloatField(db_index=True)


class Presence(models.Model):
    """Dernier passage d'un soignant connecté (AJAX ou WebSocket)."""

    compte = models.OneToOneField(Compte, primary_key=True, on_delete=models.CASCADE)
    vu_le = models.FloatField(db_index=True)
