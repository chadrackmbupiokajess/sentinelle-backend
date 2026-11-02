"""
Temps réel de Sentinelle, par établissement.

Deux façons de recevoir les mises à jour, avec exactement les mêmes messages :

  • AJAX (PythonAnywhere, ou secours) : l'écran interroge GET /api/temps-reel/…
    toutes les quelques secondes. Les événements adressés aux soignants sont
    gardés quelques minutes dans un journal (table EvenementTempsReel) ; l'état
    complet est recalculé à chaque requête.
  • WebSocket (VPS, Django Channels, SENTINELLE_WEBSOCKET=1) : les écrans sont
    abonnés à des groupes, et reçoivent les mêmes messages immédiatement.

Tout est en base (pas en mémoire) : fonctionne avec plusieurs processus web.
"""

from __future__ import annotations

import json
import logging
import time

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from . import services
from .models import Chambre, Compte, EvenementTempsReel, Presence
from .outils import maintenant

logger = logging.getLogger("sentinelle")

CONSERVATION_S = 600  # durée de conservation du journal (secondes)
LIMITE_EVENEMENTS = 200  # événements renvoyés au maximum par requête


# ==========================================================================
# Groupes WebSocket (Django Channels) — inactifs sans SENTINELLE_WEBSOCKET
# ==========================================================================
def groupe_soignants(etab_id: int) -> str:
    return f"etab{etab_id}.soignants"


def groupe_bornes(etab_id: int) -> str:
    return f"etab{etab_id}.bornes"


def groupe_compte(compte_id: int) -> str:
    return f"compte{compte_id}"


def _envoyer_groupe(groupe: str, message: dict) -> None:
    if not settings.SENTINELLE_WEBSOCKET:
        return
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        couche = get_channel_layer()
        if couche is not None:
            async_to_sync(couche.group_send)(groupe, message)
    except Exception:  # le temps réel ne doit jamais faire échouer une action
        logger.exception("Diffusion WebSocket impossible vers %s", groupe)


# ==========================================================================
# Journal (AJAX)
# ==========================================================================
def _journaliser(etab_id: int, donnees: dict, compte_id: int | None = None) -> None:
    t = time.time()
    EvenementTempsReel.objects.create(
        etablissement_id=etab_id,
        compte_id=compte_id,
        donnees=json.dumps(donnees, ensure_ascii=False),
        cree_le=t,
    )
    EvenementTempsReel.objects.filter(cree_le__lt=t - CONSERVATION_S).delete()


def dernier_id() -> int:
    dernier = EvenementTempsReel.objects.order_by("-id").values_list("id", flat=True).first()
    return dernier or 0


def evenements_depuis(curseur: int, compte: Compte) -> tuple[list[dict], int]:
    """Événements destinés à ce compte, postérieurs au curseur. Renvoie (événements, nouveau curseur)."""
    dernier = dernier_id()
    if curseur > dernier:  # base réinitialisée : on repart du présent
        return [], dernier
    lignes = list(
        EvenementTempsReel.objects.filter(
            Q(compte__isnull=True) | Q(compte_id=compte.id),
            id__gt=curseur,
            id__lte=dernier,
            etablissement_id=compte.etablissement_id,
        )
        .order_by("id")
        .values_list("id", "donnees")[:LIMITE_EVENEMENTS]
    )
    if len(lignes) == LIMITE_EVENEMENTS:
        dernier = lignes[-1][0]
    return [json.loads(d) for _, d in lignes], dernier


# ==========================================================================
# Présence des soignants
# ==========================================================================
def marquer_present(compte: Compte) -> bool:
    """Enregistre le passage du soignant. Renvoie True s'il n'était pas déjà en ligne."""
    t = time.time()
    with transaction.atomic():
        ancien = Presence.objects.filter(compte=compte).values_list("vu_le", flat=True).first()
        Presence.objects.update_or_create(compte=compte, defaults={"vu_le": t})
    return ancien is None or t - ancien > settings.SENTINELLE_PRESENCE_S


def retirer(compte_id: int) -> None:
    Presence.objects.filter(compte_id=compte_id).delete()


def expirer_bientot(compte_id: int) -> None:
    """Fermeture d'un WebSocket : le compte reste « en ligne » 25 s, le temps qu'un autre
    de ses appareils encore connecté se signale (ping toutes les 20 s)."""
    Presence.objects.filter(compte_id=compte_id).update(
        vu_le=time.time() - settings.SENTINELLE_PRESENCE_S + 25
    )


def comptes_en_ligne(etab_id: int) -> dict[int, dict]:
    limite = time.time() - settings.SENTINELLE_PRESENCE_S
    lignes = Compte.objects.filter(
        etablissement_id=etab_id, statut="actif", presence__vu_le__gte=limite
    ).values("id", "nom", "role")
    return {c["id"]: c for c in lignes}


