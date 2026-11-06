from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog
from apps.events.models import Sponsor
from apps.events.sponsorships import brand_values
from apps.finance.models import FinancialEntry
from apps.identity.models import RoleChoices
from tests.test_sponsors import logo_file
from tests import test_sponsorship_finance as fixtures


class SponsorEditAgreementTests(TestCase):
    setUp = fixtures.SponsorshipFinanceTests.setUp
    role = fixtures.SponsorshipFinanceTests.role
    data = fixtures.SponsorshipFinanceTests.data
    pay = fixtures.SponsorshipFinanceTests.pay

    def edit_url(self):
        return reverse("gestao:sponsor_edit", args=[self.event.pk, self.sponsor.pk])

    def edit_data(self, **changes):
        data = {
            "name": "Marca atualizada", "tier": "PRATA", "sort_order": "2",
            "agreement-amount": "1.500,00", "agreement-due_at": "2027-03-19",
            "agreement-agreement_notes": "Acordo atualizado no mesmo formulário",
            "agreement-version": str(self.sponsor.finance_version),
        }
        data.update(changes)
        return data

    def assert_original_brand(self):
        self.sponsor.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.sponsor.name, "Moto Parceira")
        self.assertEqual(self.profile.name, "Moto Parceira")

    def test_edit_shows_current_agreement_and_version_for_finance(self):
        response = self.client.get(self.edit_url())
        self.assertContains(response, 'name="agreement-amount"')
        self.assertContains(response, 'name="agreement-version"')
        self.assertContains(response, "Valor e acordo desta edição")
        self.assertEqual(response.context["agreement_form"].initial["amount"], "1.000,00")
        self.assertEqual(response.context["agreement_form"].initial["version"], 0)

    def test_edit_updates_brand_and_agreement_together_without_receipt(self):
        response = self.client.post(self.edit_url(), self.edit_data())
        self.assertRedirects(response, reverse("gestao:sponsor_detail", args=[self.event.pk, self.sponsor.pk]))
        self.sponsor.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.sponsor.agreed_amount_cents, 150000)
        self.assertEqual(str(self.sponsor.due_at), "2027-03-19")
        self.assertEqual(self.sponsor.agreement_notes, "Acordo atualizado no mesmo formulário")
        self.assertEqual(self.sponsor.finance_version, 1)
        self.assertEqual(self.profile.name, "Marca atualizada")
        self.assertEqual(self.sponsor.name, self.profile.name)
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertFalse(self.sponsor.payments.exists())
        self.assertTrue(AuditLog.objects.filter(action="SPONSOR_AGREEMENT_UPDATED").exists())

    def test_invalid_amount_preserves_brand_agreement_and_version(self):
        response = self.client.post(self.edit_url(), self.edit_data(**{"agreement-amount": "12,345"}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.context["agreement_form"].errors)
        self.assert_original_brand()
        self.assertEqual(self.sponsor.agreed_amount_cents, 100000)
        self.assertEqual(self.sponsor.finance_version, 0)
        self.assertFalse(FinancialEntry.objects.exists())

    def test_stale_agreement_does_not_save_brand_or_upload(self):
        Sponsor.objects.filter(pk=self.sponsor.pk).update(finance_version=1, agreed_amount_cents=120000)
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            response = self.client.post(self.edit_url(), self.edit_data(logo_image=logo_file()))
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "Este acordo mudou", status_code=400)
            self.assertFalse([p for p in Path(media).rglob("*") if p.is_file()])
        self.assert_original_brand()
        self.assertEqual(self.sponsor.agreed_amount_cents, 120000)
        self.assertEqual(self.sponsor.finance_version, 1)

    def test_amount_below_received_rolls_back_brand_and_finance(self):
        self.pay(amount=30000)
        self.sponsor.refresh_from_db()
        version = self.sponsor.finance_version
        response = self.client.post(self.edit_url(), self.edit_data(**{"agreement-amount": "200,00"}))
        self.assertContains(response, "não pode ser menor que o total já recebido", status_code=400)
        self.assert_original_brand()
        self.assertEqual(self.sponsor.agreed_amount_cents, 100000)
        self.assertEqual(self.sponsor.finance_version, version)
        self.assertEqual(list(FinancialEntry.objects.values_list("amount_cents_signed", flat=True)), [30000])

    def test_content_only_never_sees_or_changes_forged_agreement_fields(self):
        self.role(RoleChoices.CONTENT)
        response = self.client.get(self.edit_url())
        self.assertNotContains(response, 'name="agreement-amount"')
        self.assertNotContains(response, "Condição interna exclusiva")
        response = self.client.post(self.edit_url(), self.edit_data(**{"agreement-amount": "0,00", "agreement-version": "invalid"}))
        self.assertEqual(response.status_code, 302)
        self.sponsor.refresh_from_db()
        self.assertEqual(self.sponsor.name, "Marca atualizada")
        self.assertEqual(self.sponsor.agreed_amount_cents, 100000)
        self.assertEqual(self.sponsor.agreement_notes, "Condição interna exclusiva")
        self.assertEqual(self.sponsor.finance_version, 0)
        self.assertFalse(FinancialEntry.objects.exists())

    def test_brand_only_submission_from_old_page_preserves_agreement(self):
        response = self.client.post(self.edit_url(), {"name": "Marca atualizada", "tier": "PRATA", "sort_order": "2"})
        self.assertEqual(response.status_code, 302)
        self.sponsor.refresh_from_db()
        self.assertEqual(self.sponsor.name, "Marca atualizada")
        self.assertEqual(self.sponsor.agreed_amount_cents, 100000)
        self.assertEqual(self.sponsor.agreement_notes, "Condição interna exclusiva")
        self.assertEqual(self.sponsor.finance_version, 0)

    def test_audit_failure_rolls_back_agreement_brand_and_new_files(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with patch("apps.events.views_sponsors.log_audit", side_effect=RuntimeError("audit unavailable")), self.assertRaises(RuntimeError):
                self.client.post(self.edit_url(), self.edit_data(logo_image=logo_file()))
            self.assertFalse([p for p in Path(media).rglob("*") if p.is_file()])
        self.assert_original_brand()
        self.assertEqual(self.sponsor.agreed_amount_cents, 100000)
        self.assertEqual(self.sponsor.finance_version, 0)
        self.assertFalse(AuditLog.objects.filter(action="SPONSOR_AGREEMENT_UPDATED").exists())

    def test_event_manager_can_edit_scoped_agreement_when_shared_brand_is_locked(self):
        other = Sponsor.objects.create(event=self.other, profile=self.profile, agreed_amount_cents=60000, **brand_values(self.profile))
        self.role(RoleChoices.EVENT_MANAGER)
        response = self.client.post(self.edit_url(), self.edit_data())
        self.assertEqual(response.status_code, 302)
        self.assert_original_brand()
        self.assertEqual(self.sponsor.agreed_amount_cents, 150000)
        other.refresh_from_db()
        self.assertEqual(other.agreed_amount_cents, 60000)
        self.assertEqual(other.finance_version, 0)

    def test_profile_participations_expose_permission_flags_per_event(self):
        Sponsor.objects.create(event=self.other, profile=self.profile, **brand_values(self.profile))
        response = self.client.get(reverse("gestao:sponsor_profile_edit", args=[self.profile.pk]))
        participations = response.context["participations"]
        self.assertEqual(len(participations), 2)
        self.assertTrue(all(row.can_finance and row.can_content for row in participations))
