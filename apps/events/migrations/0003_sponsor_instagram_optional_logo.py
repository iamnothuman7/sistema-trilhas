from django.db import migrations, models
import apps.events.validators


class Migration(migrations.Migration):
    dependencies = [("events", "0002_event_subdomain")]

    operations = [
        migrations.AddField(
            model_name="sponsor", name="instagram_url",
            field=models.URLField(blank=True, validators=[apps.events.validators.validate_instagram_profile], verbose_name="Instagram"),
        ),
        migrations.AlterField(
            model_name="sponsor", name="logo_image",
            field=models.ImageField(blank=True, upload_to="events/sponsors/", validators=[apps.events.validators.validate_sponsor_logo_size], verbose_name="Logotipo"),
        ),
    ]
