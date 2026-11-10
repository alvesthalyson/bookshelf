from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("library", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="cover_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="book",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="book",
            name="genre",
            field=models.CharField(blank=True, max_length=60),
        ),
        migrations.AddField(
            model_name="book",
            name="pages",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
