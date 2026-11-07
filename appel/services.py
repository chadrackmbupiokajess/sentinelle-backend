"""
Logique métier de Sentinelle (portée depuis l'ancien backend FastAPI, base.py).

Toutes les fonctions travaillent DANS un établissement : un soignant ne voit et ne
modifie que les données du sien. Les dictionnaires renvoyés ont exactement la forme
attendue par le frontend.
"""

from __future__ import annotations

import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils.text import slugify

from .models import (
    AbonnementPush,
    Alerte,
    AppareilPatient,
    Chambre,
    Compte,
    Etablissement,
    LogoEtablissement,
    Message,
    MessagePrive,
    Sejour,
    SessionSoignant,
    TypeEtablissement,
    Urgence,
)
from .vocabulaire import maj
from .outils import (
    ErreurMetier,
    empreinte_jeton,
    iso,
    maintenant,
    mot_de_passe_temporaire,
    mot_de_passe_valide,
    nouveau_code,
    nouveau_jeton,
)

DUREE_ACTIVATION = timedelta(days=7)
# Adresses réservées aux pages de l'application (localhost:3000/<adresse>).
ADRESSES_RESERVEES = {
    "admin", "plateforme", "infirmier", "c", "chambre", "api", "gestion", "static", "ws",
    "_next", "e", "aide", "connexion", "inscription", "compte", "robots-txt", "favicon-ico",
    "robotstxt", "faviconico", "icones", "horsligne",
}

STATUTS_ACTIFS = Alerte.STATUTS_ACTIFS
RE_IDENTIFIANT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
LITS_MAX = 40
ECHECS_AVANT_BLOCAGE = 5
DUREE_BLOCAGE = timedelta(minutes=5)
ESSAIS_CODE_SALLE_MAX = 25  # codes de séjour faux tolérés par salle avant blocage
SORTIE_PREVUE_MAX = timedelta(days=365)
EXPIRATION_PERMANENTE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

def duree_sejour() -> timedelta:
    return timedelta(days=settings.SENTINELLE_SEJOUR_JOURS)


def expiration_session(t: datetime) -> datetime:
    jours = settings.SENTINELLE_SESSION_JOURS
    return t + timedelta(days=jours) if jours > 0 else EXPIRATION_PERMANENTE


# ==========================================================================
# Représentations JSON (identiques à l'ancienne API)
# ==========================================================================
def compte_public(c: Compte) -> dict:
    return {
        "id": c.id,
        "identifiant": c.identifiant,
        "nom": c.nom,
        "role": c.role,
        "statut": c.statut,
        "cree_le": iso(c.cree_le),
        "doit_changer": bool(c.doit_changer),
        "etablissement": etablissement_public(c.etablissement) if c.etablissement_id else None,
    }


def etablissement_public(e: Etablissement) -> dict:
    """Ce que voient les écrans : nom, adresse, type, vocabulaire et logo."""
    base = f"/api/etablissements/{e.slug}"
    return {
        "id": e.id,
        "nom": e.nom,
        "slug": e.slug,
        "type": e.type.nom,
        "vocabulaire": e.vocabulaire,
        "actif": e.actif,
        # Adresses relatives au backend ; « ?v= » change à chaque nouveau logo.
        "logo": f"{base}/logo?v={e.logo_version}" if e.a_logo else None,
        "icone": f"{base}/icone?v={e.logo_version}" if e.a_logo else None,
    }


def chambre_dict(ch: Chambre, avec_code: bool = False, complete: bool = False) -> dict:
    d = {"id": ch.numero, "libelle": ch.libelle, "service": ch.service, "lits": ch.lits}
    if avec_code or complete:
        d["code"] = ch.code
    if complete:
        d["essais_sejour"] = ch.essais_sejour
    return d


def sejour_public(s: Sejour | None) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "chambre_id": s.chambre.numero,
        "lit": s.lit,
        "code": s.code,
        "initiales": s.initiales,
        "admis_le": iso(s.admis_le),
        "admis_par": s.admis_par,
        "expire_le": iso(s.expire_le),
        "sortie_prevue": iso(s.sortie_prevue),
    }


def alerte_dict(a: Alerte | None) -> dict | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "chambre_id": a.chambre.numero,
        "statut": a.statut,
        "cree_le": iso(a.cree_le),
        "accuse_le": iso(a.accuse_le),
        "accuse_par": a.accuse_par,
        "termine_le": iso(a.termine_le),
        "termine_par": a.termine_par,
        "annule_le": iso(a.annule_le),
        "lit": a.lit,
        "sejour_id": a.sejour_id,
        "chambre_libelle": a.chambre.libelle,
        "chambre_service": a.chambre.service,
        "patient_initiales": a.sejour.initiales if a.sejour_id and a.sejour else "",
    }


def message_dict(m: Message) -> dict:
    return {"id": m.id, "auteur": m.auteur, "texte": m.texte, "cree_le": iso(m.cree_le)}


def message_prive_dict(m: MessagePrive) -> dict:
    return {
        "id": m.id,
        "expediteur_id": m.expediteur_id,
        "expediteur_nom": m.expediteur.nom,
        "destinataire_id": m.destinataire_id,
        "texte": m.texte,
        "cree_le": iso(m.cree_le),
        "lu_le": iso(m.lu_le),
        "type": m.type,
        "audio_type": m.audio_type,
    }


def _alertes():
    return Alerte.objects.select_related("chambre", "sejour")


def _messages_prives():
    return MessagePrive.objects.select_related("expediteur").defer("audio")


# ==========================================================================
# Établissements
# ==========================================================================
def _code_chambre_libre(numero: str) -> str:
    """Code de porte : le lien « numéro-code » doit désigner une seule chambre, tous établissements confondus."""
    while True:
        code = nouveau_code(4)
        if not Chambre.objects.filter(numero=numero, code=code).exists():
            return code


