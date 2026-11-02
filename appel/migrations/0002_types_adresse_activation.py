"""Types d'établissement (vocabulaire), adresse propre (slug) et code d'activation à usage unique."""

import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify

import appel.outils

TYPES = {
    "Hôpital": {},
    "Centre médical": {
        "lieu": "Salle", "lieux": "Salles", "le_lieu": "la salle",
        "place": "Poste", "places": "Postes", "le_place": "le poste",
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


def remplir(apps, schema_editor):
    Type = apps.get_model("appel", "TypeEtablissement")
    Etab = apps.get_model("appel", "Etablissement")
    types = {nom: Type.objects.get_or_create(nom=nom, defaults={"vocabulaire": v})[0] for nom, v in TYPES.items()}
    pris = set()
    for e in Etab.objects.all():
        base = slugify(e.nom)[:50] or "etablissement"
        slug, n = base, 2
        while slug in pris:
            slug, n = f"{base}-{n}", n + 1
        pris.add(slug)
        e.slug = slug
        e.type = types["Hôpital"]
        e.active_le = e.cree_le  # établissements existants : déjà en service
        e.save(update_fields=["slug", "type", "active_le"])


class Migration(migrations.Migration):
    dependencies = [("appel", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="TypeEtablissement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nom", models.CharField(max_length=60, unique=True)),
                ("vocabulaire", models.JSONField(blank=True, default=dict)),
                ("cree_le", models.DateTimeField(default=appel.outils.maintenant)),
            ],
            options={"verbose_name": "type d'établissement", "ordering": ["nom"]},
        ),
        migrations.AddField(
            model_name="etablissement",
            name="type",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="etablissements", to="appel.typeetablissement"),
        ),
        migrations.AddField(model_name="etablissement", name="slug", field=models.SlugField(max_length=60, null=True)),
        migrations.AddField(model_name="etablissement", name="code_activation",
                            field=models.CharField(blank=True, max_length=12, null=True, unique=True)),
        migrations.AddField(model_name="etablissement", name="code_activation_expire",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="etablissement", name="active_le", field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(remplir, migrations.RunPython.noop),
        migrations.AlterField(model_name="etablissement", name="slug", field=models.SlugField(max_length=60, unique=True)),
        migrations.AlterField(
            model_name="etablissement",
            name="type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name="etablissements", to="appel.typeetablissement"),
        ),
        migrations.RemoveField(model_name="etablissement", name="code"),
    ]
