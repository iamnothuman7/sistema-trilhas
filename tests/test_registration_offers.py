from datetime import timedelta
from io import StringIO
import json
import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CapacityHold, CapacityResource, HoldStatus, Lot, Product, ProductKind, ProductVariant
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import AuditLog
from apps.events.models import Event, EventStatus, MotorcycleRaffle
from apps.identity.models import EventMembership, RoleChoices, User
from apps.payments.models import ChargeAttempt
from tests import test_system as _fixtures


class RegistrationOfferTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def run_plan(self, apply=False):
        output = StringIO()
        call_command("configure_registration_120", event=self.event.slug, apply=apply, stdout=output)
        return json.loads(output.getvalue())

    def companion(self):
        product = Product.objects.create(event=self.event, name="Acompanhante", kind=ProductKind.ACOMPANHANTE)
        lot = Lot.objects.create(event=self.event, product=product, title="Acompanhante R$60", price_cents=6000,
            opens_at=self.lot.opens_at, closes_at=self.lot.closes_at)
        return product, lot

    def checkout_url(self):
        return reverse("checkout:start_registration", kwargs={"event_slug": self.event.slug})

    def post_data(self, **changes):
        values = {"purchaser_name": "Responsável", "purchaser_email": "buyer@example.test", "purchaser_phone": "88999999999",
            "purchaser_document": "00000000191", "participant_name[]": ["Piloto da edição"],
            "participant_product[]": [str(self.product.pk)], "participant_lot[]": [str(self.lot.pk)],
            "participant_shirt[]": ["G"], "participant_city[]": ["Ubajara"], "participant_state[]": ["CE"]}
        values.update(changes)
        return values

    def old_order(self, status):
        order = Order.objects.create(event=self.event, merchant_reference="OLD-"+status,
            purchaser_name="Histórico", purchaser_email="history@example.test", purchaser_phone="88999999999",
            status=status, expires_at=timezone.now()+timedelta(hours=1), gross_cents=18000, total_cents=18000)
        OrderLine.objects.create(order=order, product=self.product, lot=self.lot, intended_person_name="Histórico",
            unit_cents=18000, net_cents=18000, item_snapshot_json={"product_name": "Piloto", "price_cents": 18000})
        return order

    def test_dry_run_is_read_only_and_lists_actual_changes(self):
        companion, companion_lot = self.companion()
        plan = self.run_plan()
        self.assertEqual(plan["mode"], "dry-run")
        self.assertEqual(plan["lot_changes"][0]["before_cents"], 18000)
        self.assertEqual(plan["lot_changes"][0]["after_cents"], 12000)
        self.assertEqual(plan["deactivate_products"][0]["id"], str(companion.pk))
        self.lot.refresh_from_db()
        companion.refresh_from_db()
        self.assertEqual(self.lot.price_cents, 18000)
        self.assertTrue(companion.active)
        self.assertEqual(companion_lot.price_cents, 6000)
        self.assertFalse(AuditLog.objects.exists())

    def test_apply_updates_current_and_future_pilot_offers_only_and_is_idempotent(self):
        companion, companion_lot = self.companion()
        future = Lot.objects.create(event=self.event, product=self.product, title="Próximo lote", price_cents=20000,
            opens_at=timezone.now()+timedelta(days=1), closes_at=self.lot.closes_at)
        expired = Lot.objects.create(event=self.event, product=self.product, title="Encerrado", price_cents=17000,
            opens_at=timezone.now()-timedelta(days=3), closes_at=timezone.now()-timedelta(days=1))
        self.run_plan(apply=True)
        for lot in (self.lot, future):
            lot.refresh_from_db()
            self.assertEqual(lot.price_cents, 12000)
        companion.refresh_from_db()
        companion_lot.refresh_from_db()
        expired.refresh_from_db()
        self.assertFalse(companion.active)
        self.assertEqual(companion_lot.price_cents, 6000)
        self.assertEqual(expired.price_cents, 17000)
        second = self.run_plan(apply=True)
        self.assertEqual(second["lot_changes"], [])
        self.assertEqual(second["deactivate_products"], [])
        self.assertEqual(AuditLog.objects.filter(action="REGISTRATION_OFFER_120_CONFIGURED").count(), 1)

    def test_paid_and_pending_orders_keep_values_and_snapshots_new_order_uses_120(self):
        paid = self.old_order(OrderStatus.PAID)
        pending = self.old_order(OrderStatus.RESERVED)
        before = list(OrderLine.objects.filter(order__in=[paid, pending]).order_by("pk").values())
        self.run_plan(apply=True)
        response = self.client.post(self.checkout_url(), self.post_data(price_cents="1"))
        self.assertEqual(response.status_code, 302)
        new = Order.objects.exclude(pk__in=[paid.pk, pending.pk]).get()
        self.assertEqual(new.gross_cents, 12000)
        self.assertEqual(new.total_cents, 12000)
        self.assertEqual(new.lines.get().unit_cents, 12000)
        self.assertEqual(new.lines.get().item_snapshot_json["price_cents"], 12000)
        for old in (paid, pending):
            old.refresh_from_db()
            self.assertEqual(old.total_cents, 18000)
        self.assertEqual(list(OrderLine.objects.filter(order__in=[paid, pending]).order_by("pk").values()), before)
        self.assertFalse(ChargeAttempt.objects.exists())

    def test_audit_failure_rolls_back_price_and_activation(self):
        companion, _ = self.companion()
        with patch("apps.core.management.commands.configure_registration_120.log_audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.run_plan(apply=True)
        self.lot.refresh_from_db()
        companion.refresh_from_db()
        self.assertEqual(self.lot.price_cents, 18000)
        self.assertTrue(companion.active)

    def test_inactive_product_and_lot_are_rejected_even_with_forged_ids(self):
        companion, companion_lot = self.companion()
        self.run_plan(apply=True)
        response = self.client.post(self.checkout_url(), self.post_data(**{
            "participant_product[]": [str(companion.pk)], "participant_lot[]": [str(companion_lot.pk)]}))
        self.assertContains(response, "O pacote ou lote escolhido não está disponível")
        self.lot.active = False
        self.lot.save(update_fields=["active"])
        response = self.client.post(self.checkout_url(), self.post_data())
        self.assertContains(response, "Inscrições indisponíveis no momento")
        self.assertFalse(Order.objects.exists())

    def test_closed_future_inactive_and_sold_out_lots_do_not_appear(self):
        for title, changes in (
            ("Expirado exclusivo", {"closes_at": timezone.now()-timedelta(days=1)}),
            ("Futuro exclusivo", {"opens_at": timezone.now()+timedelta(days=1)}),
            ("Inativo exclusivo", {"active": False}),
            ("Esgotado exclusivo", {"max_quantity": 1, "current_sold": 1}),
        ):
            values = {"event": self.event, "product": self.product, "title": title,
                "price_cents": 99900, "opens_at": self.lot.opens_at, "closes_at": self.lot.closes_at}
            Lot.objects.create(**(values | changes))
        self.run_plan(apply=True)
        for url in (self.checkout_url(), reverse("events:detail", kwargs={"slug": self.event.slug})):
            response = self.client.get(url)
            self.assertContains(response, "120,00")
            for title in ("Expirado exclusivo", "Futuro exclusivo", "Inativo exclusivo", "Esgotado exclusivo"):
                self.assertNotContains(response, title)

    @override_settings(MP_ACCESS_TOKEN="", MP_PUBLIC_KEY="", MP_WEBHOOK_SECRET="")
    def test_registration_and_raffle_cards_keep_prices_and_availability_separate(self):
        self.companion()
        self.run_plan(apply=True)
        MotorcycleRaffle.objects.create(event=self.event, is_public=True)
        response = self.client.get(reverse("events:detail", kwargs={"slug": self.event.slug}))
        self.assertContains(response, "R$ 120,00")
        self.assertNotContains(response, "R$ 180,00")
        self.assertNotContains(response, "R$ 60,00")
        self.assertContains(response, "Cotas do sorteio")
        self.assertContains(response, "Em breve")
        self.assertContains(response, "Pagamento online em breve")
        self.assertNotContains(response, "Fazer inscrição")
        self.assertContains(response, reverse("events:raffle", kwargs={"slug": self.event.slug}))

    def test_event_without_raffle_does_not_announce_one(self):
        response = self.client.get(reverse("events:detail", kwargs={"slug": self.event.slug}))
        self.assertNotContains(response, "Vai ter sorteio de moto")
        self.assertNotContains(response, "Cotas do sorteio")

    def test_invalid_submission_keeps_buyer_and_participant_data(self):
        response = self.client.post(self.checkout_url(), self.post_data(purchaser_email=""))
        self.assertContains(response, 'value="Responsável"')
        self.assertContains(response, 'value="Piloto da edição"')
        self.assertContains(response, 'value="Ubajara"')
        self.assertFalse(Order.objects.exists())

    def test_no_available_offer_has_no_registration_form_or_submit(self):
        self.lot.active = False
        self.lot.save(update_fields=["active"])
        response = self.client.get(self.checkout_url())
        self.assertNotContains(response, 'id="registrationForm"')
        self.assertContains(response, "Inscrições indisponíveis no momento")

    def test_lot_with_all_places_temporarily_reserved_is_not_offered(self):
        capacity = CapacityResource.objects.create(event=self.event, type="LOT_TOTAL", reference_id=str(self.lot.pk),
            capacity_total=self.lot.max_quantity, reserved_count=self.lot.max_quantity)
        CapacityHold.objects.create(resource=capacity, order_id=uuid.uuid4(), quantity=self.lot.max_quantity,
            status=HoldStatus.ACTIVE, expires_at=timezone.now()+timedelta(minutes=20), idempotency_key="live-lot-hold")
        for url in (self.checkout_url(), reverse("events:detail", kwargs={"slug": self.event.slug})):
            response = self.client.get(url)
            self.assertContains(response, "Inscrições indisponíveis no momento")
            self.assertNotContains(response, 'id="registrationForm"')

    def test_content_editor_can_preview_own_draft_and_public_cannot(self):
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=["status"])
        url = reverse("events:detail", kwargs={"slug": self.event.slug})
        self.assertEqual(self.client.get(url).status_code, 404)
        editor = User.objects.create_user(email="editor-preview@example.test", password="local-test-only")
        self.client.force_login(editor)
        self.assertEqual(self.client.get(url).status_code, 404)
        EventMembership.objects.create(user=editor, event=self.event, role_code=RoleChoices.CONTENT)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_invalid_names_emails_state_and_lengths_create_no_order(self):
        for changes, expected in (
            ({"participant_name[]": ["   "]}, "informe um nome"),
            ({"purchaser_email": "not-an-email"}, "e-mail válido para o responsável"),
            ({"participant_email[]": ["invalid@email"]}, "e-mail válido ou deixe esse campo em branco"),
            ({"participant_state[]": ["CEARA"]}, "UF com duas letras"),
            ({"participant_team[]": ["T"*121]}, "revise o tamanho dos campos"),
        ):
            with self.subTest(changes=changes), patch("apps.checkout.views.Order.objects.create", side_effect=AssertionError("must validate before creating order")):
                response = self.client.post(self.checkout_url(), self.post_data(**changes))
                self.assertContains(response, expected)
        self.assertFalse(Order.objects.exists())

    def test_unknown_shirt_variant_is_rejected_and_email_is_preserved(self):
        ProductVariant.objects.create(product=self.product, kind="SHIRT_SIZE", code="CUSTOM_M", label="Modelo M")
        ProductVariant.objects.create(product=self.product, kind="SHIRT_SIZE", code="CUSTOM_G", label="Modelo G")
        with patch("apps.checkout.views.Order.objects.create", side_effect=AssertionError("must validate before creating order")):
            response = self.client.post(self.checkout_url(), self.post_data(**{"participant_email[]": ["pilot@example.test"], "participant_shirt[]": ["NOT_A_SIZE"]}))
        self.assertContains(response, "Escolha um tamanho de camiseta disponível")
        self.assertContains(response, 'value="pilot@example.test"')
        self.assertContains(response, 'value="CUSTOM_M"')
        self.assertContains(response, "Modelo G")
        valid = self.client.post(self.checkout_url(), self.post_data(**{"participant_shirt[]": ["CUSTOM_G"]}))
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(OrderLine.objects.get().intended_variant_code, "CUSTOM_G")

    def test_event_closing_between_display_and_lock_prevents_new_order(self):
        select_for_update = Event.objects.select_for_update

        def close_then_lock(*args, **kwargs):
            Event.objects.filter(pk=self.event.pk).update(status=EventStatus.SALES_CLOSED)
            return select_for_update(*args, **kwargs)

        with patch.object(Event.objects, "select_for_update", side_effect=close_then_lock):
            response = self.client.post(self.checkout_url(), self.post_data())
        self.assertContains(response, "As inscrições foram encerradas enquanto você preenchia os dados")
        self.assertContains(response, 'value="Piloto da edição"')
        self.assertFalse(Order.objects.exists())

    def test_expired_reservations_do_not_hide_checkout_and_public_get_does_not_write(self):
        old = self.old_order(OrderStatus.RESERVED)
        old.expires_at = timezone.now()-timedelta(minutes=1)
        old.save(update_fields=["expires_at"])
        capacity = CapacityResource.objects.create(event=self.event, type="LOT_TOTAL", reference_id=str(self.lot.pk),
            capacity_total=self.lot.max_quantity, reserved_count=self.lot.max_quantity)
        hold = CapacityHold.objects.create(resource=capacity, order_id=old.pk, quantity=self.lot.max_quantity,
            status=HoldStatus.ACTIVE, expires_at=old.expires_at, idempotency_key="expired-lot-hold")
        response = self.client.get(reverse("events:detail", kwargs={"slug": self.event.slug}))
        self.assertContains(response, self.checkout_url())
        hold.refresh_from_db()
        old.refresh_from_db()
        capacity.refresh_from_db()
        self.assertEqual(hold.status, HoldStatus.ACTIVE)
        self.assertEqual(old.status, OrderStatus.RESERVED)
        self.assertEqual(capacity.reserved_count, self.lot.max_quantity)
        self.assertEqual(self.client.get(self.checkout_url()).status_code, 200)
        hold.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(hold.status, HoldStatus.EXPIRED)
        self.assertEqual(old.status, OrderStatus.EXPIRED)

    def test_variant_extra_is_disclosed_and_added_to_server_price_and_snapshot(self):
        self.run_plan(apply=True)
        variant = ProductVariant.objects.create(product=self.product, kind="SHIRT_SIZE", code="SPECIAL",
            label="Camiseta especial", extra_price_cents=1500)
        response = self.client.get(self.checkout_url())
        self.assertContains(response, "Camiseta especial (+ R$ 15,00)")
        self.assertContains(response, 'data-extra-cents="1500"')
        response = self.client.post(self.checkout_url(), self.post_data(**{
            "participant_shirt[]": ["SPECIAL"], "extra_price_cents": "0", "total_cents": "12000"}))
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        line = order.lines.get()
        self.assertEqual((order.gross_cents, order.total_cents, line.unit_cents), (13500, 13500, 13500))
        self.assertEqual(line.variant, variant)
        self.assertEqual(line.item_snapshot_json["lot_price_cents"], 12000)
        self.assertEqual(line.item_snapshot_json["variant_extra_price_cents"], 1500)
        self.assertEqual(line.item_snapshot_json["price_cents"], 13500)
        self.assertEqual(line.item_snapshot_json["variant_label"], "Camiseta especial")

    def test_zero_extra_variant_keeps_120_and_price_command_does_not_change_variants(self):
        ordinary = ProductVariant.objects.create(product=self.product, kind="SHIRT_SIZE", code="M", label="M", extra_price_cents=0)
        optional = ProductVariant.objects.create(product=self.product, kind="SHIRT_SIZE", code="EXTRA", label="Opcional", extra_price_cents=1900)
        self.run_plan(apply=True)
        optional.refresh_from_db()
        self.assertEqual(optional.extra_price_cents, 1900)
        response = self.client.post(self.checkout_url(), self.post_data(**{"participant_shirt[]": ["M"]}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get().total_cents, 12000)
        self.assertEqual(OrderLine.objects.get().variant, ordinary)