def _adresse_libre(nom: str, sauf_id: int | None = None) -> str:
    """Adresse collée, sans tiret : « Jessna Hôpital » → « jessnahopital » (puis jessnahopital2…)."""
    base = slugify(nom).replace("-", "")[:50] or "etablissement"
    adresse, n = base, 2
    while adresse in ADRESSES_RESERVEES or Etablissement.objects.filter(slug=adresse).exclude(id=sauf_id).exists():
        adresse, n = f"{base}{n}", n + 1
    return adresse


def _code_activation_libre() -> str:
    while True:
        code = f"{nouveau_code(4)}-{nouveau_code(4)}"
        if not Etablissement.objects.filter(code_activation=code).exists():
            return code


def _type(type_id: int | None) -> TypeEtablissement:
    t = TypeEtablissement.objects.filter(id=type_id).first() if type_id else None
    t = t or TypeEtablissement.objects.filter(nom="Hôpital").first() or TypeEtablissement.objects.first()
    if not t:
        raise ErreurMetier("Créez d'abord un type d'établissement.", 400)
    return t


def creer_etablissement(nom: str, type_id: int | None = None) -> Etablissement:
    """Nouvel établissement (par le propriétaire) : adresse et code d'activation (7 jours). Vide au départ."""
    nom = " ".join(nom.split())
    if len(nom) < 2:
        raise ErreurMetier("Indiquez le nom de l'établissement.")
    t = maintenant()
    etab = Etablissement.objects.create(
        nom=nom[:80],
        type=_type(type_id),
        slug=_adresse_libre(nom),
        code_activation=_code_activation_libre(),
        code_activation_expire=t + DUREE_ACTIVATION,
    )
    # Aucun lieu au départ : l'établissement crée lui-même ses chambres, tables… et leurs places.
    return etab


def nouveau_code_activation(etab: Etablissement) -> Etablissement:
    if etab.active_le:
        raise ErreurMetier("Cet établissement est déjà activé.", 409)
    etab.code_activation = _code_activation_libre()
    etab.code_activation_expire = maintenant() + DUREE_ACTIVATION
    etab.save(update_fields=["code_activation", "code_activation_expire"])
    return etab


def etablissement_par_activation(code: str) -> Etablissement:
    """Code d'activation valable (existe, pas utilisé, pas expiré), sinon « Code invalide »."""
    code = (code or "").strip().upper().replace(" ", "")
    if len(code) == 8 and "-" not in code:
        code = f"{code[:4]}-{code[4:]}"
    etab = Etablissement.objects.select_related("type").filter(code_activation=code, actif=True).first() if code else None
    if not etab or etab.active_le or not etab.code_activation_expire or etab.code_activation_expire < maintenant():
        raise ErreurMetier("Code invalide.", 404)
    return etab


def etablissement_par_adresse(slug: str) -> Etablissement:
    etab = Etablissement.objects.select_related("type").filter(slug=(slug or "").strip().lower()).first()
    if not etab or not etab.actif:
        raise ErreurMetier("Établissement introuvable.", 404)
    return etab


