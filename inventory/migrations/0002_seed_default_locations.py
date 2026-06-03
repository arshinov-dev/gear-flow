from django.db import migrations


def create_default_locations(apps, schema_editor):
    Holder = apps.get_model("inventory", "Holder")
    for name in ("Студия", "Склад"):
        Holder.objects.get_or_create(
            name=name,
            holder_type="location",
            defaults={
                "is_active": True,
                "is_self_service_source": True,
                "metadata": {},
            },
        )


def remove_default_locations(apps, schema_editor):
    Holder = apps.get_model("inventory", "Holder")
    Holder.objects.filter(name__in=["Студия", "Склад"], holder_type="location").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_default_locations, remove_default_locations),
    ]
