"""Intergiciels (middlewares) de Sentinelle."""


class EntetesSecurite:
    """En-têtes de sécurité ajoutés à toutes les réponses (comme l'ancienne API)."""

    def __init__(self, suivant):
        self.suivant = suivant

    def __call__(self, requete):
        reponse = self.suivant(requete)
        reponse.setdefault("X-Content-Type-Options", "nosniff")
        reponse.setdefault("Referrer-Policy", "no-referrer")
        if requete.path.startswith("/api/"):
            reponse.setdefault("Cache-Control", "no-store")  # les logos fixent leur propre cache
        return reponse
