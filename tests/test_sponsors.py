from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.events.forms import SponsorAdminForm, SponsorForm
from apps.events.models import Event, Sponsor
from apps.identity.models import EventMembership, RoleChoices, User
from tests import test_system as _fixtures


def logo_file(format="PNG", name=None):
    stream = BytesIO()
    Image.new("RGB", (64, 32), (255, 140, 40)).save(stream, format=format)
    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}[format]
    return SimpleUploadedFile(name or f"logo.{extension}", stream.getvalue(), content_type=f"image/{extension}")


class SponsorManagementTests(TestCase):
    def setUp(self):
        _fixtures.SistemaTrilhasTests.setUp(self)
        self.media = TemporaryDirectory(prefix="trilhas-sponsor-tests-")
        self.addCleanup(self.media.cleanup)
        settings = override_settings(MEDIA_ROOT=self.media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.user)
        now = timezone.now()
        self.other = Event.objects.create(
            organization=self.org, title="Outra edição", slug="outra-edicao-sponsors",
            starts_at=now, ends_at=now+timedelta(days=1),
            registration_opens_at=now, registration_closes_at=now+timedelta(hours=1),
        )

    def url(self, name="sponsor_list", event=None, sponsor=None):
        kwargs = {"event_id": (event or self.event).pk}
        if sponsor:
            kwargs["sponsor_id"] = sponsor.pk
        return reverse(f"gestao:{name}", kwargs=kwargs)

    def data(self, **changes):
        values = {"name": "Marca da trilha", "tier": "PRATA", "instagram_url": "https://www.instagram.com/marca.trilha/", "website": "https://example.com", "sort_order": "3"}
        values.update(changes)
        return values

    def operator(self, role=RoleChoices.CONTENT):
        user = User.objects.create_user(email="sponsor.editor@example.test", password="local-test-only")
        EventMembership.objects.create(user=user, event=self.event, role_code=role)
        self.client.force_login(user)
        return user

    def test_content_editor_creates_placeholder_with_correct_event_and_audit(self):
        self.operator()
        response = self.client.post(self.url("sponsor_create"), self.data(event=str(self.other.pk)))
        self.assertRedirects(response, self.url())
        sponsor = Sponsor.objects.get()
        self.assertEqual(sponsor.event, self.event)
        self.assertFalse(sponsor.logo_image)
        self.assertEqual(sponsor.website, "https://example.com")
        self.assertEqual(sponsor.tier, "PRATA")
        self.assertEqual(sponsor.sort_order, 3)
        self.assertTrue(AuditLog.objects.filter(action="SPONSOR_CREATED", event_id=self.event.pk).exists())
        listing = self.client.get(self.url())
        self.assertContains(listing, "Espaço reservado para a imagem")
        self.assertContains(listing, "https://www.instagram.com/marca.trilha/")

    def test_create_with_image_then_edit_preserves_file_and_updates_links(self):
        response = self.client.post(self.url("sponsor_create"), self.data(logo_image=logo_file()))
        self.assertEqual(response.status_code, 302)
        sponsor = Sponsor.objects.get()
        stored = sponsor.logo_image.name
        self.assertTrue(sponsor.logo_image.storage.exists(stored))
        response = self.client.post(self.url("sponsor_edit", sponsor=sponsor), self.data(name="Nova marca", instagram_url="https://instagram.com/nova_marca/"))
        self.assertEqual(response.status_code, 302)
        sponsor.refresh_from_db()
        self.assertEqual(sponsor.logo_image.name, stored)
        self.assertEqual(sponsor.name, "Nova marca")
        self.assertEqual(sponsor.instagram_url, "https://instagram.com/nova_marca/")

    def test_editor_cannot_read_or_write_other_event(self):
        self.operator()
        foreign = Sponsor.objects.create(event=self.other, name="Marca privada")
        for name, sponsor in (("sponsor_list", None), ("sponsor_create", None), ("sponsor_edit", foreign)):
            with self.subTest(name=name):
                url = self.url(name, event=self.other, sponsor=sponsor)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, self.data()).status_code, 403)
        self.assertNotContains(self.client.get(self.url()), "Marca privada")
        self.assertEqual(Sponsor.objects.count(), 1)

    def test_sponsor_id_must_belong_to_event_even_for_admin(self):
        foreign = Sponsor.objects.create(event=self.other, name="Não alterar")
        url = self.url("sponsor_edit", sponsor=foreign)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, self.data()).status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, "Não alterar")

    def test_wrong_role_cannot_edit_and_has_no_menu_link(self):
        self.operator(RoleChoices.FINANCE)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url("sponsor_create"), self.data()).status_code, 403)
        self.assertNotContains(self.client.get("/gestao/"), self.url())

    def test_revoked_editor_cannot_access(self):
        user = self.operator()
        EventMembership.objects.filter(user=user).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_csrf_required_and_get_does_not_create(self):
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.user)
        self.assertEqual(strict_client.post(self.url("sponsor_create"), self.data()).status_code, 403)
        self.assertEqual(self.client.get(self.url("sponsor_create")).status_code, 200)
        self.assertFalse(Sponsor.objects.exists())

    def test_supported_image_formats(self):
        for format in ("PNG", "JPEG", "WEBP"):
            with self.subTest(format=format):
                form = SponsorForm(self.data(), {"logo_image": logo_file(format)})
                self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_fake_images_gif_and_oversized_upload(self):
        oversized = logo_file()
        oversized = SimpleUploadedFile("large.png", oversized.read()+b"0"*(5*1024*1024))
        for upload in (
            SimpleUploadedFile("fake.png", b"<script>alert(1)</script>"),
            SimpleUploadedFile("vector.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
            logo_file("GIF"), oversized,
        ):
            with self.subTest(name=upload.name):
                response = self.client.post(self.url("sponsor_create"), self.data(logo_image=upload))
                self.assertEqual(response.status_code, 400)
                self.assertIn("logo_image", response.context["form"].errors)
        self.assertFalse(Sponsor.objects.exists())

    def test_rejects_instagram_links_outside_https_profile(self):
        for url in (
            "javascript:alert(1)", "https://evil.example/brand/", "https://instagram.com.evil.example/brand/",
            "http://instagram.com/brand/", "https://instagram.com@evil.example/brand/",
            "https://www.instagram.com:8443/brand/", "https://instagram.com/p/post/",
            "https://instagram.com/", "https://instagram.com/brand/?next=https://evil.example",
        ):
            with self.subTest(url=url):
                form = SponsorForm(self.data(instagram_url=url))
                self.assertFalse(form.is_valid())
                self.assertIn("instagram_url", form.errors)

    def test_invalid_post_preserves_fields_and_existing_record(self):
        sponsor = Sponsor.objects.create(event=self.event, name="Marca original", tier="OURO", sort_order=8)
        response = self.client.post(self.url("sponsor_edit", sponsor=sponsor), self.data(instagram_url="https://evil.example/marca/"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Marca da trilha"', status_code=400)
        sponsor.refresh_from_db()
        self.assertEqual(sponsor.name, "Marca original")
        self.assertEqual(sponsor.sort_order, 8)

    def test_audit_failure_rolls_back_sponsor(self):
        with patch("apps.events.views_sponsors.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("sponsor_create"), self.data())
        self.assertFalse(Sponsor.objects.exists())

    def test_create_audit_failure_removes_new_uploaded_file(self):
        with patch("apps.events.views_sponsors.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("sponsor_create"), self.data(logo_image=logo_file()))
        self.assertFalse(Sponsor.objects.exists())
        self.assertEqual([p for p in Path(self.media.name).rglob("*") if p.is_file()], [])

    def test_edit_audit_failure_removes_only_replacement_preserving_original(self):
        sponsor = Sponsor.objects.create(event=self.event, name="Marca original", logo_image=logo_file())
        previous_name = sponsor.logo_image.name
        with sponsor.logo_image.open("rb") as stored:
            previous_bytes = stored.read()
        with patch("apps.events.views_sponsors.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("sponsor_edit", sponsor=sponsor), self.data(logo_image=logo_file()))
        sponsor.refresh_from_db()
        self.assertEqual(sponsor.name, "Marca original")
        self.assertEqual(sponsor.logo_image.name, previous_name)
        with sponsor.logo_image.open("rb") as stored:
            self.assertEqual(stored.read(), previous_bytes)
        files = [p.relative_to(self.media.name).as_posix() for p in Path(self.media.name).rglob("*") if p.is_file()]
        self.assertEqual(files, [previous_name])

    def test_edit_without_upload_audit_failure_keeps_original_file(self):
        sponsor = Sponsor.objects.create(event=self.event, name="Marca original", logo_image=logo_file())
        previous_name = sponsor.logo_image.name
        with patch("apps.events.views_sponsors.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("sponsor_edit", sponsor=sponsor), self.data())
        sponsor.refresh_from_db()
        self.assertEqual(sponsor.logo_image.name, previous_name)
        self.assertTrue(sponsor.logo_image.storage.exists(previous_name))

    def test_django_admin_uses_same_instagram_validation(self):
        form = SponsorAdminForm(self.data(event=str(self.event.pk), approved_at=timezone.now(), instagram_url="https://evil.example/brand/"))
        self.assertFalse(form.is_valid())
        self.assertIn("instagram_url", form.errors)

    def test_existing_image_reference_does_not_need_storage_access_to_edit(self):
        sponsor = Sponsor.objects.create(event=self.event, name="Marca legada", logo_image="events/sponsors/legacy.png")
        response = self.client.post(self.url("sponsor_edit", sponsor=sponsor), self.data())
        self.assertEqual(response.status_code, 302)
        sponsor.refresh_from_db()
        self.assertEqual(sponsor.logo_image.name, "events/sponsors/legacy.png")
        self.assertEqual(sponsor.instagram_url, self.data()["instagram_url"])
