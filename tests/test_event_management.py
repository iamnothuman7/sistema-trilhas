import io
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CapacityResource, Product, ProductVariant, Lot
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import Organization, AuditLog
from apps.events.models import Event, EventTheme, EventStatus, EventPageBlock
from apps.finance.models import FinancialEntry
from apps.identity.models import User, Person, EventMembership, RoleChoices
from apps.registrations.models import Registration, CommercialState
from apps.reporting.selectors import get_event_kpi_summary


class EventManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Organização de teste")
        dates = dict(starts_at=timezone.now()+timedelta(days=20), ends_at=timezone.now()+timedelta(days=21),
                     registration_opens_at=timezone.now()-timedelta(days=2), registration_closes_at=timezone.now()+timedelta(days=19))
        cls.event = Event.objects.create(organization=cls.org, title="Edição de teste", slug="edicao-de-teste", max_attendees=100, **dates)
        cls.other = Event.objects.create(organization=cls.org, title="Outra edição", slug="outra-edicao", **dates)
        cls.manager = User.objects.create_user(email="manager@example.test", full_name="Gestor")
        EventMembership.objects.create(user=cls.manager, event=cls.event, role_code=RoleChoices.EVENT_MANAGER)
        cls.admin = User.objects.create_user(email="global@example.test", full_name="Gestão geral")
        EventMembership.objects.create(user=cls.admin, event=None, role_code=RoleChoices.EVENT_MANAGER)
        cls.capacity = CapacityResource.objects.create(event=cls.event, type="EVENT_OVERALL", capacity_total=100, reserved_count=3, committed_count=7)

    def setUp(self):
        self.client.force_login(self.manager)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def payload(self, **changes):
        data = dict(title="Edição revisada", slug="edicao-revisada", organization=str(self.org.pk), edition_number="2", type="TRILHAO",
                    status="PUBLISHED", city="Tianguá", state="ce", venue="Praça de encontro", timezone="America/Fortaleza",
                    starts_at="2027-04-18T07:00", ends_at="2027-04-18T18:00", registration_opens_at="2027-01-01T08:00",
                    registration_closes_at="2027-04-17T20:00", max_attendees="120", description="Informações reais da edição",
                    rules_summary="Orientações da organização", contact_email="equipe@example.test", contact_phone="",
                    version=self.event.updated_at.isoformat())
        data.update(changes)
        return data

    def edit(self, **changes):
        return self.client.post(reverse("gestao:event_edit", args=[self.event.pk]), self.payload(**changes))

    def image(self, name="arte.png"):
        output = io.BytesIO()
        Image.new("RGBA", (12, 10), (230, 100, 20, 100)).save(output, format="PNG")
        return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")

    def test_scoped_manager_edits_dates_contact_and_capacity_without_changing_address(self):
        before_slug, before_subdomain = self.event.slug, self.event.subdomain
        response = self.edit()
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.capacity.refresh_from_db()
        self.assertEqual(self.event.title, "Edição revisada")
        self.assertEqual(self.event.state, "CE")
        self.assertEqual(self.event.slug, before_slug)
        self.assertEqual(self.event.subdomain, before_subdomain)
        self.assertEqual(self.event.starts_at, datetime(2027, 4, 18, 10, tzinfo=dt_timezone.utc))
        self.assertEqual(self.capacity.capacity_total, 120)
        self.assertEqual((self.capacity.reserved_count, self.capacity.committed_count), (3, 7))
        self.assertTrue(AuditLog.objects.filter(action="EVENT_UPDATED", event_id=self.event.id).exists())

    def test_invalid_numbers_dates_choices_and_contacts_return_field_errors(self):
        cases = [({"max_attendees": "abc"}, "max_attendees"), ({"max_attendees": "0"}, "max_attendees"),
                 ({"status": "INVENTADO"}, "status"), ({"state": "XX"}, "state"),
                 ({"ends_at": "2027-04-17T18:00"}, "ends_at"),
                 ({"registration_closes_at": "2026-12-31T20:00"}, "registration_closes_at"),
                 ({"registration_closes_at": "2027-04-19T20:00"}, "registration_closes_at"),
                 ({"timezone": "unknown/zone"}, "timezone"), ({"contact_email": "email inválido"}, "contact_email")]
        for changes, field in cases:
            with self.subTest(field=field, changes=changes):
                response = self.edit(**changes)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Edição de teste")

    def test_cannot_reduce_capacity_below_existing_holds_and_commitments(self):
        response = self.edit(max_attendees="9")
        self.assertIn("max_attendees", response.context["form"].errors)
        self.capacity.refresh_from_db()
        self.assertEqual(self.capacity.capacity_total, 100)
        self.assertEqual(get_event_kpi_summary(self.event)["vacancies_left"], 90)

    def test_timezone_is_used_for_datetime_input_and_roundtrip(self):
        response = self.edit(timezone="America/Manaus", starts_at="2027-04-18T09:30")
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, datetime(2027, 4, 18, 13, 30, tzinfo=dt_timezone.utc))
        response = self.client.get(reverse("gestao:event_edit", args=[self.event.pk]))
        self.assertContains(response, 'value="2027-04-18T09:30"')

    def test_capacity_counts_legacy_active_registrations_in_addition_to_new_reservations(self):
        self.capacity.committed_count = 0
        self.capacity.save(update_fields=["committed_count"])
        person = Person.objects.create(full_name="Titular legado")
        order = Order.objects.create(event=self.event, merchant_reference="LEGACY-CAPACITY", purchaser_name=person.full_name,
                    purchaser_email="legado@example.test", purchaser_phone="", status=OrderStatus.PAID,
                    expires_at=timezone.now(), total_cents=120000)
        for index in range(10):
            line = OrderLine.objects.create(order=order, intended_person_name=person.full_name, unit_cents=12000, net_cents=12000)
            Registration.objects.create(event=self.event, person=person, source_order_line=line, registration_number=index+1,
                                        commercial_state=CommercialState.ACTIVE)
        self.assertEqual(get_event_kpi_summary(self.event)["vacancies_left"], 87)
        response = self.edit(max_attendees="10")
        self.assertIn("13 vagas", str(response.context["form"].errors["max_attendees"]))
        self.assertEqual(self.edit(max_attendees="13").status_code, 302)
        self.assertEqual(get_event_kpi_summary(self.event)["vacancies_left"], 0)
        self.capacity.refresh_from_db()
        self.assertEqual((self.capacity.reserved_count, self.capacity.committed_count), (3, 0))

    def test_create_requires_global_role_and_forces_draft_with_theme_and_capacity(self):
        url = reverse("gestao:event_create")
        self.assertEqual(self.client.post(url, self.payload()).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(url, self.payload())
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(slug="edicao-revisada")
        self.assertEqual(event.status, EventStatus.DRAFT)
        self.assertEqual(event.theme.primary_color, "#f97316")
        self.assertEqual(CapacityResource.objects.get(event=event).capacity_total, 120)
        self.assertFalse(event.products.exists())

    def test_new_slug_conflict_and_invalid_create_do_not_write_partial_records(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("gestao:event_create"), self.payload(slug=self.event.slug))
        self.assertIn("slug", response.context["form"].errors)
        self.assertEqual(Event.objects.count(), 2)

    def test_event_write_and_capacity_roll_back_if_audit_fails(self):
        with patch("apps.core.views_gestao.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.edit()
        self.event.refresh_from_db()
        self.capacity.refresh_from_db()
        self.assertEqual(self.event.title, "Edição de teste")
        self.assertEqual(self.capacity.capacity_total, 100)

    def test_stale_tab_cannot_overwrite_event_or_capacity(self):
        url = reverse("gestao:event_edit", args=[self.event.pk])
        opened = self.client.get(url)
        version = opened.context["form"]["version"].value()
        self.assertContains(opened, 'type="hidden" name="version"')
        old_tab = self.payload(version=version, title="Título da aba antiga", max_attendees="150")
        self.assertEqual(self.edit(version=version, title="Título salvo primeiro").status_code, 302)
        response = self.client.post(url, old_tab)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Reabrir dados atuais", status_code=409)
        self.assertContains(response, "Título da aba antiga", status_code=409)
        self.event.refresh_from_db()
        self.capacity.refresh_from_db()
        self.assertEqual(self.event.title, "Título salvo primeiro")
        self.assertEqual(self.capacity.capacity_total, 120)
        self.assertEqual(AuditLog.objects.filter(action="EVENT_UPDATED", event_id=self.event.id).count(), 1)
        current = self.client.get(url).context["form"]["version"].value()
        self.assertNotEqual(current, version)
        self.assertEqual(self.edit(version=current, title="Revisão após reabrir").status_code, 302)

    def test_missing_or_invalid_edit_version_does_not_write(self):
        for value in ("", "versao-invalida"):
            with self.subTest(version=value):
                response = self.edit(version=value)
                self.assertEqual(response.status_code, 409)
                self.assertTrue(response.context["form"].non_field_errors())
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Edição de teste")
        self.assertFalse(AuditLog.objects.filter(action="EVENT_UPDATED", event_id=self.event.id).exists())

    def test_field_validation_keeps_version_available_for_correction(self):
        version = self.event.updated_at.isoformat()
        invalid = self.edit(version=version, max_attendees="abc")
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(invalid.context["form"].version_conflict)
        self.assertEqual(invalid.context["form"]["version"].value(), version)
        self.assertEqual(self.edit(version=version, max_attendees="125").status_code, 302)

    def test_cross_event_edit_and_appearance_are_denied(self):
        for name in ["event_edit", "event_appearance"]:
            response = self.client.post(reverse("gestao:"+name, args=[self.other.pk]), self.payload())
            self.assertEqual(response.status_code, 403)

    def test_content_role_can_upload_banner_and_logo_but_cannot_edit_event(self):
        EventMembership.objects.filter(user=self.manager).update(role_code=RoleChoices.CONTENT)
        self.assertEqual(self.edit().status_code, 403)
        response = self.client.post(reverse("gestao:event_appearance", args=[self.event.pk]),
                                    {"hero_banner": self.image("banner.png"), "logo_image": self.image("logo.png")})
        self.assertEqual(response.status_code, 302)
        theme = EventTheme.objects.get(event=self.event)
        self.assertTrue(theme.hero_banner.storage.exists(theme.hero_banner.name))
        self.assertTrue(theme.logo_image.storage.exists(theme.logo_image.name))

    def test_invalid_upload_does_not_write_theme_or_media(self):
        response = self.client.post(reverse("gestao:event_appearance", args=[self.event.pk]),
                                    {"hero_banner": SimpleUploadedFile("bad.png", b"not an image", content_type="image/png")})
        self.assertIn("hero_banner", response.context["form"].errors)
        self.assertFalse(EventTheme.objects.filter(event=self.event).exists())
        self.assertEqual(list(Path(self.media.name).rglob("*.png")), [])

    def test_appearance_audit_failure_removes_only_new_files(self):
        theme = EventTheme.objects.create(event=self.event, logo_image=self.image("existing.png"))
        original = theme.logo_image.name
        with patch("apps.core.views_gestao.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("gestao:event_appearance", args=[self.event.pk]), {"logo_image": self.image()})
        theme.refresh_from_db()
        self.assertEqual(theme.logo_image.name, original)
        self.assertEqual(len(list(Path(self.media.name).rglob("*.png"))), 1)

    def test_clearing_logo_keeps_original_file_and_preserves_banner(self):
        theme = EventTheme.objects.create(event=self.event, logo_image=self.image("existing.png"), hero_banner=self.image("banner.png"))
        original, banner = theme.logo_image.name, theme.hero_banner.name
        response = self.client.post(reverse("gestao:event_appearance", args=[self.event.pk]), {"logo_image-clear": "on"})
        self.assertEqual(response.status_code, 302)
        theme.refresh_from_db()
        self.assertFalse(theme.logo_image)
        self.assertEqual(theme.hero_banner.name, banner)
        self.assertTrue(theme.hero_banner.storage.exists(original))

    def test_duplicate_preserves_offer_configuration_and_assets_without_copying_history(self):
        self.event.timezone = "America/Manaus"
        self.event.contact_email = "edicao@example.test"
        self.event.contact_phone = ""
        self.event.status = EventStatus.PUBLISHED
        self.event.save()
        theme = EventTheme.objects.create(event=self.event, logo_image=self.image("logo.png"),
                    hero_banner=self.image("banner.png"), text_color="#faf5f0", custom_css=".edition {color: inherit}")
        pilot = Product.objects.create(event=self.event, name="Piloto", default_benefits_json=["KIT", "MEAL"], includes_meal=False)
        companion = Product.objects.create(event=self.event, name="Acompanhante", kind="ACOMPANHANTE", active=False)
        variant = ProductVariant.objects.create(product=pilot, kind="SHIRT_SIZE", code="XG", label="Extra grande", extra_price_cents=500)
        dates = dict(opens_at=self.event.registration_opens_at, closes_at=self.event.registration_closes_at)
        pilot_lot = Lot.objects.create(event=self.event, product=pilot, title="Piloto R$120", price_cents=12000,
                                      max_quantity=45, current_sold=7, priority=2, **dates)
        Lot.objects.create(event=self.event, product=companion, title="Acompanhante desativado", price_cents=6000, active=False, **dates)
        EventPageBlock.objects.create(event=self.event, page_slug="home", section_key="about", title="Sobre a edição",
                                      content_json={"text": "Informações da organização"}, visible=False)
        person = Person.objects.create(full_name="Titular do histórico", email="historico@example.test")
        order = Order.objects.create(event=self.event, merchant_reference="HISTORY-DUPLICATION", purchaser_name=person.full_name,
                    purchaser_email=person.email, purchaser_phone="", status=OrderStatus.PAID,
                    expires_at=timezone.now(), gross_cents=12000, total_cents=12000)
        line = OrderLine.objects.create(order=order, product=pilot, lot=pilot_lot, intended_person_name=person.full_name,
                                      unit_cents=12000, net_cents=12000, item_snapshot_json={"price_cents": 12000})
        Registration.objects.create(event=self.event, person=person, source_order_line=line, registration_number=1,
                                    commercial_state=CommercialState.ACTIVE)
        FinancialEntry.objects.create(event=self.event, order=order, kind="RECEITA_INSCRICAO", amount_cents_signed=12000,
                                      source_reference="historical", description="Histórico preservado")
        url = reverse("gestao:event_duplicate", args=[self.event.pk])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        duplicate = Event.objects.exclude(pk__in=[self.event.pk, self.other.pk]).get()
        self.assertEqual(duplicate.status, EventStatus.DRAFT)
        for field in ["organization_id", "timezone", "contact_email", "contact_phone", "max_attendees"]:
            self.assertEqual(getattr(duplicate, field), getattr(self.event, field))
        self.assertNotEqual(duplicate.subdomain, self.event.subdomain)
        for field in ["starts_at", "ends_at", "registration_opens_at", "registration_closes_at"]:
            self.assertEqual(getattr(duplicate, field), getattr(self.event, field) + timedelta(days=365))
        self.assertEqual(duplicate.theme.logo_image.name, theme.logo_image.name)
        self.assertEqual(duplicate.theme.hero_banner.name, theme.hero_banner.name)
        self.assertEqual(duplicate.theme.text_color, theme.text_color)
        self.assertEqual(duplicate.theme.custom_css, theme.custom_css)
        self.assertTrue(theme.logo_image.storage.exists(theme.logo_image.name))
        self.assertEqual(len(list(Path(self.media.name).rglob("*.png"))), 2)
        copied_pilot = duplicate.products.get(name="Piloto")
        self.assertEqual(copied_pilot.default_benefits_json, pilot.default_benefits_json)
        self.assertFalse(copied_pilot.includes_meal)
        copied_variant = copied_pilot.variants.get(code=variant.code)
        self.assertEqual((copied_variant.kind, copied_variant.label, copied_variant.extra_price_cents),
                         (variant.kind, variant.label, variant.extra_price_cents))
        self.assertFalse(duplicate.products.get(name="Acompanhante").active)
        self.assertFalse(duplicate.lots.get(title="Acompanhante desativado").active)
        copied_lot = duplicate.lots.get(title=pilot_lot.title)
        self.assertEqual((copied_lot.price_cents, copied_lot.max_quantity, copied_lot.current_sold, copied_lot.priority), (12000, 45, 0, 2))
        self.assertEqual(copied_lot.opens_at, pilot_lot.opens_at + timedelta(days=365))
        self.assertEqual(copied_lot.closes_at, pilot_lot.closes_at + timedelta(days=365))
        self.assertFalse(duplicate.page_blocks.get(section_key="about").visible)
        cap = duplicate.capacity_resources.get()
        self.assertEqual((cap.capacity_total, cap.reserved_count, cap.committed_count), (100, 0, 0))
        self.assertFalse(duplicate.orders.exists())
        self.assertFalse(duplicate.registrations.exists())
        self.assertFalse(duplicate.financial_entries.exists())
        self.assertEqual((Order.objects.count(), Registration.objects.count(), FinancialEntry.objects.count(), Person.objects.count()), (1, 1, 1, 1))
        order.refresh_from_db()
        pilot_lot.refresh_from_db()
        self.capacity.refresh_from_db()
        self.assertEqual((order.status, order.total_cents, pilot_lot.current_sold), (OrderStatus.PAID, 12000, 7))
        self.assertEqual((self.capacity.reserved_count, self.capacity.committed_count), (3, 7))
        self.assertTrue(AuditLog.objects.filter(event_id=duplicate.pk, action="EVENT_DUPLICATED").exists())
