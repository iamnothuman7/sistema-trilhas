from datetime import timedelta
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.events.models import Event, EventStatus, TeamAlias
from apps.identity.models import EventMembership, Person, RoleChoices, User
from apps.mediaframes.models import FrameTemplate, GeneratedMontage
from apps.mediaframes.team_art import confirmed_teams, team_art_config
from apps.registrations.models import CommercialState, OperationalState, Registration
from tests import test_system as _fixtures


class TeamFrameTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def url(self, event=None):
        return reverse("gestao:team_frame_editor", kwargs={"event_id": (event or self.event).id})

    def operator(self, role=RoleChoices.CONTENT):
        user = User.objects.create_user(email=f"{uuid.uuid4()}@example.test", password="test-password", full_name="Conteúdo")
        EventMembership.objects.create(user=user, event=self.event, role_code=role)
        self.client.force_login(user)
        return user

    def registration(self, team, event=None, state=CommercialState.ACTIVE, order_state=OrderStatus.PAID):
        event = event or self.event
        order = Order.objects.create(event=event, merchant_reference=str(uuid.uuid4()), purchaser_name="Dado privado", purchaser_email="private@example.test", status=order_state, total_cents=12000, expires_at=timezone.now() + timedelta(hours=1))
        line = OrderLine.objects.create(order=order, intended_person_name="Pessoa privada", unit_cents=12000, net_cents=12000)
        person = Person.objects.create(full_name="Pessoa privada", email="private@example.test")
        return Registration.objects.create(event=event, source_order_line=line, person=person, team_name=team, commercial_state=state, operational_state=OperationalState.NOT_READY)

    def other_event(self):
        return Event.objects.create(organization=self.org, title="Outra edição", slug="outra-edicao-artes", starts_at=self.event.starts_at, ends_at=self.event.ends_at, registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)

    def test_editor_requires_authentication(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/?next=", response.url)

    def test_content_role_can_use_own_draft_but_not_other_event(self):
        self.operator()
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=["status"])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.other_event())).status_code, 403)

    def test_finance_and_registration_roles_cannot_open_editor(self):
        for role in (RoleChoices.FINANCE, RoleChoices.REGISTRATION_AGENT):
            with self.subTest(role=role):
                self.operator(role)
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_revoked_content_membership_loses_access(self):
        user = self.operator()
        EventMembership.objects.filter(user=user).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_only_paid_active_teams_from_same_event_are_included(self):
        self.registration("  Equipe   Serra ")
        self.registration("equipe serra")
        self.registration("Equipe reservada", state=CommercialState.RESERVED)
        self.registration("Equipe cancelada", state=CommercialState.CANCELED)
        self.registration("Equipe sem pagamento", order_state=OrderStatus.AWAITING_PAYMENT)
        self.registration("Equipe de outra edição", event=self.other_event())
        for name in ("", "  ", "Não informado", "Sem equipe"):
            self.registration(name)
        teams = confirmed_teams(self.event)
        self.assertEqual(len(teams), 1)
        self.assertEqual(teams[0]["name"], "Equipe Serra")
        self.assertEqual(teams[0]["count"], 2)

    def test_rendered_config_contains_no_personal_details_and_is_not_cached(self):
        self.registration("Equipe Serra")
        self.operator()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="confirmed-teams"')
        self.assertContains(response, "Equipe Serra")
        self.assertContains(response, "1 inscrição ativa")
        self.assertNotContains(response, "Pessoa privada")
        self.assertNotContains(response, "private@example.test")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, "/api/montages/")
        self.assertNotContains(response, "/api/remove-background/")

    def test_editor_uses_the_same_spelling_corrections_as_the_public_ranking(self):
        self.registration("Trilheros")
        self.registration("Trilheiros")
        TeamAlias.objects.create(event=self.event, source_name="Trilheros", canonical_name="Trilheiros")
        teams = confirmed_teams(self.event)
        self.assertEqual([(team["name"], team["count"]) for team in teams], [("Trilheiros", 2)])

    def test_empty_edition_can_prepare_art_but_requires_a_photo_to_export(self):
        self.operator()
        response = self.client.get(self.url())
        self.assertContains(response, 'id="download-button" type="button" disabled')
        self.assertContains(response, '<option value="team" selected>Equipe confirmada</option>', html=True)
        self.assertNotContains(response, 'id="teamArtCanvas"')

    def test_team_name_is_escaped_in_html_and_json(self):
        self.registration('</script><script>alert("nome")</script>')
        self.operator()
        response = self.client.get(self.url())
        self.assertNotContains(response, '<script>alert("nome")</script>')
        self.assertContains(response, '&lt;/script&gt;&lt;script&gt;')

    def test_get_and_rejected_post_do_not_create_public_templates_or_archives(self):
        self.registration("Equipe somente interna")
        self.operator()
        counts = (FrameTemplate.objects.count(), GeneratedMontage.objects.count())
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.post(self.url(), {"team": "Outra", "image": "data:image/png"}).status_code, 405)
        self.assertEqual((FrameTemplate.objects.count(), GeneratedMontage.objects.count()), counts)
        self.client.logout()
        public = self.client.get(reverse("events:frames", kwargs={"slug": self.event.slug}))
        self.assertEqual(public.status_code, 200)
        self.assertNotContains(public, "Equipe somente interna")
        self.assertNotContains(public, "team_frame_editor.js")

    def test_event_logo_prefers_upload_and_fallback_is_scoped_to_edition(self):
        self.theme.logo_image = "events/logos/exact-edition.png"
        self.theme.save(update_fields=["logo_image"])
        self.assertTrue(team_art_config(self.event, [])["editionLogo"].endswith("events/logos/exact-edition.png"))
        self.theme.logo_image = ""
        self.theme.save(update_fields=["logo_image"])
        self.event.slug = "3-trilhao-tiangua-off-road-2027"
        self.assertTrue(team_art_config(self.event, [])["editionLogo"].endswith("logo_3trilhao_2027.png"))
        self.event.slug = "outra-edicao"
        config = team_art_config(self.event, [])
        self.assertTrue(config["editionLogo"].endswith("logo_emblem.png"))
        self.assertEqual(config["eventTitle"], self.event.title)
