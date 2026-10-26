import io
import tempfile
from pathlib import Path

from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order
from apps.core.private_storage import private_document_storage
from apps.events.models import Event
from apps.finance.admin import ExpenseAdmin
from apps.finance.models import Expense
from apps.identity.models import EventMembership, RoleChoices, User
from apps.payments.admin import ManualPaymentEvidenceAdmin
from apps.payments.models import ManualPaymentEvidence
from tests import test_system as fixtures


class PrivateDocumentTests(TestCase):
    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.root / "public", PRIVATE_MEDIA_ROOT=self.root / "private")
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.expense = Expense.objects.create(
            event=self.event, description="Despesa", amount_cents=1200, due_at=timezone.localdate(),
            proof_asset=SimpleUploadedFile("recibo.html", b"<b>privado</b>"),
        )
        self.order = Order.objects.create(event=self.event, merchant_reference="PRIVATE-TEST",
                                          purchaser_name="Teste", purchaser_email="buyer@example.test",
                                          expires_at=timezone.now())
        self.evidence = ManualPaymentEvidence.objects.create(
            order=self.order, amount_cents=1200, verified_by=self.user, notes="Teste",
            proof_asset=SimpleUploadedFile("pix.pdf", b"%PDF-test-private"),
        )

    def url(self, document=None, event=None):
        document = document or self.expense
        return reverse("gestao:private_document_download", kwargs={
            "event_id": (event or self.event).pk,
            "kind": "despesa" if isinstance(document, Expense) else "pagamento",
            "document_id": document.pk,
        })

    def finance_user(self):
        user = User.objects.create_user(email="finance-private@example.test")
        EventMembership.objects.create(user=user, event=self.event, role_code=RoleChoices.FINANCE)
        return user

    def test_uploads_stay_outside_public_media_and_have_no_public_url(self):
        for document in (self.expense, self.evidence):
            self.assertTrue(Path(document.proof_asset.path).is_relative_to(self.root / "private"))
            self.assertFalse((self.root / "public" / document.proof_asset.name).exists())
            with self.assertRaises(ValueError):
                _ = document.proof_asset.url
            self.assertEqual(self.client.get("/media/" + document.proof_asset.name).status_code, 404)

    def test_authorized_downloads_are_attachments_not_cacheable_or_renderable(self):
        self.client.force_login(self.finance_user())
        for document, expected in ((self.expense, b"<b>privado</b>"), (self.evidence, b"%PDF-test-private")):
            response = self.client.get(self.url(document))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/octet-stream")
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("private", response["Cache-Control"])
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(b"".join(response.streaming_content), expected)

    def test_anonymous_participant_and_nonfinancial_team_cannot_download(self):
        for document in (self.expense, self.evidence):
            self.assertEqual(self.client.get(self.url(document)).status_code, 302)
        user = User.objects.create_user(email="content-private@example.test")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        EventMembership.objects.create(user=user, event=self.event, role_code=RoleChoices.CONTENT)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.get(self.url(self.evidence)).status_code, 403)

    def test_scope_cannot_be_bypassed_with_other_event_or_document_id(self):
        other = Event.objects.get(pk=self.event.pk)
        other.pk = None
        other.slug = "other-document-event"
        other.subdomain = "other-document-event"
        other.save()
        self.client.force_login(self.finance_user())
        self.assertEqual(self.client.get(self.url(event=other)).status_code, 403)
        other_expense = Expense.objects.create(event=other, description="Outro", amount_cents=10, due_at=timezone.localdate())
        self.assertEqual(self.client.get(self.url(other_expense)).status_code, 404)
        self.order.event = other
        self.order.save(update_fields=["event"])
        self.assertEqual(self.client.get(self.url(self.evidence)).status_code, 404)

    def test_download_rejects_post_and_missing_files(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url()).status_code, 405)
        Path(self.expense.proof_asset.path).unlink()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.expense.proof_asset = ""
        self.expense.save(update_fields=["proof_asset"])
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_admin_form_and_finance_page_use_protected_download_without_public_url(self):
        request = RequestFactory().get("/admin/")
        request.user = self.user
        for model, document, model_admin in ((Expense, self.expense, ExpenseAdmin),
                                             (ManualPaymentEvidence, self.evidence, ManualPaymentEvidenceAdmin)):
            handler = model_admin(model, admin.site)
            rendered = handler.get_form(request, document)(instance=document).as_p()
            self.assertNotIn("/media/", rendered)
            self.assertIn(self.url(document), handler.private_document_link(document))
        self.client.force_login(self.user)
        response = self.client.get(reverse("gestao:finance", kwargs={"event_id": self.event.pk}))
        self.assertContains(response, self.url())

    def test_private_storage_refuses_public_root_and_path_traversal(self):
        with override_settings(PRIVATE_MEDIA_ROOT=self.root / "public" / "bad"):
            with self.assertRaises(ImproperlyConfigured):
                private_document_storage.path("proof.pdf")
        with self.assertRaises(SuspiciousFileOperation):
            private_document_storage.path("../proof.pdf")

    def test_legacy_migration_requires_apply_and_verifies_copy_before_removal(self):
        name = self.expense.proof_asset.name
        private = Path(self.expense.proof_asset.path)
        public = self.root / "public" / name
        public.parent.mkdir(parents=True)
        public.write_bytes(private.read_bytes())
        private.unlink()
        output = io.StringIO()
        call_command("migrate_private_documents", stdout=output)
        self.assertTrue(public.exists())
        self.assertFalse(private.exists())
        call_command("migrate_private_documents", apply=True, stdout=output)
        self.assertEqual(private.read_bytes(), b"<b>privado</b>")
        self.assertFalse(public.exists())
        call_command("migrate_private_documents", apply=True, stdout=output)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.proof_asset.name, name)

    def test_legacy_conflicts_and_missing_files_are_reported_not_overwritten(self):
        public = self.root / "public" / self.expense.proof_asset.name
        public.parent.mkdir(parents=True)
        public.write_bytes(b"different")
        with self.assertRaises(CommandError):
            call_command("migrate_private_documents", apply=True, stdout=io.StringIO())
        self.assertEqual(public.read_bytes(), b"different")
        self.assertEqual(Path(self.expense.proof_asset.path).read_bytes(), b"<b>privado</b>")
        public.unlink()
        Path(self.expense.proof_asset.path).unlink()
        with self.assertRaises(CommandError):
            call_command("migrate_private_documents", apply=True, stdout=io.StringIO())
