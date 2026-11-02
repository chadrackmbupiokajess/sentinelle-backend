"""
Dépannage en ligne de commande (sur le serveur) :

    python manage.py sentinelle lister
    python manage.py sentinelle creer-etablissement "Clinique La Colombe" ["Restaurant"]
    python manage.py sentinelle creer-admin <identifiant> "<Nom complet>" <adresse-etablissement>
    python manage.py sentinelle reinitialiser <identifiant>

Utile si l'unique administrateur d'un établissement a oublié son mot de passe.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from appel import services
from appel.models import Compte, Etablissement, TypeEtablissement
from appel.outils import ErreurMetier, maintenant, mot_de_passe_temporaire


class Command(BaseCommand):
    help = "Dépannage des établissements et des comptes Sentinelle."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["lister", "creer-etablissement", "creer-admin", "reinitialiser"])
        parser.add_argument("valeurs", nargs="*")

    def handle(self, action, valeurs, **_):
        try:
            getattr(self, action.replace("-", "_"))(*valeurs)
        except TypeError:
            raise CommandError("Arguments manquants : voir « python manage.py sentinelle --help ».")
        except ErreurMetier as e:
            raise CommandError(e.message)

    def lister(self):
        for etab in Etablissement.objects.select_related("type"):
            etat = "activé" if etab.active_le else f"code d'activation {etab.code_activation}"
            self.stdout.write(f"\n{etab.nom}  [{etab.type.nom}]  /{etab.slug}  ({etat}{'' if etab.actif else ', SUSPENDU'})")
            for c in Compte.objects.filter(etablissement=etab).order_by("nom"):
                self.stdout.write(f"   {c.identifiant:<20} {c.nom:<28} {c.role:<10} {c.statut}")

    def creer_etablissement(self, nom, type_nom=None):
        type_id = None
        if type_nom:
            t = TypeEtablissement.objects.filter(nom__iexact=type_nom).first()
            if not t:
                raise CommandError("Type inconnu : " + ", ".join(TypeEtablissement.objects.values_list("nom", flat=True)))
            type_id = t.id
        etab = services.creer_etablissement(nom, type_id)
        self.stdout.write(self.style.SUCCESS(
            f"« {etab.nom} » créé : adresse /{etab.slug}, code d'activation {etab.code_activation} (7 jours)."
        ))

    def creer_admin(self, identifiant, nom, adresse):
        etab = Etablissement.objects.filter(slug=adresse.strip().lower()).first()
        if not etab:
            raise CommandError("Adresse d'établissement inconnue.")
        mot_de_passe = getpass.getpass("Mot de passe : ")
        identifiant, nom = services._verifier_nouveau_compte(identifiant, nom, mot_de_passe)
        Compte.objects.create_user(identifiant, mot_de_passe, etablissement=etab, nom=nom, role="admin", statut="actif")
        if not etab.active_le:
            etab.active_le, etab.code_activation, etab.code_activation_expire = maintenant(), None, None
            etab.save(update_fields=["active_le", "code_activation", "code_activation_expire"])
        self.stdout.write(self.style.SUCCESS(f"Administrateur {identifiant} créé dans {etab.nom}."))

    def reinitialiser(self, identifiant):
        compte = Compte.objects.filter(identifiant=identifiant.strip().lower()).first()
        if not compte:
            raise CommandError("Compte introuvable.")
        provisoire = mot_de_passe_temporaire()
        compte.set_password(provisoire)
        compte.doit_changer, compte.echecs, compte.bloque_jusqua, compte.statut = True, 0, None, "actif"
        compte.save()
        services.supprimer_sessions_compte(compte.id)
        self.stdout.write(self.style.SUCCESS(f"Mot de passe provisoire de {compte.identifiant} : {provisoire}"))
