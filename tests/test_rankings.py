from datetime import timedelta
from unittest.mock import patch
import uuid

from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import AuditLog
from apps.events.models import CityAlias, Event, EventStatus, TeamAlias
from apps.events.rankings import get_event_rankings, group_key
from apps.identity.models import EventMembership, Person, RoleChoices, User
from apps.registrations.models import CommercialState, OperationalState, Registration
from tests import test_system as _fixtures


class EventRankingTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def registration(self, team="Equipe Serra", city="Tianguá", state="CE", event=None, commercial=CommercialState.ACTIVE, paid=OrderStatus.PAID, person=None):
        event = event or self.event
        order = Order.objects.create(event=event, merchant_reference=str(uuid.uuid4()), purchaser_name="Privado", purchaser_email="pessoa@example.test", status=paid, total_cents=12000, expires_at=timezone.now() + timedelta(hours=1))
        line = OrderLine.objects.create(order=order, intended_person_name="Nome privado", unit_cents=12000, net_cents=12000)
        person = person or Person.objects.create(full_name="Nome privado", email="pessoa@example.test", city=city, state=state)
        return Registration.objects.create(event=event, person=person, source_order_line=line, team_name=team, commercial_state=commercial, operational_state=OperationalState.NOT_READY)

    def other_event(self):
        return Event.objects.create(organization=self.org, title="Outra edição", slug="outra-edicao-ranking", starts_at=self.event.starts_at, ends_at=self.event.ends_at, registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)

    def url(self, event=None):
        return reverse("gestao:ranking_management", kwargs={"event_id": (event or self.event).pk})

    def operator(self, role=RoleChoices.REGISTRATION_AGENT):
        user = User.objects.create_user(email=f"{uuid.uuid4()}@example.test", password="test-password")
        EventMembership.objects.create(user=user, event=self.event, role_code=role)
        self.client.force_login(user)
        return user

    def correction(self, source, canonical, kind="team", state="CE"):
        data = {"action": f"save_{kind}", f"{kind}-source_name": source, f"{kind}-canonical_name": canonical, f"{kind}-reason": "Correção de grafia confirmada"}
        if kind == "city":
            data["city-state"] = state
        return self.client.post(self.url(), data)

    def test_normalization_ignores_accents_case_and_whitespace_but_not_spelling(self):
        self.assertEqual(group_key(" São  João \t"), group_key("SAOJOAO"))
        self.assertNotEqual(group_key("Trilheirs"), group_key("Trilheiros"))
        self.assertNotEqual(group_key("Equipe-A"), group_key("Equipe A"))

    def test_only_active_paid_registration_counts_without_requiring_ready(self):
        self.registration()
        self.registration(commercial=CommercialState.RESERVED)
        self.registration(commercial=CommercialState.CANCELED)
        self.registration(commercial=CommercialState.TRANSFERRED)
        for status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT, OrderStatus.EXPIRED, OrderStatus.CANCELED, OrderStatus.PARTIALLY_REFUNDED, OrderStatus.REFUNDED):
            self.registration(paid=status)
        result = get_event_rankings(self.event)
        self.assertEqual(result["confirmed_count"], 1)
        self.assertEqual(result["teams"][0]["count"], 1)
        self.assertEqual(result["cities"][0]["count"], 1)

    def test_groups_spelling_variants_and_keeps_same_city_names_in_distinct_states(self):
        self.registration("São João", "São Benedito", "CE")
        self.registration(" SAOJOAO ", "Sao  Benedito", " ce ")
        self.registration("são joão", "São Benedito", "MA")
        self.registration("Outra", "São Benedito", "")
        result = get_event_rankings(self.event)
        self.assertEqual(result["teams"][0]["count"], 3)
        self.assertEqual({(row["state"], row["count"]) for row in result["cities"]}, {("CE", 2), ("MA", 1), ("", 1)})

    def test_counts_registrations_once_even_when_person_has_two_paid_registrations(self):
        first = self.registration()
        self.registration(person=first.person)
        result = get_event_rankings(self.event)
        self.assertEqual(result["confirmed_count"], 2)
        self.assertEqual(result["teams"][0]["count"], 2)

    def test_missing_names_are_excluded_from_groups_not_total_confirmations(self):
        for value in ("", " Não informado ", "-"):
            self.registration(team=value, city=value)
        result = get_event_rankings(self.event)
        self.assertEqual(result["confirmed_count"], 3)
        self.assertEqual(result["teams"], [])
        self.assertEqual(result["cities"], [])

    def test_rankings_and_aliases_are_isolated_per_event(self):
        other = self.other_event()
        self.registration("Trilheros")
        self.registration("Outra equipe", event=other)
        TeamAlias.objects.create(event=other, source_name="Trilheros", canonical_name="Não aplicar")
        self.assertEqual(get_event_rankings(self.event)["teams"][0]["name"], "Trilheros")
        self.assertEqual(get_event_rankings(self.event)["confirmed_count"], 1)

    def test_alias_merges_exact_explicit_mapping_and_does_not_change_registrations(self):
        original = self.registration("Trilheros")
        self.registration("Trilheiros")
        TeamAlias.objects.create(event=self.event, source_name="Trilheros", canonical_name="Trilheiros")
        result = get_event_rankings(self.event)
        self.assertEqual([(row["name"], row["count"]) for row in result["teams"]], [("Trilheiros", 2)])
        original.refresh_from_db()
        self.assertEqual(original.team_name, "Trilheros")

    def test_city_alias_never_crosses_state_boundaries(self):
        self.registration(city="Tianguaaa", state="CE")
        self.registration(city="Tianguá", state="CE")
        self.registration(city="Tianguaaa", state="PI")
        CityAlias.objects.create(event=self.event, source_name="Tianguaaa", canonical_name="Tianguá", state="CE")
        result = get_event_rankings(self.event)
        self.assertEqual([(row["name"], row["state"], row["count"]) for row in result["cities"]], [("Tianguá", "CE", 2), ("Tianguaaa", "PI", 1)])

    def test_top_limit_and_tied_positions_preserve_full_group_totals(self):
        self.registration("Equipe A")
        self.registration("Equipe B")
        self.registration("Equipe C")
        self.registration("Equipe C")
        result = get_event_rankings(self.event, limit=2)
        self.assertEqual(result["team_group_count"], 3)
        self.assertEqual(result["team_registration_count"], 4)
        self.assertEqual([row["rank"] for row in get_event_rankings(self.event, limit=None)["teams"]], [1, 2, 2])
        self.assertEqual(len(result["teams"]), 2)

    def test_public_partial_escapes_names_and_never_contains_personal_information(self):
        self.registration('<script>alert("team")</script>')
        html = render_to_string("events/_ranking.html", {"ranking": get_event_rankings(self.event)})
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("Nome privado", html)
        self.assertNotIn("pessoa@example.test", html)

    def test_management_is_authenticated_scoped_and_requires_registration_capability(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.operator(RoleChoices.CONTENT)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.operator()
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.other_event())).status_code, 403)

    def test_save_update_delete_are_audited_and_repeated_post_is_idempotent(self):
        self.operator()
        self.assertEqual(self.correction("Trilheros", "Trilheiros").status_code, 302)
        self.assertEqual(self.correction("Trilheros", "Trilheiros").status_code, 302)
        self.assertEqual(TeamAlias.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="SAVE_RANKING_ALIAS").count(), 1)
        self.assertEqual(self.correction("Trilheros", "Equipe Serra").status_code, 302)
        alias = TeamAlias.objects.get()
        self.assertEqual(alias.canonical_name, "Equipe Serra")
        response = self.client.post(self.url(), {"action": "delete_team", "alias_id": alias.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TeamAlias.objects.exists())
        self.assertEqual(AuditLog.objects.filter(action="DELETE_RANKING_ALIAS").count(), 1)

    def test_alias_chain_resolves_and_cycles_are_rejected(self):
        self.operator()
        self.registration("Equipe A")
        self.assertEqual(self.correction("Equipe A", "Equipe B").status_code, 302)
        self.assertEqual(self.correction("Equipe B", "Equipe C").status_code, 302)
        self.assertEqual(get_event_rankings(self.event)["teams"][0]["name"], "Equipe C")
        response = self.correction("Equipe C", "Equipe A")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "cria um ciclo", status_code=400)
        self.assertEqual(TeamAlias.objects.count(), 2)

    def test_same_normalized_name_can_correct_accents_and_case(self):
        self.operator()
        self.registration("SAOJOAO")
        self.assertEqual(self.correction("SAOJOAO", "São João").status_code, 302)
        self.assertEqual(get_event_rankings(self.event)["teams"][0]["name"], "São João")

    def test_invalid_city_state_and_placeholder_names_are_rejected(self):
        self.operator()
        self.assertEqual(self.correction("Tianguá", "Tianguá", kind="city", state="XX").status_code, 400)
        self.assertEqual(self.correction("Não informado", "Equipe nova").status_code, 400)
        self.assertFalse(CityAlias.objects.exists())
        self.assertFalse(TeamAlias.objects.exists())

    def test_alias_save_and_delete_roll_back_when_audit_fails(self):
        self.operator()
        with patch("apps.events.views_rankings.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.correction("Equipe A", "Equipe B")
        self.assertFalse(TeamAlias.objects.exists())
        alias = TeamAlias.objects.create(event=self.event, source_name="Equipe A", canonical_name="Equipe B")
        with patch("apps.events.views_rankings.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.correction("Equipe A", "Equipe C")
        alias.refresh_from_db()
        self.assertEqual(alias.canonical_name, "Equipe B")
        with patch("apps.events.views_rankings.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url(), {"action": "delete_team", "alias_id": alias.pk})
        self.assertTrue(TeamAlias.objects.filter(pk=alias.pk).exists())

    def test_cannot_delete_another_event_alias_and_post_requires_csrf(self):
        user = self.operator()
        alias = TeamAlias.objects.create(event=self.other_event(), source_name="A", canonical_name="B")
        self.assertEqual(self.client.post(self.url(), {"action": "delete_team", "alias_id": alias.pk}).status_code, 404)
        self.assertTrue(TeamAlias.objects.filter(pk=alias.pk).exists())
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(user)
        self.assertEqual(secure_client.post(self.url(), {"action": "save_team"}).status_code, 403)

    def test_database_uniqueness_uses_normalized_key_within_event_and_state(self):
        TeamAlias.objects.create(event=self.event, source_name="São João", canonical_name="São João")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TeamAlias.objects.create(event=self.event, source_name="SAOJOAO", canonical_name="Outra")
        CityAlias.objects.create(event=self.event, source_name="São João", state="CE", canonical_name="São João")
        CityAlias.objects.create(event=self.event, source_name="SAOJOAO", state="PI", canonical_name="São João")
        self.assertEqual(CityAlias.objects.count(), 2)

    def test_public_snapshot_is_aggregate_only_not_cached_and_read_only(self):
        self.registration()
        url = reverse("events:ranking_snapshot", kwargs={"slug": self.event.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(set(data), {"confirmed_count", "team_group_count", "city_group_count", "team_registration_count", "city_registration_count", "teams", "cities"})
        for row in data["teams"] + data["cities"]:
            self.assertEqual(set(row), {"name", "state", "count", "rank"})
        self.assertNotContains(response, "Nome privado")
        self.assertNotContains(response, "pessoa@example.test")
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_public_snapshot_protects_draft_and_unknown_event_slug(self):
        self.registration()
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=["status"])
        url = reverse("events:ranking_snapshot", kwargs={"slug": self.event.slug})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.operator(RoleChoices.FINANCE)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 200)
        unknown = reverse("events:ranking_snapshot", kwargs={"slug": "nao-existe-ranking"})
        self.assertEqual(self.client.get(unknown).status_code, 404)

    def test_public_snapshot_recalculates_after_refund_and_cancellation(self):
        first = self.registration("Equipe A")
        second = self.registration("Equipe B")
        url = reverse("events:ranking_snapshot", kwargs={"slug": self.event.slug})
        self.assertEqual(self.client.get(url).json()["confirmed_count"], 2)
        Order.objects.filter(pk=first.source_order_line.order_id).update(status=OrderStatus.REFUNDED)
        result = self.client.get(url).json()
        self.assertEqual(result["confirmed_count"], 1)
        self.assertEqual([row["name"] for row in result["teams"]], ["Equipe B"])
        second.commercial_state = CommercialState.CANCELED
        second.save(update_fields=["commercial_state"])
        result = self.client.get(url).json()
        self.assertEqual(result["confirmed_count"], 0)
        self.assertEqual(result["teams"], [])
        self.assertEqual(result["cities"], [])
