import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import Organization
from apps.events.models import DocumentKind, Event, EventTheme, LegalDocumentVersion
from apps.identity.models import ClaimToken, Person, User, UserPersonLink
from apps.registrations.models import CommercialState, OperationalState, Registration, RegistrationCredential, TermsAcceptance


class ParticipantAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        org = Organization.objects.create(name="Organização teste")
        cls.event = Event.objects.create(organization=org, title="Edição teste", slug="participant-access",
            starts_at=now+timedelta(days=10), ends_at=now+timedelta(days=11),
            registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(days=9))
        EventTheme.objects.create(event=cls.event)
        cls.buyer = User.objects.create_user(email="buyer@example.test", full_name="Comprador")
        cls.holder = User.objects.create_user(email="holder@example.test", full_name="Participante")
        cls.outsider = User.objects.create_user(email="outsider@example.test", full_name="Outra pessoa")
        cls.person = Person.objects.create(full_name="Participante da inscrição", email=cls.outsider.email)
        product = Product.objects.create(event=cls.event, name="Piloto")
        cls.order = Order.objects.create(event=cls.event, merchant_reference="PARTICIPANT-TEST", purchaser_user=cls.buyer,
            purchaser_name="Comprador", purchaser_email=cls.buyer.email, purchaser_phone="000000000",
            total_cents=10000, gross_cents=10000, status=OrderStatus.PAID, expires_at=now+timedelta(hours=1))
        line = OrderLine.objects.create(order=cls.order, product=product, intended_person_name=cls.person.full_name,
                                        unit_cents=10000, net_cents=10000)
        cls.reg = Registration.objects.create(event=cls.event, person=cls.person, source_order_line=line,
            commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.NOT_READY, registration_number=1)
        cls.old_document = LegalDocumentVersion.objects.create(event=cls.event, kind=DocumentKind.REGULAMENTO, version_number=1, content="Regras antigas")
        cls.regulation = LegalDocumentVersion.objects.create(event=cls.event, kind=DocumentKind.REGULAMENTO, version_number=2, content="Regras vigentes")
        cls.terms = LegalDocumentVersion.objects.create(event=cls.event, kind=DocumentKind.TERMO_RESPONSABILIDADE, version_number=1, content="Responsabilidade")
        cls.future = LegalDocumentVersion.objects.create(event=cls.event, kind=DocumentKind.REGULAMENTO, version_number=3,
            content="Regras futuras", effective_at=now+timedelta(days=2))

    def url(self, name, **kwargs):
        return reverse(f"minha_conta:{name}", kwargs=kwargs or {"registration_id": self.reg.pk})

    def link_holder(self):
        UserPersonLink.objects.create(user=self.holder, person=self.person)
        self.client.force_login(self.holder)

    def complete_documents(self):
        return {"document_version_id": [str(self.regulation.pk), str(self.terms.pk)]}

    def create_link(self, *, guest=False):
        if guest:
            self.client.logout()
            session = self.client.session
            session["checkout_order_ids"] = [str(self.order.pk)]
            session.save()
        else:
            self.client.force_login(self.buyer)
        response = self.client.post(self.url("create_claim"))
        self.assertEqual(response.status_code, 200)
        claim_url = response.context["claim_url"]
        token = claim_url.rstrip("/").split("/")[-1]
        return token, self.url("claim_ticket", token=token)

    def test_dashboard_uses_buyer_or_verified_person_link_and_never_email_match(self):
        url = reverse("minha_conta:dashboard")
        self.client.force_login(self.outsider)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["registrations"]), [])
        self.client.force_login(self.buyer)
        self.assertEqual(list(self.client.get(url).context["registrations"]), [self.reg])
        self.link_holder()
        self.assertEqual(list(self.client.get(url).context["registrations"]), [self.reg])

    def test_unrelated_user_cannot_open_detail_credential_or_accept(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("registration_detail")).status_code, 404)
        self.assertEqual(self.client.get(self.url("credential_view")).status_code, 404)
        self.assertEqual(self.client.post(self.url("accept_terms"), self.complete_documents()).status_code, 403)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_buyer_can_view_but_cannot_accept_for_participant(self):
        self.client.force_login(self.buyer)
        response = self.client.get(self.url("registration_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_holder"])
        self.assertNotContains(response, 'name="document_version_id"')
        self.assertEqual(self.client.post(self.url("accept_terms"), self.complete_documents()).status_code, 403)

    def test_accept_requires_complete_current_set_and_rejects_empty_partial_or_stale(self):
        self.link_holder()
        invalid_sets = [[], [str(self.regulation.pk)], [str(self.old_document.pk), str(self.terms.pk)],
                        [str(self.future.pk), str(self.terms.pk)], ["invalid-uuid"]]
        for documents in invalid_sets:
            response = self.client.post(self.url("accept_terms"), {"document_version_id": documents})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(TermsAcceptance.objects.exists())
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.operational_state, OperationalState.NOT_READY)
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_accept_current_terms_creates_evidence_and_valid_qr_once(self):
        self.link_holder()
        response = self.client.post(self.url("accept_terms"), self.complete_documents())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TermsAcceptance.objects.count(), 2)
        self.assertTrue(all(item.accepting_user_id == self.holder.pk and len(item.evidence_hash) == 64 for item in TermsAcceptance.objects.all()))
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.operational_state, OperationalState.READY)
        credential = RegistrationCredential.objects.get(registration=self.reg)
        self.assertTrue(credential.is_valid)
        response = self.client.get(self.url("credential_view"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.client.post(self.url("accept_terms"), self.complete_documents())
        self.assertEqual(TermsAcceptance.objects.count(), 2)
        self.assertEqual(RegistrationCredential.objects.get().raw_token, credential.raw_token)

    def test_accept_does_not_replace_payment_confirmation(self):
        self.link_holder()
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.AWAITING_PAYMENT)
        Registration.objects.filter(pk=self.reg.pk).update(commercial_state=CommercialState.RESERVED)
        self.assertEqual(self.client.post(self.url("accept_terms"), self.complete_documents()).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.count(), 2)
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.operational_state, OperationalState.NOT_READY)
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_detail_get_and_accept_get_do_not_mutate_registration(self):
        self.link_holder()
        for doc in (self.regulation, self.terms):
            TermsAcceptance.objects.create(registration=self.reg, document_version=doc, accepting_user=self.holder, evidence_hash="a"*64)
        response = self.client.get(self.url("registration_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.client.get(self.url("accept_terms")).status_code, 405)
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.operational_state, OperationalState.NOT_READY)
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_accept_audit_failure_rolls_back_evidence_and_credential(self):
        self.link_holder()
        with patch("apps.registrations.views_minha_conta.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("accept_terms"), self.complete_documents())
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_credential_requires_current_terms_and_not_revoked(self):
        self.link_holder()
        self.client.post(self.url("accept_terms"), self.complete_documents())
        RegistrationCredential.objects.filter(registration=self.reg).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url("credential_view")).status_code, 302)
        RegistrationCredential.objects.filter(registration=self.reg).update(revoked_at=None)
        LegalDocumentVersion.objects.create(event=self.event, kind=DocumentKind.TERMO_RESPONSABILIDADE, version_number=2, content="Atualização vigente")
        self.assertEqual(self.client.get(self.url("credential_view")).status_code, 302)

    def test_claim_link_requires_buyer_and_post(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.url("create_claim")).status_code, 404)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self.url("create_claim")).status_code, 405)
        token, _ = self.create_link()
        self.assertEqual(ClaimToken.objects.get().hashed_token, hashlib.sha256(token.encode()).hexdigest())

    def test_guest_buyer_can_generate_share_link_from_own_session(self):
        _, claim_url = self.create_link(guest=True)
        self.assertEqual(ClaimToken.objects.count(), 1)
        self.assertEqual(self.client.get(claim_url).status_code, 302)

    def test_claim_get_is_confirmation_and_post_is_one_use(self):
        token, url = self.create_link()
        self.client.force_login(self.holder)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIsNone(ClaimToken.objects.get().used_at)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 302)
        self.assertTrue(UserPersonLink.objects.filter(person=self.person, user=self.holder).exists())
        self.assertIsNotNone(ClaimToken.objects.get().used_at)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 410)
        self.assertEqual(UserPersonLink.objects.filter(person=self.person).count(), 1)

    def test_claim_cannot_replace_existing_holder(self):
        _, url = self.create_link()
        UserPersonLink.objects.create(person=self.person, user=self.holder)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 409)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person, user=self.outsider).exists())
        self.assertIsNone(ClaimToken.objects.get().used_at)

    def test_claim_requires_confirmation_and_refuses_expired_token(self):
        _, url = self.create_link()
        self.client.force_login(self.holder)
        self.assertEqual(self.client.post(url).status_code, 400)
        ClaimToken.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 410)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())

    def test_regenerating_share_link_expires_previous_link(self):
        _, previous = self.create_link()
        self.create_link()
        self.client.force_login(self.holder)
        self.assertEqual(self.client.get(previous).status_code, 410)
        self.assertEqual(ClaimToken.objects.filter(expires_at__gt=timezone.now()).count(), 1)

    def test_claim_audit_failure_rolls_back_use_and_link(self):
        _, url = self.create_link()
        self.client.force_login(self.holder)
        with patch("apps.registrations.views_minha_conta.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {"confirm_holder": "yes"})
        self.assertIsNone(ClaimToken.objects.get().used_at)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())

    def test_accept_and_claim_mutations_are_csrf_protected(self):
        _, url = self.create_link()
        self.link_holder()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.holder)
        self.assertEqual(client.post(self.url("accept_terms"), self.complete_documents()).status_code, 403)
        self.assertEqual(client.post(url, {"confirm_holder": "yes"}).status_code, 403)
        client.force_login(self.buyer)
        self.assertEqual(client.post(self.url("create_claim")).status_code, 403)
