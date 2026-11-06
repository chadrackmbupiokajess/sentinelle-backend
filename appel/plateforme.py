"""
Espace du propriétaire de la plateforme (SaaS) : vue d'ensemble des établissements.

Réservé aux comptes « superutilisateur » (python manage.py createsuperuser), qui n'appartiennent
à aucun établissement : leur jeton ne donne pas accès aux données d'un hôpital, seulement à
ces statistiques et aux actions de gestion (créer, renommer, suspendre, dépanner un compte).
"""

from __future__ import annotations

import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q, Sum

from . import services
from . import vocabulaire
from .models import Alerte, Chambre, Compte, Etablissement, Presence, Sejour, SessionSoignant, TypeEtablissement
from .outils import ErreurMetier, empreinte_jeton, iso, maintenant

STATUTS_ACTIFS = Alerte.STATUTS_ACTIFS


# ----------------------------------------------------------------- comptes propriétaires
def proprietaire_public(c: Compte) -> dict:
    return {"id": c.id, "identifiant": c.identifiant, "nom": c.nom}


def authentifier(identifiant: str, mot_de_passe: str) -> Compte:
    compte = Compte.objects.filter(identifiant=identifiant.strip().lower(), is_superuser=True).first()
    if compte and compte.bloque_jusqua and compte.bloque_jusqua > maintenant():
        raise ErreurMetier("Trop d'essais. Réessayez dans quelques minutes.", 429)
    if not compte or not compte.check_password(mot_de_passe) or compte.statut != "actif":
        if compte:
            echecs = compte.echecs + 1
            if echecs >= services.ECHECS_AVANT_BLOCAGE:
                Compte.objects.filter(id=compte.id).update(
                    echecs=0, bloque_jusqua=maintenant() + services.DUREE_BLOCAGE
                )
            else:
                Compte.objects.filter(id=compte.id).update(echecs=echecs)
        else:
            Compte().set_password(mot_de_passe)  # même durée que l'identifiant existe ou non
        raise ErreurMetier("Identifiant ou mot de passe incorrect.", 401)
    Compte.objects.filter(id=compte.id).update(echecs=0, bloque_jusqua=None)
    return compte


def proprietaire_par_jeton(jeton: str | None) -> Compte | None:
    if not jeton:
        return None
    session = (
        SessionSoignant.objects.select_related("compte").filter(empreinte=empreinte_jeton(jeton)).first()
    )
    if not session or session.expire_le < maintenant():
        return None
    c = session.compte
    return c if c.is_superuser and c.statut == "actif" else None


# ----------------------------------------------------------------- statistiques
def _par_etablissement(qs, champ: str, **agregats) -> dict[int, dict]:
    return {ligne.pop(champ): ligne for ligne in qs.values(champ).annotate(**agregats)}


def lister_etablissements() -> dict:
    t = maintenant()
    depuis = t - timedelta(hours=24)
    etabs = Etablissement.objects.select_related("type").annotate(
        comptes_actifs=Count("comptes", filter=Q(comptes__statut="actif"), distinct=True),
        demandes=Count("comptes", filter=Q(comptes__statut="en_attente"), distinct=True),
        administrateurs=Count("comptes", filter=Q(comptes__statut="actif", comptes__role="admin"), distinct=True),
    ).order_by("-actif", "nom")

    chambres = _par_etablissement(Chambre.objects.all(), "etablissement", chambres=Count("id"), lits=Sum("lits"))
    occupes = _par_etablissement(
        Sejour.objects.filter(sortie_le__isnull=True, expire_le__gt=t), "chambre__etablissement", n=Count("id")
    )
    appels = _par_etablissement(
        Alerte.objects.filter(cree_le__gte=depuis).exclude(statut="annulee"), "chambre__etablissement", n=Count("id")
    )
    en_cours = _par_etablissement(
        Alerte.objects.filter(statut__in=STATUTS_ACTIFS), "chambre__etablissement", n=Count("id")
    )
    dernier_appel = _par_etablissement(Alerte.objects.all(), "chambre__etablissement", d=Max("cree_le"))
    en_ligne = _par_etablissement(
        Presence.objects.filter(vu_le__gte=time.time() - settings.SENTINELLE_PRESENCE_S, compte__statut="actif"),
        "compte__etablissement",
        n=Count("compte"),
    )
    delais: dict[int, list[float]] = {}
    for etab_id, cree, accuse in Alerte.objects.filter(
        cree_le__gte=depuis, accuse_le__isnull=False
    ).values_list("chambre__etablissement", "cree_le", "accuse_le"):
        delais.setdefault(etab_id, []).append((accuse - cree).total_seconds())

    liste = []
    for e in etabs:
        d = delais.get(e.id)
        liste.append(
            {
                "id": e.id,
                "nom": e.nom,
                "slug": e.slug,
                "type": {"id": e.type_id, "nom": e.type.nom},
                **activation(e),
                "actif": e.actif,
                "logo": f"/api/etablissements/{e.slug}/logo?v={e.logo_version}" if e.a_logo else None,
                "cree_le": iso(e.cree_le),
                "comptes_actifs": e.comptes_actifs,
                "demandes": e.demandes,
                "administrateurs": e.administrateurs,
                "chambres": chambres.get(e.id, {}).get("chambres", 0),
                "lits": chambres.get(e.id, {}).get("lits") or 0,
                "lits_occupes": occupes.get(e.id, {}).get("n", 0),
                "appels_24h": appels.get(e.id, {}).get("n", 0),
                "appels_en_cours": en_cours.get(e.id, {}).get("n", 0),
                "delai_moyen_s": round(sum(d) / len(d)) if d else None,
                "en_ligne": en_ligne.get(e.id, {}).get("n", 0),
                "dernier_appel": iso(dernier_appel.get(e.id, {}).get("d")),
            }
        )
    actifs = [x for x in liste if x["actif"]]
    return {
        "resume": {
            "etablissements": len(actifs),
            "suspendus": len(liste) - len(actifs),
            "soignants": sum(x["comptes_actifs"] for x in actifs),
            "en_ligne": sum(x["en_ligne"] for x in actifs),
            "appels_24h": sum(x["appels_24h"] for x in actifs),
            "appels_en_cours": sum(x["appels_en_cours"] for x in actifs),
        },
        "etablissements": liste,
    }


