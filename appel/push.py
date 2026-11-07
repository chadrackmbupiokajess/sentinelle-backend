"""Notifications push (Web Push, clés VAPID) vers les téléphones des soignants."""

from __future__ import annotations

import json
import logging
import threading

from django.conf import settings

from . import services

logger = logging.getLogger("sentinelle")


def _cle_privee_valide() -> bool:
    cle = settings.SENTINELLE_VAPID_PRIVATE_KEY
    if not cle:
        return False
    try:
        from py_vapid import Vapid

        Vapid.from_string(cle)
        return True
    except Exception:
        logger.error("La clé privée VAPID n'est pas valide.")
        return False


def disponible() -> bool:
    return bool(
        settings.SENTINELLE_VAPID_PUBLIC_KEY and settings.SENTINELLE_VAPID_SUBJECT and _cle_privee_valide()
    )


def cle_publique() -> str | None:
    return settings.SENTINELLE_VAPID_PUBLIC_KEY if disponible() else None


def envoyer_abonnement(abonnement: dict, donnees: dict) -> tuple[bool, str]:
    """Envoie une notification à un téléphone. Renvoie (réussi, explication en cas d'échec)."""
    if not disponible():
        logger.warning("Push indisponible : clés VAPID non configurées.")
        return False, "Clés VAPID non configurées sur le serveur."
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": abonnement["endpoint"],
                "keys": {"p256dh": abonnement["p256dh"], "auth": abonnement["auth"]},
            },
            data=json.dumps(donnees, ensure_ascii=False),
            vapid_private_key=settings.SENTINELLE_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.SENTINELLE_VAPID_SUBJECT},
            ttl=86400 if donnees.get("type") in ("urgence", "urgence_terminee", "appel_termine") else 600,
            timeout=6,
        )
        return True, ""
    except WebPushException as exc:
        statut = exc.response.status_code if exc.response is not None else None
        detail = (exc.response.text[:200] if exc.response is not None else str(exc)).strip()
        logger.warning("Échec push vers %s (HTTP %s) : %s", _service(abonnement), statut, detail)
        if statut in (404, 410):
            services.supprimer_abonnement_push(abonnement["compte_id"], abonnement["endpoint"])
            return False, "Abonnement expiré sur ce téléphone : réactivez la cloche."
        return False, f"Refusé par {_service(abonnement)} (HTTP {statut}) : {detail}"
    except Exception as exc:  # réseau bloqué (compte gratuit PythonAnywhere…)
        logger.warning("Échec push vers %s : %s", _service(abonnement), exc)
        return False, f"Serveur de notifications injoignable ({_service(abonnement)}) : {exc}"


def _service(abonnement: dict) -> str:
    """Nom du serveur de notifications du téléphone (Google, Apple, Mozilla…), pour les messages."""
    from urllib.parse import urlparse

    return urlparse(abonnement["endpoint"]).hostname or "?"


def tester_compte(compte_id: int, vocabulaire: dict) -> dict:
    """Bouton « Tester » : notification d'essai, envoyée tout de suite, avec le résultat par téléphone."""
    abonnements = services.abonnements_push_compte(compte_id)
    resultats = []
    for abonnement in abonnements:
        reussi, detail = envoyer_abonnement(
            {**abonnement, "compte_id": compte_id},
            {
                "type": "test",
                "tag": "jessna-test",
                "titre": "🔔 Notification d'essai",
                "corps": f"Les appels arriveront ainsi, même application fermée. ({vocabulaire['personnel']} prévenu(e).)",
            },
        )
        resultats.append({"service": _service(abonnement), "ok": reussi, "erreur": detail or None})
    return {"abonnements": len(abonnements), "envoyes": sum(r["ok"] for r in resultats), "resultats": resultats}


def envoyer_compte(compte_id: int, donnees: dict) -> None:
    for abonnement in services.abonnements_push_compte(compte_id):
        envoyer_abonnement({**abonnement, "compte_id": compte_id}, donnees)


def envoyer_etablissement(etablissement_id: int, donnees: dict) -> None:
    """Tout le personnel actif de l'établissement (téléphones abonnés).
    Envoi direct par défaut : PythonAnywhere désactive les fils d'exécution des applications web
    (un fil n'y démarrerait jamais, sans aucune erreur). Sur le VPS, SENTINELLE_PUSH_ARRIERE_PLAN=1
    envoie en arrière-plan pour que la borne n'attende pas."""
    if not disponible():
        return
    abonnements = services.abonnements_push_etablissement(etablissement_id)
    if not abonnements:
        return

    def envoyer():
        for abonnement in abonnements:
            envoyer_abonnement(abonnement, donnees)

    if not getattr(settings, "SENTINELLE_PUSH_ARRIERE_PLAN", False):
        envoyer()
        return

    def envoyer_en_arriere_plan():
        try:
            envoyer()
        finally:
            from django.db import connection

            connection.close()  # connexion ouverte par ce fil (suppression d'un abonnement expiré)

    try:
        threading.Thread(target=envoyer_en_arriere_plan, name="push-appel", daemon=True).start()
    except RuntimeError:  # fils d'exécution indisponibles : envoi direct
        envoyer()


def donnees_appel(alerte: dict, slug: str, v: dict) -> dict:
    """Appel d'un patient (ou client) : la notification reste jusqu'à la prise en charge."""
    lieu = alerte["chambre_libelle"]
    place = f" · {v['place']} {alerte['lit']}" if alerte.get("lit") else ""
    qui = f" ({alerte['patient_initiales']})" if alerte.get("patient_initiales") else ""
    return {
        "type": "appel",
        "tag": f"appel-{alerte['id']}",
        "titre": f"🔔 {lieu}{place}",
        "corps": f"{v['beneficiaire']}{qui} appelle. Ouvrez Jessna pour accuser réception.",
        "url": f"/{slug}",
        "alerte_id": alerte["id"],
    }


def fin_appel(alerte_id: str) -> dict:
    """Ferme la notification de l'appel sur tous les téléphones (pris en charge, terminé ou annulé)."""
    return {"type": "appel_termine", "tag": f"appel-{alerte_id}"}


def donnees_urgence(message_id: int, expediteur_nom: str, expediteur_role: str, v: dict) -> dict:
    role = v["role_infirmier"] if expediteur_role == "infirmier" else v["role_medecin"]
    return {
        "type": "urgence",
        "tag": f"urgence-privee-{message_id}",
        "titre": f"🚨 Urgence — {expediteur_nom} ({role})",
        "corps": "Vous êtes demandé(e). Ouvrez Jessna puis appuyez sur « J'arrive ».",
        "url": "/infirmier",
        "message_id": message_id,
    }
