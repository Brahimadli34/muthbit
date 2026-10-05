from django.db import migrations


def seed(apps, schema_editor):
    TG = apps.get_model("verifier", "TrustedGrader")
    for i, (name, aliases) in enumerate([
        ("الألباني", "الالباني، ناصر الدين الألباني"),
        ("أحمد شاكر", "احمد شاكر"),
        ("شعيب الأرناؤوط", "الأرناؤوط، الارناؤوط، الأرنؤوط، الارنؤوط"),
    ], start=1):
        TG.objects.get_or_create(name=name, defaults={"aliases": aliases, "priority": i * 10})


class Migration(migrations.Migration):
    dependencies = [("verifier", "0004_trusted_graders")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