def _etablissement(etab_id: int) -> Etablissement:
    e = Etablissement.objects.select_related("type").filter(id=etab_id).first()
    if not e:
        raise ErreurMetier("Établissement introuvable.", 404)
    return e


def activation(e: Etablissement) -> dict:
    """État du code d'activation : « active » (compte administrateur créé), « en_attente » ou « expire »."""
    if e.active_le:
        etat = "active"
    elif e.code_activation_expire and e.code_activation_expire > maintenant():
        etat = "en_attente"
    else:
        etat = "expire"
    return {
        "activation": etat,
        "code_activation": e.code_activation if etat == "en_attente" else None,
        "code_activation_expire": iso(e.code_activation_expire) if etat == "en_attente" else None,
        "active_le": iso(e.active_le),
    }


def detail(etab_id: int) -> dict:
    e = _etablissement(etab_id)
    return {
        **services.etablissement_public(e),
        **activation(e),
        "type": {"id": e.type_id, "nom": e.type.nom},
        "comptes": services.lister_comptes(e),
    }


def creer(nom: str, type_id: int | None) -> dict:
    e = services.creer_etablissement(nom, type_id)
    return {"id": e.id, "nom": e.nom, "slug": e.slug, "type": {"id": e.type_id, "nom": e.type.nom}, **activation(e)}


def nouveau_code_activation(etab_id: int) -> dict:
    e = services.nouveau_code_activation(_etablissement(etab_id))
    return {"id": e.id, "nom": e.nom, "slug": e.slug, **activation(e)}


def modifier(etab_id: int, nom: str | None, actif: bool | None, type_id: int | None = None) -> Etablissement:
    e = _etablissement(etab_id)
    if nom is not None:
        services.renommer_etablissement(e, nom)
    if type_id is not None and type_id != e.type_id:
        e.type = _type(type_id)
        e.save(update_fields=["type"])
    if actif is not None and actif != e.actif:
        e.actif = actif
        e.save(update_fields=["actif"])
    return e


def reinitialiser_compte(etab_id: int, compte_id: int) -> dict:
    """Support : mot de passe provisoire pour un compte d'un établissement (ex. administrateur bloqué)."""
    e = _etablissement(etab_id)
    compte = services._exiger_compte(e, compte_id)
    provisoire = services.reinitialiser_mot_de_passe(e, compte_id)
    return {"mot_de_passe": provisoire, "identifiant": compte.identifiant, "nom": compte.nom}


# ----------------------------------------------------------------- types d'établissement
def _type(type_id: int) -> TypeEtablissement:
    t = TypeEtablissement.objects.filter(id=type_id).first()
    if not t:
        raise ErreurMetier("Type d'établissement introuvable.", 404)
    return t


def type_public(t: TypeEtablissement, nb: int | None = None) -> dict:
    return {
        "id": t.id,
        "nom": t.nom,
        "vocabulaire": vocabulaire.complet(t.vocabulaire),
        "etablissements": nb if nb is not None else t.etablissements.count(),
    }


def lister_types() -> dict:
    types = TypeEtablissement.objects.annotate(nb=Count("etablissements"))
    return {
        "cles": [{"cle": cle, "libelle": libelle, "defaut": defaut} for cle, (libelle, defaut) in vocabulaire.CLES.items()],
        "types": [type_public(t, t.nb) for t in types],
    }


def _nom_type(nom: str, sauf_id: int | None = None) -> str:
    nom = " ".join((nom or "").split())[:60]
    if len(nom) < 2:
        raise ErreurMetier("Indiquez le nom du type (ex. Hôpital, Restaurant).")
    if TypeEtablissement.objects.filter(nom__iexact=nom).exclude(id=sauf_id).exists():
        raise ErreurMetier("Ce type existe déjà.", 409)
    return nom


def creer_type(nom: str, vocab: dict | None) -> dict:
    t = TypeEtablissement.objects.create(nom=_nom_type(nom), vocabulaire=vocabulaire.nettoyer(vocab or {}))
    return type_public(t, 0)


def modifier_type(type_id: int, nom: str | None, vocab: dict | None) -> dict:
    t = _type(type_id)
    if nom is not None:
        t.nom = _nom_type(nom, t.id)
    if vocab is not None:
        t.vocabulaire = vocabulaire.nettoyer(vocab)
    t.save()
    return type_public(t)


def supprimer_type(type_id: int) -> None:
    t = _type(type_id)
    if t.etablissements.exists():
        raise ErreurMetier("Impossible : des établissements utilisent ce type.", 409)
    t.delete()