def compte_en_ligne(compte: Compte) -> bool:
    return compte.id in comptes_en_ligne(compte.etablissement_id)


# ==========================================================================
# États envoyés aux écrans
# ==========================================================================
def etat_soignants(compte: Compte) -> dict:
    etab = compte.etablissement
    en_ligne = comptes_en_ligne(etab.id)
    etat = {
        "type": "etat",
        "alertes": services.alertes_visibles(etab),
        "chambres": services.lister_chambres(etab, avec_sejours=True),
        "equipe": sorted({c["nom"] for c in en_ligne.values()}, key=str.lower),
        "contacts_medicaux": [
            {**contact, "en_ligne": contact["id"] in en_ligne} for contact in services.contacts_medicaux(etab)
        ],
        "stats": services.statistiques(etab),
        "heure_serveur": maintenant().isoformat(timespec="milliseconds"),
        "urgences_privees": services.urgences_actives(compte.id),
    }
    if compte.role == "admin":
        etat["demandes_en_attente"] = services.demandes_en_attente(etab)
    return etat


def etat_borne(chambre: Chambre, jeton_sejour: str | None) -> dict:
    statut, sejour = services.appareil(chambre, jeton_sejour)
    return {
        "type": "borne",
        "chambre": services.chambre_dict(chambre),
        "etablissement": services.etablissement_public(chambre.etablissement),
        "statut": statut,
        "lit": sejour.lit if sejour else None,
        "alerte": services.alerte_active_sejour(sejour.id) if sejour else None,
        "heure_serveur": maintenant().isoformat(timespec="milliseconds"),
    }


def messages_ouverture(compte: Compte) -> list[dict]:
    """Messages envoyés à l'ouverture d'une connexion (WebSocket ou première requête AJAX)."""
    return [
        {"type": "moi", "compte": services.compte_public(compte)},
        {"type": "messages", "messages": services.messages(compte.etablissement)},
        *(
            {"type": "message_prive", "message": message, "urgence": False, "silencieux": True}
            for message in services.appels_manques_non_lus(compte.id)
        ),
    ]


# ==========================================================================
# Diffusion (appelée par les vues après chaque changement)
# ==========================================================================
def publier(etab_id: int) -> None:
    """L'état a changé : les écrans WebSocket le redemandent (en AJAX, il revient à chaque requête)."""
    _envoyer_groupe(groupe_soignants(etab_id), {"type": "publier"})
    _envoyer_groupe(groupe_bornes(etab_id), {"type": "publier"})


def aux_soignants(etab_id: int, donnees: dict) -> None:
    _journaliser(etab_id, donnees)
    _envoyer_groupe(groupe_soignants(etab_id), {"type": "transmettre", "donnees": donnees})


def a_un_compte(etab_id: int, compte_id: int, donnees: dict) -> None:
    _journaliser(etab_id, donnees, compte_id)
    _envoyer_groupe(groupe_compte(compte_id), {"type": "transmettre", "donnees": donnees})


def fermer_compte(etab_id: int, compte_id: int) -> None:
    """Déconnecte immédiatement tous les écrans d'un compte (désactivé, rôle modifié…)."""
    _journaliser(etab_id, {"type": "_fermeture", "code": 4401}, compte_id)
    retirer(compte_id)
    _envoyer_groupe(groupe_compte(compte_id), {"type": "fermer", "code": 4401})


def fermer_bornes(etab_id: int, numero: str) -> None:
    """Après un changement de code : les anciens liens de cette chambre cessent de fonctionner."""
    _envoyer_groupe(groupe_bornes(etab_id), {"type": "fermer_chambre", "numero": numero})


# ==========================================================================
# Sorties automatiques (date de sortie prévue atteinte)
# ==========================================================================
_derniere_verification = 0.0


def verifier_sorties_automatiques() -> None:
    for s in services.cloturer_sejours_expires():
        publier(s["etablissement_id"])  # les téléphones concernés passent à « Séjour terminé »
        aux_soignants(
            s["etablissement_id"],
            {"type": "info", "texte": f"Départ automatique : {s['chambre_libelle']} · {s['place']} {s['lit']}."},
        )


def verifier_sorties_si_necessaire() -> None:
    """Appelée par les requêtes AJAX et les pings WebSocket : au plus une fois toutes les 30 s.
    Aucune tâche de fond n'est donc nécessaire (PythonAnywhere n'en garde pas)."""
    global _derniere_verification
    t = time.monotonic()
    if t - _derniere_verification < 30:
        return
    _derniere_verification = t
    try:
        verifier_sorties_automatiques()
    except Exception:  # ne jamais faire échouer la requête pour autant
        logger.exception("Sorties automatiques")

