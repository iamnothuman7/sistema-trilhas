from datetime import timedelta
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class SponsorBankMigrationTests(TransactionTestCase):
    def test_existing_sponsors_preserve_images_publication_and_unknown_finances(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [("events", "0007_event_cover_images"), ("finance", "0004_private_financial_documents")]
        try:
            executor.migrate(old_target)
            old = executor.loader.project_state(old_target).apps
            org = old.get_model("core", "Organization").objects.create(name="Organização legada")
            now = timezone.now()
            event = old.get_model("events", "Event").objects.create(organization=org, title="Evento legado", slug="legacy-sponsor-migration",
                starts_at=now, ends_at=now + timedelta(days=1), registration_opens_at=now, registration_closes_at=now)
            old.get_model("events", "Sponsor").objects.create(event=event, name="Marca legada", website=None,
                logo_image="events/sponsors/existing.png", logo_preview="events/sponsors/previews/existing.webp")
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from apps.events.models import Sponsor, SponsorProfile
            sponsor = Sponsor.objects.get(name="Marca legada")
            self.assertTrue(sponsor.is_public)
            self.assertIsNone(sponsor.agreed_amount_cents)
            self.assertEqual(sponsor.profile.organization_id, org.pk)
            self.assertEqual(sponsor.profile.logo_image.name, sponsor.logo_image.name)
            self.assertEqual(sponsor.profile.logo_preview.name, sponsor.logo_preview.name)
            self.assertEqual(sponsor.profile.website, "")
            self.assertEqual(SponsorProfile.objects.count(), 1)
            self.assertFalse(sponsor.payments.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
