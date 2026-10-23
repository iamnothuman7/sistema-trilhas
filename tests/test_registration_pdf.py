import hashlib
from datetime import timedelta
from unittest.mock import patch

import qrcode
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import Organization
from apps.events.models import DocumentKind, Event, EventStatus, LegalDocumentVersion
from apps.identity.models import EventMembership, Person, RoleChoices, User, UserPersonLink
from apps.registrations.eligibility import credential_is_available, eligibility_reason
from apps.registrations.models import CommercialState, Entitlement, OperationalState, Registration, RegistrationCredential, TermsAcceptance
from apps.registrations.pdf import document_state


class RegistrationPdfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        org = Organization.objects.create(name="Organização de teste PDF")
        cls.event = Event.objects.create(organization=org, title="Trilhão de Teste", slug="comprovante-teste",
            status=EventStatus.PUBLISHED, starts_at=now+timedelta(days=10), ends_at=now+timedelta(days=11),
            registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(days=9))
        cls.buyer = User.objects.create_user(email="pdf-buyer@example.test", full_name="Comprador de teste")
        cls.holder = User.objects.create_user(email="pdf-holder@example.test", full_name="Titular de teste")
        cls.other = User.objects.create_user(email="pdf-other@example.test", full_name="Outra pessoa")
        cls.person = Person.objects.create(full_name="Participante de teste", email=cls.other.email)
        cls.order = Order.objects.create(event=cls.event, merchant_reference="PDF-TEST", purchaser_user=cls.buyer,
            purchaser_name="Comprador de teste", purchaser_email=cls.buyer.email, purchaser_phone="000000000",
            gross_cents=12000, total_cents=12000, status=OrderStatus.PAID, expires_at=now+timedelta(hours=1))
        line = OrderLine.objects.create(order=cls.order, intended_person_name=cls.person.full_name, unit_cents=12000, net_cents=12000)
        cls.reg = Registration.objects.create(event=cls.event, person=cls.person, source_order_line=line, registration_number=27,
            commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.READY)
        UserPersonLink.objects.create(user=cls.holder, person=cls.person)
        cls.document = LegalDocumentVersion.objects.create(event=cls.event, kind=DocumentKind.REGULAMENTO, version_number=1, content="Regulamento de teste")
        TermsAcceptance.objects.create(registration=cls.reg, document_version=cls.document, accepting_user=cls.holder, evidence_hash="a"*64)
        cls.token = "a56b18c092d34e77aa11223344556677"
        cls.credential = RegistrationCredential.objects.create(registration=cls.reg, raw_token=cls.token,
            token_digest=hashlib.sha256(cls.token.encode()).hexdigest())
        Entitlement.objects.create(registration=cls.reg, benefit_type="KIT", title="Kit do participante", quantity_total=2, quantity_used=1, state="PARTIALLY_USED")

    def url(self):
        return reverse("minha_conta:registration_pdf", kwargs={"registration_id": self.reg.pk})

    def current(self):
        return Registration.objects.select_related("event", "person", "credential", "source_order_line__order").get(pk=self.reg.pk)

    def assert_pdf(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn('attachment; filename="comprovante-inscricao-27.pdf"', response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_holder_buyer_and_original_guest_session_can_download_but_email_match_cannot(self):
        for user in (self.holder, self.buyer):
            self.client.force_login(user)
            self.assert_pdf(self.client.get(self.url()))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        session = self.client.session; session["checkout_order_ids"] = [str(self.order.pk)]; session.save()
        self.assert_pdf(self.client.get(self.url()))
        session = self.client.session; session["checkout_order_ids"] = ["f0000000-0000-0000-0000-000000000001"]; session.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_event_operator_authorization_does_not_leak_across_events_or_finance_role(self):
        self.client.force_login(self.other)
        member = EventMembership.objects.create(user=self.other, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.assert_pdf(self.client.get(self.url()))
        member.role_code = RoleChoices.FINANCE; member.save(update_fields=["role_code"])
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        member.role_code = RoleChoices.REGISTRATION_AGENT; member.revoked_at = timezone.now(); member.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        other_event = Event.objects.create(organization=self.event.organization, title="Outra edição", slug="pdf-outra",
            starts_at=self.event.starts_at, ends_at=self.event.ends_at,
            registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        EventMembership.objects.create(user=self.other, event=other_event, role_code=RoleChoices.EVENT_MANAGER)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_pdf_reuses_current_token_without_writing_or_creating_credentials(self):
        self.client.force_login(self.holder)
        with patch("apps.registrations.pdf.qrcode.QRCode", wraps=qrcode.QRCode) as qr:
            self.assert_pdf(self.client.get(self.url()))
            self.assertEqual(qr.call_count, 1)
        self.reg.refresh_from_db(); self.credential.refresh_from_db()
        self.assertEqual(self.reg.operational_state, OperationalState.READY)
        self.assertEqual(RegistrationCredential.objects.count(), 1)
        self.assertEqual(self.credential.raw_token, self.token)
        self.assertEqual(TermsAcceptance.objects.count(), 1)
        self.assertEqual(self.client.post(self.url()).status_code, 405)

    def test_pending_canceled_transferred_and_refunded_documents_never_emit_qr(self):
        self.client.force_login(self.buyer)
        cases = [(CommercialState.RESERVED, OrderStatus.AWAITING_PAYMENT), (CommercialState.CANCELED, OrderStatus.PAID),
                 (CommercialState.TRANSFERRED, OrderStatus.PAID), (CommercialState.ACTIVE, OrderStatus.REFUNDED)]
        for commercial, order_status in cases:
            with self.subTest(commercial=commercial, order_status=order_status):
                Registration.objects.filter(pk=self.reg.pk).update(commercial_state=commercial)
                Order.objects.filter(pk=self.order.pk).update(status=order_status)
                self.assertFalse(document_state(self.current())["confirmed"])
                with patch("apps.registrations.pdf.qrcode.QRCode") as qr:
                    self.assert_pdf(self.client.get(self.url()))
                    qr.assert_not_called()

    def test_current_terms_and_active_holder_link_are_required_even_if_already_ready(self):
        self.client.force_login(self.buyer)
        new = LegalDocumentVersion.objects.create(event=self.event, kind=DocumentKind.REGULAMENTO, version_number=2, content="Nova versão")
        self.assertFalse(credential_is_available(self.current()))
        with patch("apps.registrations.pdf.qrcode.QRCode") as qr:
            self.assert_pdf(self.client.get(self.url())); qr.assert_not_called()
        TermsAcceptance.objects.create(registration=self.reg, document_version=new, accepting_user=self.holder, evidence_hash="b"*64)
        self.assertTrue(credential_is_available(self.current()))
        UserPersonLink.objects.filter(user=self.holder).delete()
        self.assertFalse(credential_is_available(self.current()))

    def test_revoked_expired_or_missing_credentials_are_not_reissued_by_pdf(self):
        self.client.force_login(self.holder)
        for updates in ({"revoked_at":timezone.now()}, {"revoked_at":None,"expires_at":timezone.now()-timedelta(seconds=1)}):
            RegistrationCredential.objects.filter(pk=self.credential.pk).update(**updates)
            with patch("apps.registrations.pdf.qrcode.QRCode") as qr:
                self.assert_pdf(self.client.get(self.url())); qr.assert_not_called()
        RegistrationCredential.objects.all().delete()
        self.assert_pdf(self.client.get(self.url()))
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_finished_event_keeps_valid_credential_but_canceled_and_archived_block_it(self):
        Event.objects.filter(pk=self.event.pk).update(status=EventStatus.FINISHED)
        self.assertTrue(credential_is_available(self.current()))
        for status in (EventStatus.CANCELED, EventStatus.ARCHIVED):
            Event.objects.filter(pk=self.event.pk).update(status=status)
            self.assertFalse(credential_is_available(self.current()))
            self.assertIn("cancelado ou arquivado", eligibility_reason(self.current()))

    def test_document_buttons_and_shared_instructions_are_available_in_account(self):
        self.client.force_login(self.holder)
        for name in ("dashboard", "registration_detail", "credential_view"):
            kwargs = {} if name == "dashboard" else {"registration_id":self.reg.pk}
            response = self.client.get(reverse("minha_conta:"+name, kwargs=kwargs))
            self.assertContains(response, self.url())
            if name != "dashboard": self.assertContains(response, "Credenciamento e retirada")
