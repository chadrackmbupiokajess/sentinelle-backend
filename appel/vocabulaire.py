"""
Vocabulaire des types d'établissement (hôpital, centre médical, restaurant…).

Le propriétaire de la plateforme crée les types et règle leurs mots dans /plateforme ;
toutes les pages et tous les messages les utilisent (« Chambre » ou « Table », « Lit » ou
« Place », « Patient » ou « Client »…). Une clé absente prend la valeur par défaut (hôpital).
"""

from __future__ import annotations

# Clé → (libellé dans /plateforme, valeur par défaut = hôpital)
CLES: dict[str, tuple[str, str]] = {
    "lieu": ("Lieu d'appel (singulier)", "Chambre"),
    "lieux": ("Lieu d'appel (pluriel)", "Chambres"),
    "le_lieu": ("Lieu avec article", "la chambre"),
    "place": ("Place dans le lieu (singulier)", "Lit"),
    "places": ("Place (pluriel)", "Lits"),
    "le_place": ("Place avec article", "le lit"),
    "beneficiaire": ("Personne qui appelle (singulier)", "Patient"),
    "beneficiaires": ("Personne qui appelle (pluriel)", "Patients"),
    "personnel": ("Personnel (singulier)", "Soignant"),
    "personnels": ("Personnel (pluriel)", "Soignants"),
    "role_infirmier": ("Rôle de base", "Infirmier"),
    "role_medecin": ("Rôle supérieur", "Médecin"),
    "l_intervenant": ("Qui répond, avec article (« par … »)", "l'infirmière"),
    "a_intervenant": ("Qui répond (« demandez … »)", "à l'infirmière"),
    "service": ("Regroupement des lieux", "Service"),
    "admission": ("Arrivée (nom)", "Admission"),
    "admettre": ("Arrivée (verbe)", "Admettre"),
    "sortie": ("Départ (nom)", "Sortie"),
    "sejour": ("Durée de présence", "séjour"),
    "conseil_urgence": ("Conseil si trop d'appels", "En cas d'urgence, utilisez la sonnette murale."),
    "groupes_initiaux": ("Exemples de regroupements (séparés par des virgules)", "Aile Nord, Aile Sud"),
    "places_par_defaut": ("Places proposées par défaut pour un nouveau lieu", "5"),
}

DEFAUT: dict[str, str] = {cle: valeur for cle, (_, valeur) in CLES.items()}

TYPES_PAR_DEFAUT: dict[str, dict[str, str]] = {
    "Hôpital": {},
    "Centre médical": {
        "lieu": "Salle", "lieux": "Salles", "le_lieu": "la salle",
        # place : « Lit » (valeur par défaut), comme à l'hôpital.
        "groupes_initiaux": "Consultations, Soins", "places_par_defaut": "3",
    },
    "Restaurant": {
        "lieu": "Table", "lieux": "Tables", "le_lieu": "la table",
        "place": "Place", "places": "Places", "le_place": "la place",
        "beneficiaire": "Client", "beneficiaires": "Clients",
        "personnel": "Serveur", "personnels": "Serveurs",
        "role_infirmier": "Serveur", "role_medecin": "Responsable",
        "l_intervenant": "le serveur", "a_intervenant": "au serveur",
        "service": "Salle", "admission": "Installation", "admettre": "Installer",
        "sortie": "Départ", "sejour": "repas",
        "conseil_urgence": "Un serveur arrive dès que possible.",
        "groupes_initiaux": "Salle, Terrasse", "places_par_defaut": "4",
    },
}


def complet(vocab: dict | None) -> dict[str, str]:
    """Vocabulaire complet : valeurs du type, sinon valeurs par défaut."""
    resultat = dict(DEFAUT)
    for cle, valeur in (vocab or {}).items():
        if cle in CLES and isinstance(valeur, str) and valeur.strip():
            resultat[cle] = valeur.strip()
    return resultat


def nettoyer(vocab: dict) -> dict[str, str]:
    """Garde seulement les clés connues, différentes de la valeur par défaut."""
    propre = {}
    for cle, valeur in (vocab or {}).items():
        if cle in CLES and isinstance(valeur, str) and valeur.strip() and valeur.strip() != DEFAUT[cle]:
            propre[cle] = valeur.strip()[:120]
    return propre


def maj(texte: str) -> str:
    return texte[:1].upper() + texte[1:]