# ----------------------------------------------------------------- logo
TAILLE_MAX_LOGO = 1024 * 1024  # 1 Mo par image (le navigateur les réduit à 512 px avant l'envoi)
SIGNATURES_IMAGES = {
    "image/png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}


def _verifier_image(donnees: bytes, types: tuple[str, ...], quoi: str) -> str:
    """Type réel de l'image (d'après son contenu, pas son nom). SVG refusé : il peut contenir du code."""
    if not donnees:
        raise ErreurMetier(f"{quoi} : image vide.")
    if len(donnees) > TAILLE_MAX_LOGO:
        raise ErreurMetier(f"{quoi} : image trop lourde (1 Mo au maximum).", 413)
    for t in types:
        if SIGNATURES_IMAGES[t](donnees):
            return t
    raise ErreurMetier(f"{quoi} : format non pris en charge (PNG, JPEG ou WebP).", 415)


def changer_logo(etab: Etablissement, image: bytes, icone: bytes, icone_masquable: bytes) -> dict:
    type_image = _verifier_image(image, ("image/png", "image/jpeg", "image/webp"), "Logo")
    _verifier_image(icone, ("image/png",), "Icône")
    _verifier_image(icone_masquable, ("image/png",), "Icône")
    with transaction.atomic():
        LogoEtablissement.objects.update_or_create(
            etablissement=etab,
            defaults={"image": image, "type": type_image, "icone": icone, "icone_masquable": icone_masquable, "modifie_le": maintenant()},
        )
        etab.a_logo, etab.logo_version = True, etab.logo_version + 1
        etab.save(update_fields=["a_logo", "logo_version"])
    return etablissement_public(etab)


def retirer_logo(etab: Etablissement) -> dict:
    with transaction.atomic():
        LogoEtablissement.objects.filter(etablissement=etab).delete()
        etab.a_logo, etab.logo_version = False, etab.logo_version + 1  # jamais deux fois la même adresse
        etab.save(update_fields=["a_logo", "logo_version"])
    return etablissement_public(etab)


def image_logo(adresse: str, quelle: str) -> tuple[bytes, str] | None:
    """quelle : « logo », « icone » ou « icone-masquable ». None si l'établissement n'a pas de logo."""
    etab = etablissement_par_adresse(adresse)
    logo = LogoEtablissement.objects.filter(etablissement=etab).first() if etab.a_logo else None
    if not logo:
        return None
    if quelle == "logo":
        return bytes(logo.image), logo.type
    return bytes(logo.icone if quelle == "icone" else logo.icone_masquable), "image/png"


def renommer_etablissement(etab: Etablissement, nom: str) -> dict:
    nom = " ".join(nom.split())
    if len(nom) < 2:
        raise ErreurMetier("Indiquez le nom de l'établissement.")
    etab.nom = nom[:80]
    etab.save(update_fields=["nom"])  # l'adresse ne change pas (déjà imprimée, partagée…)
    return etablissement_public(etab)


# ==========================================================================
# Comptes
# ==========================================================================
def premier_compte() -> bool:
    """Vrai tant qu'aucun établissement n'existe."""
    return not Etablissement.objects.exists()


def contacts_medicaux(etab: Etablissement) -> list[dict]:
    comptes = Compte.objects.filter(
        etablissement=etab, statut="actif", role__in=("infirmier", "medecin", "admin")
    ).values("id", "nom", "role")
    return [
        {
            "id": c["id"],
            "nom": c["nom"],
            "role": "medecin" if c["role"] in ("medecin", "admin") else "infirmier",
        }
        for c in sorted(comptes, key=lambda c: c["nom"].lower())
    ]


def _verifier_nouveau_compte(
    identifiant: str, nom: str, mot_de_passe: str, etab: Etablissement | None = None
) -> tuple[str, str]:
    """Format des champs ; si etab est donné, l'identifiant doit être libre DANS cet établissement."""
    identifiant = identifiant.strip().lower()
    nom = " ".join(nom.split())
    if not RE_IDENTIFIANT.match(identifiant):
        raise ErreurMetier(
            "Identifiant : 3 à 30 caractères, lettres minuscules, chiffres, point, tiret ou souligné."
        )
    if len(nom) < 2:
        raise ErreurMetier("Indiquez votre nom complet.")
    erreur = mot_de_passe_valide(mot_de_passe)
    if erreur:
        raise ErreurMetier(erreur)
    if etab is not None and Compte.objects.filter(etablissement=etab, identifiant=identifiant).exists():
        raise ErreurMetier("Cet identifiant est déjà utilisé dans cet établissement.", 409)
    return identifiant, nom


def inscrire(identifiant: str, nom: str, mot_de_passe: str, adresse: str) -> Compte:
    """Demande de compte du personnel, sur l'adresse de l'établissement : validée par un administrateur."""
    etab = etablissement_par_adresse(adresse)
    if not etab.active_le:
        raise ErreurMetier("Cet établissement n'est pas encore activé par son administrateur.", 409)
    identifiant, nom = _verifier_nouveau_compte(identifiant, nom, mot_de_passe, etab)
    try:
        with transaction.atomic():
            return Compte.objects.create_user(
                identifiant, mot_de_passe, etablissement=etab, nom=nom[:60], role="infirmier", statut="en_attente"
            )
    except IntegrityError:
        raise ErreurMetier("Cet identifiant est déjà utilisé dans cet établissement.", 409)


def activer_etablissement(code: str, identifiant: str, nom: str, mot_de_passe: str) -> Compte:
    """Le responsable utilise le code d'activation : son compte devient l'administrateur. Code consommé."""
    identifiant, nom = _verifier_nouveau_compte(identifiant, nom, mot_de_passe)
    with transaction.atomic():
        etab = etablissement_par_activation(code)
        # Usage unique, même si deux personnes valident en même temps.
        if not Etablissement.objects.filter(id=etab.id, active_le__isnull=True, code_activation=etab.code_activation).update(
            active_le=maintenant(), code_activation=None, code_activation_expire=None
        ):
            raise ErreurMetier("Code invalide.", 404)
        compte = Compte.objects.create_user(  # établissement neuf : aucun compte, identifiant forcément libre
            identifiant, mot_de_passe, etablissement=etab, nom=nom[:60], role="admin", statut="actif",
            valide_par="Code d'activation",
        )
    return Compte.objects.select_related("etablissement", "etablissement__type").get(id=compte.id)


def authentifier(identifiant: str, mot_de_passe: str, adresse: str | None = None) -> Compte:
    """Connexion. Sur la page d'un établissement (adresse), seuls ses comptes sont acceptés."""
    identifiant = identifiant.strip().lower()
    candidats = Compte.objects.select_related("etablissement", "etablissement__type").filter(identifiant=identifiant)
    if adresse:
        # Page d'un établissement : seulement ses comptes (un même identifiant peut exister ailleurs).
        candidats = candidats.filter(etablissement__slug=adresse.strip().lower())
    elif len(candidats) > 1:
        candidats = candidats.filter(etablissement__isnull=False)
        if len(candidats) > 1:
            raise ErreurMetier(
                "Connectez-vous depuis l'adresse de votre établissement (ex. …/mon-etablissement).", 409
            )
    compte = candidats.first()
    if compte and compte.bloque_jusqua and compte.bloque_jusqua > maintenant():
        raise ErreurMetier("Trop d'essais. Réessayez dans quelques minutes.", 429)
    if not compte or not compte.check_password(mot_de_passe):
        if not compte:
            Compte().set_password(mot_de_passe)  # même durée que l'identifiant existe ou non
        else:
            echecs = compte.echecs + 1
            if echecs >= ECHECS_AVANT_BLOCAGE:
                Compte.objects.filter(id=compte.id).update(
                    echecs=0, bloque_jusqua=maintenant() + DUREE_BLOCAGE
                )
            else:
                Compte.objects.filter(id=compte.id).update(echecs=echecs)
        raise ErreurMetier("Identifiant ou mot de passe incorrect.", 401)
    if compte.statut == "en_attente":
        raise ErreurMetier("Votre compte attend la validation d'un administrateur.", 403)
    if compte.statut != "actif":
        raise ErreurMetier("Ce compte est désactivé. Contactez un administrateur.", 403)
    if compte.etablissement_id is None:
        raise ErreurMetier("Ce compte sert à la gestion de la plateforme : connectez-vous sur la page /plateforme.", 403)
    if not compte.etablissement.actif:
        raise ErreurMetier("Cet établissement est suspendu. Contactez le support.", 403)
    Compte.objects.filter(id=compte.id).update(echecs=0, bloque_jusqua=None)
    return compte


# ----------------------------------------------------------------- sessions
def creer_session(compte: Compte, appareil: str = "") -> str:
    jeton = nouveau_jeton()
    t = maintenant()
    SessionSoignant.objects.create(
        empreinte=empreinte_jeton(jeton),
        compte=compte,
        cree_le=t,
        vu_le=t,
        expire_le=expiration_session(t),
        appareil=appareil[:120],
    )
    SessionSoignant.objects.filter(expire_le__lt=t).delete()  # ménage
    return jeton


def compte_par_jeton(jeton: str | None) -> Compte | None:
    """Compte actif (d'un établissement actif) lié au jeton, ou None."""
    if not jeton:
        return None
    t = maintenant()
    session = (
        SessionSoignant.objects.select_related("compte", "compte__etablissement", "compte__etablissement__type")
        .filter(empreinte=empreinte_jeton(jeton))
        .first()
    )
    if not session or session.expire_le < t:
        return None
    compte = session.compte
    if compte.statut != "actif" or not compte.etablissement_id or not compte.etablissement.actif:
        return None
    if t - session.vu_le > timedelta(hours=1):
        SessionSoignant.objects.filter(empreinte=session.empreinte).update(
            vu_le=t, expire_le=expiration_session(t)
        )
    return compte


def supprimer_session(jeton: str) -> None:
    SessionSoignant.objects.filter(empreinte=empreinte_jeton(jeton)).delete()


def supprimer_sessions_compte(compte_id: int) -> None:
    SessionSoignant.objects.filter(compte_id=compte_id).delete()


def changer_mot_de_passe(compte: Compte, actuel: str, nouveau: str) -> None:
    compte = Compte.objects.get(id=compte.id)
    if not compte.check_password(actuel):
        raise ErreurMetier("Mot de passe actuel incorrect.", 401)
    erreur = mot_de_passe_valide(nouveau)
    if erreur:
        raise ErreurMetier(erreur)
    if actuel == nouveau:
        raise ErreurMetier("Choisissez un mot de passe différent de l'actuel.")
    compte.set_password(nouveau)
    compte.doit_changer = False
    compte.save(update_fields=["password", "doit_changer"])


# ------------------------------------------------------------ notifications
def enregistrer_abonnement_push(compte: Compte, endpoint: str, p256dh: str, auth: str) -> None:
    AbonnementPush.objects.update_or_create(
        endpoint=endpoint,
        defaults={"compte": compte, "p256dh": p256dh, "auth": auth, "cree_le": maintenant()},
    )


def abonnements_push_compte(compte_id: int) -> list[dict]:
    return list(AbonnementPush.objects.filter(compte_id=compte_id).values("endpoint", "p256dh", "auth"))


def abonnements_push_etablissement(etablissement_id: int) -> list[dict]:
    """Téléphones abonnés du personnel actif de l'établissement."""
    return list(
        AbonnementPush.objects.filter(compte__etablissement_id=etablissement_id, compte__statut="actif").values(
            "endpoint", "p256dh", "auth", "compte_id"
        )
    )


def supprimer_abonnement_push(compte_id: int, endpoint: str) -> None:
    AbonnementPush.objects.filter(compte_id=compte_id, endpoint=endpoint).delete()


# ------------------------------------------------------------ administration
def lister_comptes(etab: Etablissement) -> list[dict]:
    t = maintenant()
    comptes = (
        Compte.objects.select_related("etablissement", "etablissement__type")
        .filter(etablissement=etab)
        .annotate(nb_sessions=Count("sessions", filter=Q(sessions__expire_le__gt=t)))
    )
    rang = {"en_attente": 0, "actif": 1}
    comptes = sorted(comptes, key=lambda c: (rang.get(c.statut, 2), c.nom.lower()))
    return [{**compte_public(c), "sessions": c.nb_sessions, "valide_par": c.valide_par} for c in comptes]


def demandes_en_attente(etab: Etablissement) -> int:
    return Compte.objects.filter(etablissement=etab, statut="en_attente").count()


def _exiger_compte(etab: Etablissement, compte_id: int) -> Compte:
    compte = (
        Compte.objects.select_related("etablissement", "etablissement__type")
        .filter(id=compte_id, etablissement=etab)
        .first()
    )
    if not compte:
        raise ErreurMetier("Compte introuvable.", 404)
    return compte


def _admins_actifs(etab: Etablissement) -> int:
    return Compte.objects.filter(etablissement=etab, role="admin", statut="actif").count()


def changer_statut(etab: Etablissement, compte_id: int, statut: str, par: Compte) -> dict:
    if statut not in ("actif", "refuse", "desactive"):
        raise ErreurMetier("Statut inconnu.")
    with transaction.atomic():
        compte = _exiger_compte(etab, compte_id)
        if compte.id == par.id and statut != "actif":
            raise ErreurMetier("Vous ne pouvez pas désactiver votre propre compte.", 409)
        if compte.role == "admin" and compte.statut == "actif" and statut != "actif" and _admins_actifs(etab) <= 1:
            raise ErreurMetier("Il doit rester au moins un administrateur actif.", 409)
        if compte.statut == "en_attente" and statut == "actif":
            compte.valide_par = par.nom
        compte.statut = statut
        compte.save(update_fields=["statut", "valide_par"])
        if statut != "actif":
            supprimer_sessions_compte(compte.id)
    return compte_public(compte)


def changer_role(etab: Etablissement, compte_id: int, role: str, par: Compte) -> dict:
    if role not in ("infirmier", "medecin", "admin"):
        raise ErreurMetier("Rôle inconnu.")
    with transaction.atomic():
        compte = _exiger_compte(etab, compte_id)
        if compte.role == "admin" and role != "admin" and compte.statut == "actif" and _admins_actifs(etab) <= 1:
            raise ErreurMetier("Il doit rester au moins un administrateur actif.", 409)
        if compte.id == par.id and role != "admin":
            raise ErreurMetier("Demandez à un autre administrateur de modifier votre rôle.", 409)
        compte.role = role
        compte.save(update_fields=["role"])
    return compte_public(compte)


def reinitialiser_mot_de_passe(etab: Etablissement, compte_id: int) -> str:
    """Génère un mot de passe provisoire (à changer à la prochaine connexion)."""
    provisoire = mot_de_passe_temporaire()
    with transaction.atomic():
        compte = _exiger_compte(etab, compte_id)
        compte.set_password(provisoire)
        compte.doit_changer = True
        compte.echecs = 0
        compte.bloque_jusqua = None
        compte.save(update_fields=["password", "doit_changer", "echecs", "bloque_jusqua"])
        supprimer_sessions_compte(compte.id)
    return provisoire


# ==========================================================================
# Chambres
# ==========================================================================
def _entier_en_tete(texte: str) -> int:
    """Comme CAST(… AS INTEGER) de SQLite : « 12B » → 12, « A1 » → 0."""
    m = re.match(r"\d+", texte)
    return int(m.group()) if m else 0


def _cle_tri_chambre(ch: Chambre):
    return (ch.service, _entier_en_tete(ch.numero), ch.numero)


def lister_chambres(etab: Etablissement, avec_codes: bool = False, avec_sejours: bool = False) -> list[dict]:
    chambres = sorted(Chambre.objects.filter(etablissement=etab), key=_cle_tri_chambre)
    sejours = sejours_actifs(etab) if avec_sejours else {}
    resultat = []
    for ch in chambres:
        d = chambre_dict(ch, avec_code=avec_codes)
        if avec_sejours:
            d["sejours"] = sejours.get(ch.numero, [])
        resultat.append(d)
    return resultat


def _chambre(etab: Etablissement, numero: str) -> Chambre:
    ch = Chambre.objects.select_related("etablissement__type").filter(etablissement=etab, numero=numero).first()
    if not ch:
        raise ErreurMetier(f"{etab.vocabulaire['lieu']} introuvable.", 404)
    return ch


def chambre_par_jeton(jeton: str) -> Chambre | None:
    """« 104-K7QM » → chambre 104 (de l'établissement concerné), si le code correspond."""
    if "-" not in jeton:
        return None
    numero, code = jeton.rsplit("-", 1)
    return (
        Chambre.objects.select_related("etablissement", "etablissement__type")
        .filter(numero=numero, code=code.strip().upper(), etablissement__actif=True)
        .first()
    )


def ajouter_chambre(etab: Etablissement, numero: str, libelle: str, service: str, lits: int) -> dict:
    v = etab.vocabulaire
    numero = numero.strip()
    if not 1 <= lits <= LITS_MAX:
        raise ErreurMetier(f"Nombre de {v['places'].lower()} : entre 1 et {LITS_MAX}.")
    if not numero or not re.match(r"^[A-Za-z0-9]{1,12}$", numero):
        raise ErreurMetier("Numéro : lettres et chiffres uniquement (12 au maximum).")
    if Chambre.objects.filter(etablissement=etab, numero=numero).exists():
        raise ErreurMetier(f"{v['lieu']} {numero} existe déjà.", 409)
    ch = Chambre.objects.create(
        etablissement=etab,
        numero=numero,
        libelle=libelle.strip() or f"{v['lieu']} {numero}",
        service=service.strip(),
        code=_code_chambre_libre(numero),
        lits=lits,
    )
    return chambre_dict(ch, complete=True)


def changer_lits(etab: Etablissement, numero: str, lits: int) -> dict:
    v = etab.vocabulaire
    if not 1 <= lits <= LITS_MAX:
        raise ErreurMetier(f"Nombre de {v['places'].lower()} : entre 1 et {LITS_MAX}.")
    with transaction.atomic():
        ch = _chambre(etab, numero)
        occupe = [s.lit for s in _sejours_actifs_chambre(ch) if s.lit > lits]
        if occupe:
            raise ErreurMetier(
                f"Impossible : {v['le_place']} {max(occupe)} n'est pas libre. "
                f"Enregistrez d'abord le départ de ce {v['beneficiaire'].lower()}.",
                409,
            )
        ch.lits = lits
        ch.save(update_fields=["lits"])
    return chambre_dict(ch, complete=True)


def regenerer_code(etab: Etablissement, numero: str) -> dict:
    ch = _chambre(etab, numero)
    ch.code = _code_chambre_libre(numero)
    ch.save(update_fields=["code"])
    return chambre_dict(ch, complete=True)


def supprimer_chambre(etab: Etablissement, numero: str) -> None:
    ch = _chambre(etab, numero)
    if Alerte.objects.filter(chambre=ch, statut__in=STATUTS_ACTIFS).exists():
        raise ErreurMetier(f"Impossible : un appel est en cours pour {etab.vocabulaire['le_lieu']} {numero}.", 409)
    ch.delete()  # séjours, téléphones et historique des appels avec


# ==========================================================================
# Séjours
# ==========================================================================
def _valider_sortie_prevue(valeur: str | None) -> datetime | None:
    """Date de sortie prévue (ISO 8601) : dans le futur, et dans moins d'un an."""
    if not valeur:
        return None
    try:
        date = datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ErreurMetier("Date de sortie invalide.")
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    t = maintenant()
    if date <= t + timedelta(minutes=1):
        raise ErreurMetier("La date de sortie doit être dans le futur.")
    if date > t + SORTIE_PREVUE_MAX:
        raise ErreurMetier("La date de sortie doit être dans moins d'un an.")
    return date.astimezone(timezone.utc).replace(microsecond=0)


def _sejours_actifs_qs():
    return Sejour.objects.select_related("chambre").filter(sortie_le__isnull=True, expire_le__gt=maintenant())


def _sejours_actifs_chambre(ch: Chambre) -> list[Sejour]:
    return list(_sejours_actifs_qs().filter(chambre=ch).order_by("lit"))


def _sejour_actif_lit(ch: Chambre, lit: int) -> Sejour | None:
    return _sejours_actifs_qs().filter(chambre=ch, lit=lit).order_by("-admis_le").first()


def sejours_actifs(etab: Etablissement) -> dict[str, list[dict]]:
    """Patients admis, regroupés par salle et triés par lit."""
    resultat: dict[str, list[dict]] = {}
    for s in _sejours_actifs_qs().filter(chambre__etablissement=etab).order_by("chambre__numero", "lit"):
        resultat.setdefault(s.chambre.numero, []).append(sejour_public(s))
    return resultat


def _code_libre(ch: Chambre) -> str:
    """Code à 4 chiffres différent de ceux des autres lits de la salle."""
    pris = {s.code for s in _sejours_actifs_chambre(ch)}
    while True:
        code = f"{secrets.randbelow(10_000):04d}"
        if code not in pris:
            return code


def _exiger_lit(etab: Etablissement, numero: str, lit: int) -> Chambre:
    ch = _chambre(etab, numero)
    if not 1 <= lit <= ch.lits:
        v = etab.vocabulaire
        raise ErreurMetier(f"{maj(v['le_lieu'])} {numero} compte {ch.lits} {v['places'].lower()}.", 400)
    return ch


def admettre(etab: Etablissement, numero: str, lit: int, initiales: str, par: str, sortie_prevue: str | None) -> dict:
    """Ouvre un séjour sur un lit et renvoie le code à donner au patient."""
    initiales = re.sub(r"[^A-Za-zÀ-ÿ. -]", "", initiales).strip().upper()[:6]
    prevue = _valider_sortie_prevue(sortie_prevue)
    t = maintenant().replace(microsecond=0)
    with transaction.atomic():
        ch = _exiger_lit(etab, numero, lit)
        if _sejour_actif_lit(ch, lit):
            raise ErreurMetier(f"{maj(etab.vocabulaire['le_place'])} {lit} n'est pas libre.", 409)
        s = Sejour.objects.create(
            id=uuid.uuid4().hex,
            chambre=ch,
            lit=lit,
            code=_code_libre(ch),
            initiales=initiales,
            admis_le=t,
            admis_par=par,
            expire_le=prevue or t + duree_sejour(),
            sortie_prevue=prevue,
        )
        Chambre.objects.filter(id=ch.id).update(essais_sejour=0)
    return sejour_public(s)


def _exiger_sejour_lit(etab: Etablissement, numero: str, lit: int) -> Sejour:
    s = _sejour_actif_lit(_chambre(etab, numero), lit)
    if not s:
        v = etab.vocabulaire
        raise ErreurMetier(f"Aucun {v['beneficiaire'].lower()} sur {v['le_place']} {lit}.", 409)
    return s


def changer_code_sejour(etab: Etablissement, numero: str, lit: int) -> dict:
    """Nouveau code (si l'ancien a circulé) : le téléphone du patient devra le saisir."""
    with transaction.atomic():
        s = _exiger_sejour_lit(etab, numero, lit)
        s.code = _code_libre(s.chambre)
        s.save(update_fields=["code"])
        AppareilPatient.objects.filter(sejour=s).delete()
        Chambre.objects.filter(id=s.chambre_id).update(essais_sejour=0)
    return sejour_public(s)


def modifier_sortie_prevue(etab: Etablissement, numero: str, lit: int, sortie_prevue: str | None) -> dict:
    """Change (ou retire) la date de sortie prévue d'un patient en cours de séjour."""
    prevue = _valider_sortie_prevue(sortie_prevue)
    with transaction.atomic():
        s = _exiger_sejour_lit(etab, numero, lit)
        s.sortie_prevue = prevue
        s.expire_le = prevue or maintenant().replace(microsecond=0) + duree_sejour()
        s.save(update_fields=["sortie_prevue", "expire_le"])
    return sejour_public(s)


def _terminer_alertes_sejour(s: Sejour, t: datetime, par: str) -> None:
    Alerte.objects.filter(sejour=s, statut__in=STATUTS_ACTIFS).update(
        statut="terminee", termine_le=t, termine_par=par
    )


def cloturer_sejours_expires() -> list[dict]:
    """Sorties automatiques (tous établissements) : séjours dont la date de sortie est passée.
    Clôture aussi leurs appels restés ouverts. Renvoie les séjours clôturés."""
    t = maintenant()
    clotures = []
    with transaction.atomic():
        for s in Sejour.objects.select_related("chambre__etablissement__type").filter(
            sortie_le__isnull=True, expire_le__lte=t
        ):
            if not Sejour.objects.filter(id=s.id, sortie_le__isnull=True).update(
                sortie_le=s.expire_le, sortie_par="Sortie automatique"
            ):
                continue  # déjà clôturé par un autre processus
            _terminer_alertes_sejour(s, t, "Sortie automatique")
            clotures.append(
                {
                    "etablissement_id": s.chambre.etablissement_id,
                    "chambre_libelle": s.chambre.libelle,
                    "lit": s.lit,
                    "place": s.chambre.etablissement.vocabulaire["place"],
                }
            )
    return clotures


def sortie(etab: Etablissement, numero: str, lit: int, par: str) -> dict:
    """Clôture le séjour du lit : le téléphone de ce patient ne peut plus appeler."""
    with transaction.atomic():
        s = _exiger_sejour_lit(etab, numero, lit)
        t = maintenant()
        s.sortie_le = t
        s.sortie_par = par
        s.save(update_fields=["sortie_le", "sortie_par"])
        _terminer_alertes_sejour(s, t, par)  # un appel resté ouvert est clôturé avec le séjour
    return sejour_public(s)


def activer_appareil(ch: Chambre, code: str) -> str:
    """Le patient saisit son code : son téléphone est lié à SON séjour (donc à son lit)."""
    v = ch.etablissement.vocabulaire
    code = re.sub(r"\D", "", code)
    sejours = _sejours_actifs_chambre(ch)
    if not sejours:
        raise ErreurMetier(f"L'accès n'est pas encore ouvert ici. Demandez {v['a_intervenant']} de l'activer.", 409)
    ch.refresh_from_db(fields=["essais_sejour"])
    if ch.essais_sejour >= ESSAIS_CODE_SALLE_MAX:
        raise ErreurMetier(f"Trop d'essais. Demandez un nouveau code {v['a_intervenant']}.", 429)
    sejour = next((s for s in sejours if hmac.compare_digest(code, s.code)), None)
    if not sejour:
        Chambre.objects.filter(id=ch.id).update(essais_sejour=ch.essais_sejour + 1)
        raise ErreurMetier(f"Code incorrect. Vérifiez le code donné par {v['l_intervenant']}.", 401)
    jeton = nouveau_jeton()
    AppareilPatient.objects.create(empreinte=empreinte_jeton(jeton), sejour=sejour)
    return jeton


def appareil(ch: Chambre, jeton: str | None) -> tuple[str, Sejour | None]:
    """
    Statut du téléphone du patient dans cette salle, et son séjour s'il est actif :
      actif        : lié à un séjour en cours → peut appeler (pour son lit)
      code_requis  : des patients sont admis, ce téléphone n'a pas (ou plus) de code valide
      termine      : ce téléphone appartenait à un séjour clôturé (patient sorti)
      libre        : aucun patient admis dans la salle
    """
    lie = None
    if jeton:
        a = AppareilPatient.objects.select_related("sejour", "sejour__chambre").filter(
            empreinte=empreinte_jeton(jeton)
        ).first()
        lie = a.sejour if a else None
    if lie and lie.chambre_id == ch.id:
        if lie.sortie_le is None and lie.expire_le > maintenant():
            return "actif", lie
        return "termine", None
    return ("code_requis" if _sejours_actifs_chambre(ch) else "libre"), None


# ==========================================================================
# Appels (alertes)
# ==========================================================================
def alerte_active_sejour(sejour_id: str) -> dict | None:
    return alerte_dict(
        _alertes().filter(sejour_id=sejour_id, statut__in=STATUTS_ACTIFS).order_by("-cree_le").first()
    )


def creer_alerte(s: Sejour) -> tuple[dict, bool]:
    """Crée l'appel d'un patient. S'il a déjà un appel en cours, on le renvoie (pas de doublon)."""
    existante = alerte_active_sejour(s.id)
    if existante:
        return existante, False
    a = Alerte.objects.create(
        id=uuid.uuid4().hex, chambre=s.chambre, lit=s.lit, sejour=s, statut="en_attente", cree_le=maintenant()
    )
    return alerte_dict(_alertes().get(id=a.id)), True


def _exiger_alerte(etab: Etablissement, alerte_id: str) -> Alerte:
    a = _alertes().filter(id=alerte_id, chambre__etablissement=etab).first()
    if not a:
        raise ErreurMetier("Appel introuvable.", 404)
    return a


def accuser_reception(etab: Etablissement, alerte_id: str, infirmier: str) -> dict:
    _exiger_alerte(etab, alerte_id)
    # La condition sur le statut garantit qu'une seule infirmière « gagne » si deux appuient en même temps.
    modifie = Alerte.objects.filter(id=alerte_id, statut="en_attente").update(
        statut="prise_en_charge", accuse_le=maintenant(), accuse_par=infirmier
    )
    a = _exiger_alerte(etab, alerte_id)
    if not modifie:
        if a.statut == "prise_en_charge":
            raise ErreurMetier(f"Déjà pris en charge par {a.accuse_par}.", 409)
        raise ErreurMetier("Cet appel n'est plus actif.", 409)
    return alerte_dict(a)


def terminer(etab: Etablissement, alerte_id: str, infirmier: str) -> dict:
    _exiger_alerte(etab, alerte_id)
    modifie = Alerte.objects.filter(id=alerte_id, statut="prise_en_charge").update(
        statut="terminee", termine_le=maintenant(), termine_par=infirmier
    )
    a = _exiger_alerte(etab, alerte_id)
    if not modifie:
        if a.statut == "en_attente":
            raise ErreurMetier("Accusez d'abord réception de l'appel.", 409)
        raise ErreurMetier("Cet appel n'est plus actif.", 409)
    return alerte_dict(a)


def annuler_sejour(sejour_id: str) -> dict | None:
    """Le patient annule son appel en cours (s'il y en a un)."""
    a = _alertes().filter(sejour_id=sejour_id, statut__in=STATUTS_ACTIFS).order_by("-cree_le").first()
    if not a:
        return None
    Alerte.objects.filter(id=a.id, statut__in=STATUTS_ACTIFS).update(statut="annulee", annule_le=maintenant())
    return alerte_dict(_alertes().get(id=a.id))


def alertes_visibles(etab: Etablissement, heures: int = 12, limite_historique: int = 25) -> list[dict]:
    """Appels en cours + historique récent, pour le tableau des soignants."""
    base = _alertes().filter(chambre__etablissement=etab)
    actives = base.filter(statut__in=STATUTS_ACTIFS).order_by("cree_le")
    historique = base.filter(
        statut__in=("terminee", "annulee"), cree_le__gte=maintenant() - timedelta(hours=heures)
    ).order_by("-cree_le")[:limite_historique]
    return [alerte_dict(a) for a in actives] + [alerte_dict(a) for a in historique]


def statistiques(etab: Etablissement) -> dict:
    """Indicateurs des dernières 24 h."""
    lignes = Alerte.objects.filter(
        chambre__etablissement=etab, cree_le__gte=maintenant() - timedelta(hours=24)
    ).exclude(statut="annulee").values_list("cree_le", "accuse_le")
    lignes = list(lignes)
    delais = [(accuse - cree).total_seconds() for cree, accuse in lignes if accuse]
    return {
        "appels_24h": len(lignes),
        "delai_moyen_s": round(sum(delais) / len(delais)) if delais else None,
        "delai_max_s": round(max(delais)) if delais else None,
    }


# ==========================================================================
# Messages
# ==========================================================================
def _verifier_texte(texte: str) -> str:
    texte = texte.strip()
    if not texte:
        raise ErreurMetier("Message vide.")
    if len(texte) > 500:
        raise ErreurMetier("Message trop long (500 caractères maximum).")
    return texte


def messages(etab: Etablissement, limite: int = 80) -> list[dict]:
    lignes = Message.objects.filter(etablissement=etab).order_by("-id")[:limite]
    return [message_dict(m) for m in reversed(list(lignes))]


def ajouter_message(etab: Etablissement, auteur: str, texte: str) -> dict:
    m = Message.objects.create(etablissement=etab, auteur=auteur[:60], texte=_verifier_texte(texte))
    return message_dict(m)


def partenaire_medical(compte: Compte, autre_id: int) -> Compte:
    """Messagerie privée : entre médecins et infirmiers actifs du même établissement."""

    def role_medical(role: str) -> str | None:
        return {"infirmier": "infirmier", "medecin": "medecin", "admin": "medecin"}.get(role)

    v = compte.etablissement.vocabulaire
    refus = f"La messagerie privée est réservée aux {v['role_medecin'].lower()}s et {v['role_infirmier'].lower()}s."
    if role_medical(compte.role) is None:
        raise ErreurMetier(refus, 403)
    autre = Compte.objects.filter(id=autre_id, etablissement_id=compte.etablissement_id).first()
    if not autre or autre.statut != "actif":
        raise ErreurMetier("Ce compte est introuvable ou indisponible.", 404)
    if role_medical(autre.role) is None:
        raise ErreurMetier(refus, 403)
    return autre


def messages_prives(compte_id: int, autre_id: int, limite: int = 100) -> list[dict]:
    lignes = _messages_prives().filter(
        Q(expediteur_id=compte_id, destinataire_id=autre_id)
        | Q(expediteur_id=autre_id, destinataire_id=compte_id)
    ).order_by("-id")[:limite]
    return [message_prive_dict(m) for m in reversed(list(lignes))]


def appels_manques_non_lus(destinataire_id: int) -> list[dict]:
    lignes = _messages_prives().filter(
        destinataire_id=destinataire_id, type="appel_manque", lu_le__isnull=True
    ).order_by("id")
    return [message_prive_dict(m) for m in lignes]


def _ajouter_message_prive(expediteur_id: int, destinataire_id: int, texte: str, type_message: str) -> dict:
    m = MessagePrive.objects.create(
        expediteur_id=expediteur_id,
        destinataire_id=destinataire_id,
        texte=_verifier_texte(texte),
        type=type_message,
    )
    return message_prive_dict(_messages_prives().get(id=m.id))


def ajouter_message_prive(expediteur_id: int, destinataire_id: int, texte: str) -> dict:
    return _ajouter_message_prive(expediteur_id, destinataire_id, texte, "texte")


def ajouter_appel_manque(expediteur_id: int, destinataire_id: int) -> dict:
    return _ajouter_message_prive(expediteur_id, destinataire_id, "Appel manqué", "appel_manque")


def ajouter_vocal_prive(expediteur_id: int, destinataire_id: int, audio: bytes, audio_type: str) -> dict:
    if not audio:
        raise ErreurMetier("L'enregistrement vocal est vide.")
    m = MessagePrive.objects.create(
        expediteur_id=expediteur_id, destinataire_id=destinataire_id, texte="", type="audio",
        audio=audio, audio_type=audio_type,
    )
    return message_prive_dict(_messages_prives().get(id=m.id))


def vocal_prive(message_id: int, compte_id: int, autre_id: int) -> MessagePrive | None:
    return MessagePrive.objects.filter(
        Q(expediteur_id=compte_id, destinataire_id=autre_id)
        | Q(expediteur_id=autre_id, destinataire_id=compte_id),
        id=message_id,
        type="audio",
    ).first()


def marquer_messages_prives_lus(lecteur_id: int, expediteur_id: int) -> dict:
    with transaction.atomic():
        ids = list(
            MessagePrive.objects.filter(
                expediteur_id=expediteur_id, destinataire_id=lecteur_id, lu_le__isnull=True
            ).values_list("id", flat=True)
        )
        if not ids:
            return {"ids": [], "lu_le": None}
        lu_le = maintenant().replace(microsecond=0)
        MessagePrive.objects.filter(id__in=ids, lu_le__isnull=True).update(lu_le=lu_le)
    return {"ids": ids, "lu_le": iso(lu_le)}


# ---------------------------------------------------------------- urgences
def ajouter_urgence(message_id: int, destinataire_id: int) -> None:
    Urgence.objects.update_or_create(
        message_id=message_id,
        defaults={"destinataire_id": destinataire_id, "actif": True, "cree_le": maintenant()},
    )


def clore_urgence(message_id: int, ferme_par: str | None = None) -> dict | None:
    u = Urgence.objects.filter(message_id=message_id, actif=True).first()
    if not u:
        return None
    ferme_le = maintenant()
    Urgence.objects.filter(message_id=message_id).update(actif=False, ferme_par=ferme_par, ferme_le=ferme_le)
    return {
        "message_id": u.message_id,
        "destinataire_id": u.destinataire_id,
        "actif": 1,
        "cree_le": iso(u.cree_le),
        "ferme_par": ferme_par,
        "ferme_le": iso(ferme_le),
    }


def urgence_par_message(message_id: int) -> Urgence | None:
    return Urgence.objects.filter(message_id=message_id).first()


def urgences_actives(destinataire_id: int) -> list[dict]:
    """Urgences actives destinées à un compte."""
    lignes = Urgence.objects.select_related("message", "message__expediteur").filter(
        actif=True, destinataire_id=destinataire_id
    )
    return [
        {
            "message_id": u.message_id,
            "destinataire_id": u.destinataire_id,
            "expediteur_id": u.message.expediteur_id,
            "expediteur_nom": u.message.expediteur.nom,
            "expediteur_role": u.message.expediteur.role,
            "texte": u.message.texte,
            "cree_le": iso(u.message.cree_le),
        }
        for u in lignes
    ]
