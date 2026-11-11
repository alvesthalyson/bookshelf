from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("library", "0005_favorite"),
    ]

    operations = [
        migrations.AddField(
            model_name="loan",
            name="fine_paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
