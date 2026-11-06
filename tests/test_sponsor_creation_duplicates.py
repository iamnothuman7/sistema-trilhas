from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Organization
from apps.events.models import Event, Sponsor, SponsorProfile
from apps.finance.models import FinancialEntry
from tests import test_sponsorship_finance as fixtures


class SponsorCreationDuplicateTests(TestCase):
    setUp = fixtures.SponsorshipFinanceTests.setUp

    def create_sponsor(self, name, event=None):
        return self.client.post(reverse("gestao:sponsor_create", args=[(event or self.event).pk]), {
            "name": name, "tier": "OURO", "sort_order": "0", "agreement-amount": "250,00",
        })

    def test_replaying_event_creation_does_not_duplicate_agreement(self):
        first = self.create_sponsor("Parceiro de auditoria")
        second = self.create_sponsor("Parceiro de auditoria")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertFormError(second.context["form"], "name", "Esta marca já está cadastrada. Edite o cadastro existente ou use Vincular marca para adicioná-la ao evento.")
        self.assertEqual(Sponsor.objects.filter(name="Parceiro de auditoria").count(), 1)
        self.assertEqual(SponsorProfile.objects.filter(name="Parceiro de auditoria").count(), 1)
        self.assertEqual(Sponsor.objects.get(name="Parceiro de auditoria").agreed_amount_cents, 25000)
        self.assertFalse(FinancialEntry.objects.exists())

    def test_replaying_brand_creation_keeps_one_brand(self):
        url = reverse("gestao:sponsor_profile_create")
        first = self.client.post(url, {"name": "Marca para vincular"})
        second = self.client.post(url, {"name": "Marca para vincular"})
        self.assertEqual((first.status_code, second.status_code), (302, 400))
        self.assertEqual(SponsorProfile.objects.filter(name="Marca para vincular").count(), 1)

    def test_accents_case_and_whitespace_do_not_create_another_brand(self):
        SponsorProfile.objects.create(organization=self.org, name="Hotel Árvore Grande")
        for route in (reverse("gestao:sponsor_profile_create"), reverse("gestao:sponsor_create", args=[self.event.pk])):
            response = self.client.post(route, {"name": " hotel arvore   GRANDE ", "tier": "OURO", "sort_order": 0})
            self.assertEqual(response.status_code, 400)
            self.assertIn("name", response.context["form"].errors)
        self.assertEqual(SponsorProfile.objects.filter(organization=self.org).count(), 2)

    def test_same_organization_other_event_must_link_existing_brand(self):
        response = self.create_sponsor(self.profile.name, self.other)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sponsor.objects.filter(event=self.other).exists())

    def test_archived_brand_is_not_recreated(self):
        self.profile.archived_at = timezone.now()
        self.profile.save(update_fields=["archived_at"])
        response = self.create_sponsor(self.profile.name)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Restaure o cadastro existente", status_code=400)
        self.assertEqual(SponsorProfile.objects.filter(name=self.profile.name).count(), 1)
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.archived_at)

    def test_legacy_participation_without_brand_is_not_duplicated(self):
        Sponsor.objects.create(event=self.event, name="Apoiador legado")
        response = self.create_sponsor("apoiador legado")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SponsorProfile.objects.filter(name__iexact="apoiador legado").exists())

    def test_same_name_in_another_organization_is_allowed(self):
        organization = Organization.objects.create(name="Outra organização")
        other = Event.objects.create(organization=organization, title="Outra edição", slug="sponsor-duplicate-other-org",
            starts_at=self.event.starts_at, ends_at=self.event.ends_at,
            registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        response = self.create_sponsor(self.profile.name, other)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SponsorProfile.objects.filter(name=self.profile.name).count(), 2)
        self.assertFalse(FinancialEntry.objects.exists())
