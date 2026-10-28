import uuid
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog, Organization
from apps.events.models import Event, Sponsor, SponsorProfile
from apps.events.forms_sponsorships import SponsorAgreementForm, SponsorReceiptForm
from apps.events.sponsorships import brand_values, decorate_balance, sponsorships_with_balances
from apps.finance.models import FinancialEntry, FinancialEntryKind, SponsorPayment
from apps.finance.sponsorships import record_sponsor_payment, reverse_sponsor_payment, update_sponsor_agreement
from apps.identity.models import EventMembership, RoleChoices, User
from apps.reporting.selectors import get_financial_summary
from tests import test_system as fixtures
from tests.test_sponsors import logo_file


class SponsorshipFinanceTests(TestCase):
    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.client.force_login(self.user)
        self.profile = SponsorProfile.objects.create(organization=self.org, name="Moto Parceira", contact_phone="(88) 99999-1234", notes="Contato reservado")
        self.sponsor = Sponsor.objects.create(event=self.event, profile=self.profile, agreed_amount_cents=100000,
            due_at=timezone.localdate() + timedelta(days=3), agreement_notes="Condição interna exclusiva", **brand_values(self.profile))
        self.other = Event.objects.create(organization=self.org, title="Outra trilha", slug="outra-sponsor-finance",
            starts_at=self.event.starts_at, ends_at=self.event.ends_at, registration_opens_at=self.event.registration_opens_at,
            registration_closes_at=self.event.registration_closes_at)

    def url(self, name, event=None, sponsor=None, **kwargs):
        args = {"event_id": (event or self.event).pk}
        if name not in {"sponsor_list", "sponsor_link", "sponsor_create", "reports"}:
            args["sponsor_id"] = (sponsor or self.sponsor).pk
        args.update(kwargs)
        return reverse("gestao:" + name, kwargs=args)

    def data(self, **kwargs):
        data = {"amount": 30000, "received_on": timezone.localdate(), "method": "PIX", "reference": "TX-123",
                "notes": "Sinal do acordo", "confirm_received": True, "idempotency_key": uuid.uuid4()}
        data.update(kwargs)
        return data

    def pay(self, **kwargs):
        return record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=self.user, data=self.data(**kwargs))[0]

    def balance(self):
        return decorate_balance(sponsorships_with_balances().get(pk=self.sponsor.pk))

    def role(self, role, event=None):
        user = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.test", password="Only-test-123")
        EventMembership.objects.create(user=user, event=event or self.event, role_code=role)
        self.client.force_login(user)
        return user

    def test_partial_full_and_reversal_keep_primary_finance_in_sync(self):
        first = self.pay()
        self.assertEqual(self.balance().balance_cents, 70000)
        self.assertEqual(self.balance().payment_status, "partial")
        finance = get_financial_summary(self.event)
        self.assertEqual((finance["revenue_cents"], finance["net_cents"], finance["sponsor_outstanding_cents"]), (30000, 30000, 70000))
        self.pay(amount=70000)
        self.assertEqual(self.balance().payment_status, "paid")
        self.assertEqual(get_financial_summary(self.event)["net_cents"], 100000)
        _, changed = reverse_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, payment_id=first.pk, actor=self.user, reason="Baixa lançada por engano")
        self.assertTrue(changed)
        self.assertEqual(self.balance().balance_cents, 30000)
        finance = get_financial_summary(self.event)
        self.assertEqual((finance["revenue_cents"], finance["net_cents"], finance["sponsor_received_cents"]), (70000, 70000, 70000))
        self.assertEqual(finance["refunds_cents"], 0)
        self.assertEqual(SponsorPayment.objects.count(), 2)
        self.assertEqual(FinancialEntry.objects.count(), 3)
        self.assertTrue(AuditLog.objects.filter(action="SPONSOR_PAYMENT_REVERSED", reason="Baixa lançada por engano").exists())

    def test_repeat_submission_does_not_duplicate_income_even_after_reversal(self):
        data = self.data(amount=100000)
        payment, created = record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=self.user, data=data)
        self.assertTrue(created)
        repeat, created = record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=self.user, data=data)
        self.assertFalse(created)
        self.assertEqual(payment.pk, repeat.pk)
        reverse_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, payment_id=payment.pk, actor=self.user, reason="Correção do recebimento")
        _, changed = reverse_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, payment_id=payment.pk, actor=self.user, reason="Correção do recebimento")
        self.assertFalse(changed)
        record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=self.user, data=data)
        self.assertEqual(self.balance().received_cents, 0)
        self.assertEqual(FinancialEntry.objects.count(), 2)
        with self.assertRaises(ValidationError):
            record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=self.user, data={**data, "amount": 10000})

    def test_reject_overpayment_missing_agreement_future_invalid_method_and_unconfirmed(self):
        for fields in ({"amount": 100001}, {"amount": 0}, {"amount": -100}, {"received_on": timezone.localdate() + timedelta(days=1)}, {"method": "UNKNOWN"}, {"confirm_received": False}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.pay(**fields)
        Sponsor.objects.filter(pk=self.sponsor.pk).update(agreed_amount_cents=None)
        with self.assertRaises(ValidationError):
            self.pay()
        self.assertFalse(FinancialEntry.objects.exists())

    def test_service_rejects_other_event_and_content_only_actor(self):
        editor = self.role(RoleChoices.CONTENT)
        with self.assertRaises(PermissionDenied):
            record_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, actor=editor, data=self.data())
        with self.assertRaises(Sponsor.DoesNotExist):
            record_sponsor_payment(event=self.other, sponsor_id=self.sponsor.pk, actor=self.user, data=self.data())

    def test_audit_failure_rolls_back_receipt_and_ledger(self):
        with patch("apps.finance.sponsorships.log_audit", side_effect=RuntimeError("audit unavailable")), self.assertRaises(RuntimeError):
            self.pay()
        self.assertFalse(SponsorPayment.objects.exists())
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertEqual(self.balance().finance_version, 0)

    def test_audit_failure_rolls_back_reversal(self):
        payment = self.pay()
        with patch("apps.finance.sponsorships.log_audit", side_effect=RuntimeError("audit unavailable")), self.assertRaises(RuntimeError):
            reverse_sponsor_payment(event=self.event, sponsor_id=self.sponsor.pk, payment_id=payment.pk, actor=self.user, reason="Correção de baixa")
        payment.refresh_from_db()
        self.assertIsNone(payment.reversed_at)
        self.assertEqual(FinancialEntry.objects.count(), 1)

    def test_agreement_rejects_below_received_or_stale_version(self):
        self.pay()
        sponsor = self.balance()
        data = {"amount": 20000, "version": sponsor.finance_version, "due_at": None, "agreement_notes": ""}
        with self.assertRaises(ValidationError):
            update_sponsor_agreement(event=self.event, sponsor_id=sponsor.pk, actor=self.user, data=data)
        with self.assertRaises(ValidationError):
            update_sponsor_agreement(event=self.event, sponsor_id=sponsor.pk, actor=self.user, data={**data, "amount": 200000, "version": 0})
        update_sponsor_agreement(event=self.event, sponsor_id=sponsor.pk, actor=self.user, data={**data, "amount": 200000})
        self.assertEqual(self.balance().balance_cents, 170000)
        self.assertEqual(FinancialEntry.objects.count(), 1)

    def test_publication_is_explicit_event_specific_and_independent_of_payment(self):
        other = Sponsor.objects.create(event=self.other, profile=self.profile, **brand_values(self.profile))
        for name in ("detail", "sponsors"):
            response = self.client.get(reverse("events:" + name, args=[self.event.slug]))
            self.assertNotContains(response, self.profile.name)
        self.sponsor.is_public = True
        self.sponsor.save(update_fields=["is_public"])
        response = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertContains(response, self.profile.name)
        self.assertNotContains(response, "Condição interna exclusiva")
        self.assertNotContains(response, self.profile.contact_phone)
        other.refresh_from_db()
        self.assertFalse(other.is_public)
        self.assertFalse(SponsorPayment.objects.exists())

    def test_link_existing_brand_keeps_independent_agreement(self):
        response = self.client.post(self.url("sponsor_link", event=self.other), {"profile": self.profile.pk, "tier": "BRONZE", "sort_order": 0,
            "is_public": "on", "agreement-amount": "500,00"})
        self.assertEqual(response.status_code, 302)
        linked = Sponsor.objects.get(event=self.other, profile=self.profile)
        self.assertEqual(linked.agreed_amount_cents, 50000)
        self.assertEqual(self.balance().agreed_amount_cents, 100000)
        self.assertTrue(linked.is_public)
        self.assertFalse(self.balance().is_public)
        duplicate = self.client.post(self.url("sponsor_link", event=self.other), {"profile": self.profile.pk, "tier": "BRONZE", "sort_order": 0})
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(Sponsor.objects.filter(profile=self.profile).count(), 2)

    def test_cross_organization_profile_is_not_linkable_or_visible(self):
        organization = Organization.objects.create(name="Outra organização")
        foreign = SponsorProfile.objects.create(organization=organization, name="Marca privada de outra organização")
        self.role(RoleChoices.CONTENT)
        self.assertNotContains(self.client.get(reverse("gestao:sponsor_bank")), foreign.name)
        response = self.client.post(self.url("sponsor_link"), {"profile": foreign.pk, "tier": "OURO", "sort_order": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(reverse("gestao:sponsor_profile_edit", args=[foreign.pk])).status_code, 404)

    def test_content_cannot_see_money_or_post_financial_actions(self):
        self.pay()
        self.role(RoleChoices.CONTENT)
        detail = self.client.get(self.url("sponsor_detail"))
        self.assertNotContains(detail, "Condição interna exclusiva")
        self.assertNotContains(detail, "Registrar recebimento")
        self.assertNotContains(detail, "300,00")
        for name in ("sponsor_agreement", "sponsor_receipt"):
            self.assertEqual(self.client.post(self.url(name), self.data()).status_code, 403)
        self.assertEqual(self.client.get(self.url("reports") + "?export=sponsorships").status_code, 403)
        self.assertEqual(self.client.get(self.url("sponsor_detail", event=self.other)).status_code, 403)

    def test_finance_role_can_receive_but_cannot_change_brand(self):
        self.role(RoleChoices.FINANCE)
        self.assertEqual(self.client.get(self.url("sponsor_list")).status_code, 200)
        self.assertEqual(self.client.get(self.url("sponsor_receipt")).status_code, 200)
        self.assertEqual(self.client.get(self.url("sponsor_edit")).status_code, 403)
        self.assertEqual(self.client.get(reverse("gestao:sponsor_profile_create")).status_code, 403)

    def test_shared_brand_editor_cannot_change_events_outside_scope(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, **brand_values(self.profile))
        self.role(RoleChoices.CONTENT)
        self.assertEqual(self.client.get(reverse("gestao:sponsor_profile_edit", args=[self.profile.pk])).status_code, 403)
        response = self.client.post(self.url("sponsor_edit"), {"name": "Hacked name", "tier": "PRATA", "sort_order": 5, "is_public": "on"})
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.name, "Moto Parceira")
        self.assertEqual(self.balance().tier, "PRATA")

    def test_profile_update_syncs_images_without_changing_event_approval_or_amounts(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, is_public=True, **brand_values(self.profile))
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            response = self.client.post(reverse("gestao:sponsor_profile_edit", args=[self.profile.pk]), {"name": "Nova marca oficial", "logo_image": logo_file(), "contact_name": "Responsável"})
            self.assertEqual(response.status_code, 302)
            first, other = Sponsor.objects.get(pk=self.sponsor.pk), Sponsor.objects.get(event=self.other)
            self.assertEqual(first.name, other.name)
            self.assertTrue(first.logo_preview)
            self.assertEqual(first.logo_preview.name, other.logo_preview.name)
            self.assertFalse(first.is_public)
            self.assertTrue(other.is_public)
            self.assertEqual(first.agreed_amount_cents, 100000)

    def test_exports_include_sponsor_balances_receipts_and_primary_ledger(self):
        self.pay(reference="=FORMULA()")
        for section in ("sponsorships", "sponsor_receipts", "financial", "summary"):
            response = self.client.get(self.url("reports") + "?export=" + section)
            self.assertEqual(response.status_code, 200)
            self.assertIn("private", response["Cache-Control"])
        receipts = self.client.get(self.url("reports") + "?export=sponsor_receipts").content.decode("utf-8-sig")
        self.assertIn("'=FORMULA()", receipts)
        balances = self.client.get(self.url("reports") + "?export=sponsorships").content.decode("utf-8-sig")
        self.assertIn("700,00", balances)
        self.assertContains(self.client.get(reverse("gestao:finance", args=[self.event.pk])), "Patrocínios conectados ao caixa")

    def test_receipt_post_redirect_get_and_csrf(self):
        url = self.url("sponsor_receipt")
        data = self.data(amount="1.000,00")
        response = self.client.post(url, data)
        self.assertRedirects(response, self.url("sponsor_detail"))
        self.assertEqual(self.balance().payment_status, "paid")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(SponsorPayment.objects.count(), 1)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(url, data).status_code, 403)

    def test_reversal_requires_reason_confirmation_and_event_scope(self):
        payment = self.pay()
        url = self.url("sponsor_reversal", payment_id=payment.pk)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"reason": "Correção"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "", "confirm": "on"}).status_code, 400)
        wrong = Sponsor.objects.create(event=self.event, name="Outra marca")
        self.assertEqual(self.client.get(self.url("sponsor_reversal", sponsor=wrong, payment_id=payment.pk)).status_code, 404)
        self.assertEqual(self.client.post(url, {"reason": "Correção do lançamento", "confirm": "on"}).status_code, 302)
        self.assertEqual(self.balance().received_cents, 0)

    def test_receipt_date_period_and_current_outstanding_are_distinct(self):
        self.pay(received_on=timezone.localdate() - timedelta(days=3))
        today = timezone.localdate()
        report = get_financial_summary(self.event, today, today)
        self.assertEqual(report["sponsor_received_cents"], 0)
        self.assertEqual(report["sponsor_outstanding_cents"], 70000)
        self.assertEqual(get_financial_summary(self.other)["sponsor_received_cents"], 0)

    def test_all_sponsor_pages_render_and_are_private(self):
        payment = self.pay()
        paths = [self.url(name) for name in ("sponsor_list", "sponsor_create", "sponsor_link", "sponsor_detail", "sponsor_edit", "sponsor_agreement", "sponsor_receipt")]
        paths += [reverse("gestao:sponsor_bank"), reverse("gestao:sponsor_profile_create"), reverse("gestao:sponsor_profile_edit", args=[self.profile.pk]), self.url("sponsor_reversal", payment_id=payment.pk)]
        for url in paths:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("private", response["Cache-Control"])

    def test_forms_accept_local_money_but_reject_precision_and_future_receipts(self):
        for raw, expected in (("1.234,56", 123456), ("0,00", 0), ("", None)):
            form = SponsorAgreementForm({"amount": raw})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["amount"], expected)
        self.assertFalse(SponsorAgreementForm({"amount": "12,345"}).is_valid())
        form = SponsorReceiptForm(self.data(amount="20,00", received_on=timezone.localdate() + timedelta(days=1)))
        self.assertFalse(form.is_valid())
