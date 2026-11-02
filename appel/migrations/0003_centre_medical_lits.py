"""Centre médical : « Lit » au lieu de « Poste » (sauf si le propriétaire l'a déjà changé)."""

from django.db import migrations

CLES = ("place", "places", "le_place")


def lits(apps, schema_editor):
    Type = apps.get_model("appel", "TypeEtablissement")
    for t in Type.objects.filter(nom="Centre médical"):
        vocab = dict(t.vocabulaire or {})
        if vocab.get("place") == "Poste":
            for cle in CLES:
                vocab.pop(cle, None)
            t.vocabulaire = vocab
            t.save(update_fields=["vocabulaire"])


class Migration(migrations.Migration):
    dependencies = [("appel", "0002_types_adresse_activation")]
    operations = [migrations.RunPython(lits, migrations.RunPython.noop)]
