from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.catalog.models import ProductKind
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.identity.documents import normalize_cpf
from apps.identity.models import Person, User, UserPersonLink
from apps.mediaframes.models import GeneratedMontage
from apps.registrations.models import CommercialState, Registration
from tests import test_registration_offers as offers
from tests import test_system as fixtures


class ParticipantCpfTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp
    checkout_url = offers.RegistrationOfferTests.checkout_url
    post_data = offers.RegistrationOfferTests.post_data

    def reserve(self, cpf="529.982.247-25", **changes):
        return self.client.post(self.checkout_url(), self.post_data(**{"participant_document[]": [cpf], **changes}))

    def test_form_requires_individual_cpf_and_preserves_invalid_input(self):
        self.assertContains(self.client.get(self.checkout_url()), 'name="participant_document[]"')
        result = self.reserve("111.111.111-11")
        self.assertContains(result, "CPF válido")
        self.assertContains(result, 'value="111.111.111-11"')
        self.assertFalse(Order.objects.exists())

    def test_missing_invalid_and_non_ascii_cpfs_are_rejected(self):
        for cpf in ("", "123", "52998224724", "11111111111", "5299822472x5", "５２９９８２２４７２５"):
            with self.subTest(cpf=cpf):
                self.assertContains(self.reserve(cpf), "CPF válido")
        self.assertFalse(Order.objects.exists())

    def test_normalized_cpf_is_stored_and_cannot_repeat_in_another_order(self):
        self.assertEqual(self.reserve().status_code, 302)
        reg = Registration.objects.get()
        self.assertEqual(reg.participant_cpf, "52998224725")
        self.assertEqual(reg.person.document_number, "52998224725")
        self.assertContains(self.reserve("52998224725", purchaser_email="other@example.test"), "Já existe uma inscrição")
        self.assertEqual(Order.objects.count(), 1)

    def test_group_duplicate_is_rejected_before_any_writes(self):
        result = self.reserve(**{"participant_name[]": ["Um", "Dois"],
            "participant_document[]": ["52998224725", "529.982.247-25"]})
        self.assertContains(result, "cada CPF pode ter apenas uma inscrição")
        self.assertFalse(Order.objects.exists())

    def test_same_buyer_can_purchase_for_distinct_participants(self):
        for cpf in ("52998224725", "11144477735"):
            self.assertEqual(self.reserve(cpf).status_code, 302)
        self.assertEqual(Order.objects.count(), 2)

    def test_active_and_pending_reservations_hold_cpf(self):
        self.reserve()
        Registration.objects.update(commercial_state=CommercialState.ACTIVE)
        self.assertContains(self.reserve(), "Já existe uma inscrição")
        self.assertEqual(Registration.objects.count(), 1)

    def test_expiration_releases_cpf_and_cancels_old_registration(self):
        self.reserve()
        old = Order.objects.get()
        Order.objects.filter(pk=old.pk).update(expires_at=timezone.now()-timedelta(minutes=1))
        self.assertEqual(self.reserve().status_code, 302)
        old.refresh_from_db()
        self.assertEqual(old.status, OrderStatus.EXPIRED)
        self.assertEqual(Registration.objects.filter(commercial_state=CommercialState.RESERVED).count(), 1)
        self.assertEqual(Registration.objects.filter(commercial_state=CommercialState.CANCELED).count(), 1)

    def test_canceled_registration_can_be_replaced_but_not_reactivated(self):
        self.reserve()
        old = Registration.objects.get()
        old.commercial_state = CommercialState.CANCELED
        old.save()
        self.assertEqual(self.reserve().status_code, 302)
        old.commercial_state = CommercialState.ACTIVE
        with self.assertRaises(IntegrityError), transaction.atomic():
            old.save()

    def test_database_rejects_bypass_and_allows_same_cpf_in_another_event(self):
        self.reserve()
        old = Registration.objects.get()
        line = OrderLine.objects.create(order=old.source_order_line.order, product=self.product, unit_cents=12000, net_cents=12000)
        duplicate = Registration(event=self.event, source_order_line=line, person=old.person, participant_cpf=old.participant_cpf)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.bulk_create([duplicate])
        from apps.events.models import Event
        other = Event.objects.create(organization=self.org, title="Outra edição", slug="outra-edicao", starts_at=self.event.starts_at, ends_at=self.event.ends_at,
            registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        duplicate.event = other
        duplicate.save()
        self.assertEqual(Registration.objects.count(), 2)


class PaidPilotArtTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def pilot(self, *, status=OrderStatus.PAID, state=CommercialState.ACTIVE, kind=ProductKind.PILOTO):
        user = User.objects.create_user(email="pilot@example.test", password="test-only-123")
        order = Order.objects.create(event=self.event, merchant_reference="ART-TEST", purchaser_name="Piloto",
            purchaser_email=user.email, purchaser_user=user, status=status, expires_at=timezone.now()+timedelta(hours=1))
        line = OrderLine.objects.create(order=order, product=self.product, unit_cents=12000, net_cents=12000,
            item_snapshot_json={"kind": kind})
        person = Person.objects.create(full_name="Piloto confirmado")
        reg = Registration.objects.create(event=self.event, person=person, source_order_line=line, commercial_state=state)
        self.client.force_login(user)
        return user, order, reg

    def page(self):
        return reverse("events:frames", args=[self.event.slug])

    def photo(self):
        output = BytesIO()
        Image.new("RGB", (10, 10)).save(output, "PNG")
        return SimpleUploadedFile("photo.png", output.getvalue(), content_type="image/png")

    def test_anonymous_sees_explanation_but_no_editor_or_upload_api(self):
        response = self.client.get(self.page())
        self.assertContains(response, "pagamento confirmado")
        self.assertNotContains(response, 'id="story-canvas"')
        self.assertNotContains(response, "story_studio.js")
        self.assertEqual(self.client.post(reverse("mediaframes:remove-background")).status_code, 403)

    def test_paid_buyer_gets_editor_before_terms_and_link_from_registration(self):
        _, _, reg = self.pilot()
        self.assertContains(self.client.get(self.page()), 'id="story-canvas"')
        self.assertContains(self.client.get(reverse("minha_conta:registration_detail", args=[reg.pk])), "Criar story e feed de piloto confirmado")
        self.assertEqual(self.client.get(reverse("mediaframes:story-access", args=[reg.pk])).status_code, 200)

    def test_pending_refunded_review_and_canceled_orders_do_not_unlock(self):
        _, order, reg = self.pilot()
        for status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT, OrderStatus.REFUNDED,
                OrderStatus.PARTIALLY_REFUNDED, OrderStatus.PAID_NEEDS_REVIEW, OrderStatus.CANCELED):
            order.status = status
            order.save()
            with self.subTest(status=status):
                self.assertNotContains(self.client.get(self.page()), 'id="story-canvas"')
                self.assertEqual(self.client.get(reverse("mediaframes:story-access", args=[reg.pk])).status_code, 403)

    def test_companion_purchase_does_not_unlock_pilot_art(self):
        self.pilot(kind=ProductKind.ACOMPANHANTE)
        self.assertNotContains(self.client.get(self.page()), 'id="story-canvas"')

    def test_canceled_registration_and_other_event_do_not_unlock(self):
        _, _, reg = self.pilot(state=CommercialState.CANCELED)
        self.assertNotContains(self.client.get(self.page()), 'id="story-canvas"')
        reg.commercial_state = CommercialState.ACTIVE
        reg.save()
        self.event.status = "CANCELED"
        self.event.save()
        self.assertEqual(self.client.get(reverse("mediaframes:story-access", args=[reg.pk])).status_code, 403)

    def test_other_account_cannot_use_registration_or_montage(self):
        user, _, reg = self.pilot()
        montage = GeneratedMontage.objects.create(owner=user, registration=reg, event=self.event)
        other = User.objects.create_user(email="other@example.test", password="test-only-123")
        self.client.force_login(other)
        with patch("apps.mediaframes.views.remove_background") as inference:
            response = self.client.post(reverse("mediaframes:remove-background"), {"registration_id": reg.pk, "photo": self.photo()})
            self.assertEqual(response.status_code, 403)
            inference.assert_not_called()
        self.assertEqual(self.client.post(reverse("mediaframes:montage-download", args=[montage.pk]), {"image": self.photo()}).status_code, 403)
        self.assertEqual(self.client.post(reverse("mediaframes:montage-backup", args=[montage.pk]), {"image": self.photo()}).status_code, 404)

    def test_holder_can_create_art_and_montage_is_bound_to_purchase(self):
        _, order, reg = self.pilot()
        holder = User.objects.create_user(email="holder@example.test", password="test-only-123")
        UserPersonLink.objects.create(user=holder, person=reg.person)
        self.client.force_login(holder)
        with patch("apps.mediaframes.views.remove_background", return_value=Image.new("RGBA", (10, 10))):
            response = self.client.post(reverse("mediaframes:remove-background"), {"registration_id": reg.pk, "photo": self.photo()})
        self.assertEqual(response.status_code, 200)
        montage = GeneratedMontage.objects.get()
        self.assertEqual(montage.owner, holder)
        self.assertEqual(montage.registration, reg)
        self.assertEqual(montage.event, self.event)
        order.status = OrderStatus.REFUNDED
        order.save()
        self.assertEqual(self.client.post(reverse("mediaframes:montage-download", args=[montage.pk]), {"image": self.photo()}).status_code, 403)
        self.assertEqual(GeneratedMontage.objects.get().downloads_count, 0)
