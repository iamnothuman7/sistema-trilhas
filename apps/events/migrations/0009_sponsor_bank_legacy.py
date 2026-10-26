from django.db import migrations


def populate_bank(apps, schema_editor):
    Sponsor = apps.get_model("events", "Sponsor")
    Profile = apps.get_model("events", "SponsorProfile")
    alias = schema_editor.connection.alias
    # Keep existing publication and image paths. Never guess that similar names
    # are the same company, nor infer a payment from an old approval date.
    for sponsor in Sponsor.objects.using(alias).select_related("event").filter(profile__isnull=True).iterator():
        values = {field: getattr(sponsor, field) for field in ("name", "instagram_url", "logo_image", "logo_preview", "logo_preview_width", "logo_preview_height")}
        profile = Profile.objects.using(alias).create(organization_id=sponsor.event.organization_id, website=sponsor.website or "", **values)
        Sponsor.objects.using(alias).filter(pk=sponsor.pk).update(profile=profile, is_public=True)


class Migration(migrations.Migration):
    dependencies = [("events", "0008_sponsor_agreed_amount_cents_sponsor_agreement_notes_and_more")]
    operations = [migrations.RunPython(populate_bank, migrations.RunPython.noop)]
