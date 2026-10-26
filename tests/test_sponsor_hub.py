from django.test import TestCase
from django.urls import reverse

from apps.events.models import Sponsor, SponsorProfile
from apps.events.sponsorships import brand_values
from apps.identity.models import EventMembership, RoleChoices
from tests import test_sponsorship_finance as fixtures


class SponsorHubTests(TestCase):
    setUp = fixtures.SponsorshipFinanceTests.setUp
    role = fixtures.SponsorshipFinanceTests.role

    def url(self):
        return reverse("gestao:sponsor_bank")

    def test_one_brand_card_collects_all_visible_events_and_one_menu_entry(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, agreed_amount_cents=50000, **brand_values(self.profile))
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["cards"]), 1)
        self.assertEqual(len(response.context["cards"][0]["participations"]), 2)
        self.assertEqual(response.context["totals"]["agreed_cents"], 150000)
        self.assertContains(response, 'title="Patrocinadores"', count=1)
        self.assertNotContains(response, "Banco de patrocinadores")
        self.assertContains(response, "Editar cadastro e logo")

    def test_event_filter_and_old_event_url_use_same_hub(self):
        profile = SponsorProfile.objects.create(organization=self.org, name="Sem evento ainda")
        all_events = self.client.get(self.url())
        self.assertContains(all_events, profile.name)
        for url, params in [(self.url(), {"event_id": str(self.event.pk)}),
                            (reverse("gestao:sponsor_list", args=[self.event.pk]), {})]:
            response = self.client.get(url, params)
            self.assertTemplateUsed(response, "gestao/sponsors/bank.html")
            self.assertNotContains(response, profile.name)
            self.assertContains(response, "Vincular patrocinador existente")
        self.assertEqual(self.client.get(self.url(), {"event_id": "invalid"}).status_code, 404)

    def test_mixed_roles_never_show_other_event_finances_or_outside_event(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, agreed_amount_cents=987654,
                               **brand_values(self.profile))
        user = self.role(RoleChoices.CONTENT)
        response = self.client.get(self.url())
        self.assertNotContains(response, self.other.title)
        self.assertNotContains(response, "Total combinado")
        self.assertEqual(self.client.get(self.url(), {"event_id": self.other.pk}).status_code, 403)
        EventMembership.objects.create(user=user, event=self.other, role_code=RoleChoices.FINANCE)
        response = self.client.get(self.url())
        self.assertEqual(response.context["totals"]["agreed_cents"], 987654)
        parts = response.context["cards"][0]["participations"]
        self.assertEqual({s.event_id: s.can_finance for s in parts}, {self.event.pk: False, self.other.pk: True})

    def test_legacy_rows_stay_visible_without_creating_profiles_on_read(self):
        Sponsor.objects.create(event=self.event, name="Patrocinador legado", agreed_amount_cents=5000)
        before = SponsorProfile.objects.count()
        response = self.client.get(self.url(), {"q": "legado", "event_id": self.event.pk})
        self.assertContains(response, "Patrocinador legado")
        self.assertEqual(len(response.context["cards"]), 1)
        self.assertEqual(SponsorProfile.objects.count(), before)

    def test_search_contacts_and_status_are_scoped_and_keep_financial_visibility(self):
        response = self.client.get(self.url(), {"q": "99999", "status": "pending"})
        self.assertEqual(len(response.context["cards"]), 1)
        self.role(RoleChoices.CONTENT)
        self.assertEqual(len(self.client.get(self.url(), {"status": "pending"}).context["cards"]), 0)
