import hashlib
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image, PngImagePlugin

from apps.core.rate_limits import consume_rate_limit
from apps.mediaframes.image_validation import InvalidArtwork, canonical_png
from apps.mediaframes.models import GeneratedMontage
from tests import test_cpf_and_pilot_art as pilot_fixtures
from tests import test_system as fixtures


class MontageSecurityTests(TestCase):
    pilot = pilot_fixtures.PaidPilotArtTests.pilot

    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.pilot_user, _, self.registration = self.pilot()
        self.montage = GeneratedMontage.objects.create(owner=self.pilot_user, registration=self.registration, event=self.event)

    def png(self, color="red", **kwargs):
        output = BytesIO()
        with Image.new("RGBA", (20, 30), color) as img:
            img.save(output, "PNG", **kwargs)
        return output.getvalue()

    def send(self, raw=None, *, route="montage-backup", **fields):
        uploaded = SimpleUploadedFile("claimed-image.png", self.png() if raw is None else raw, "image/png")
        return self.client.post(reverse(f"mediaframes:{route}", args=[self.montage.pk]), {"image": uploaded, **fields})

    def test_both_endpoints_reject_non_png_truncated_and_animated_content(self):
        animated = BytesIO()
        frames = [Image.new("RGBA", (20, 30), color) for color in ("red", "blue")]
        frames[0].save(animated, "PNG", save_all=True, append_images=frames[1:])
        for route in ("montage-backup", "montage-download"):
            for raw in (b'<svg onload="alert(1)"></svg>', b"<html>content</html>", self.png()[:30], animated.getvalue()):
                with self.subTest(route=route, raw=raw[:20]):
                    self.assertEqual(self.send(raw, route=route).status_code, 400)
        self.montage.refresh_from_db()
        self.assertFalse(self.montage.generated_image)
        self.assertEqual(self.montage.downloads_count, 0)

    def test_both_endpoints_enforce_pixel_and_request_limits(self):
        for route in ("montage-backup", "montage-download"):
            with self.subTest(route=route), patch("apps.mediaframes.image_validation.MAX_IMAGE_PIXELS", 599):
                self.assertEqual(self.send(route=route).status_code, 413)
            with patch("apps.mediaframes.views.MAX_REQUEST_BYTES", 10):
                self.assertEqual(self.send(route=route).status_code, 413)

    def test_read_limit_applies_even_if_file_size_claim_is_false(self):
        uploaded = SimpleUploadedFile("fake.png", b"a" * 101, "image/png")
        uploaded.size = 1
        with patch("apps.mediaframes.image_validation.MAX_UPLOAD_BYTES", 100):
            with self.assertRaises(InvalidArtwork) as error:
                canonical_png(uploaded)
        self.assertEqual(error.exception.status, 413)

    def test_png_is_recoded_metadata_removed_and_download_matches_backup(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("comment", "private-location-marker")
        raw = self.png(pnginfo=metadata) + b"<script>appended</script>"
        response = self.send(raw, route="montage-download", version="../../private/file", download_id="safe\r\nInjected: header")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertNotIn("\n", response["X-Download-ID"])
        self.assertNotIn(b"private-location-marker", response.content)
        self.assertNotIn(b"<script>", response.content)
        with Image.open(BytesIO(response.content)) as result:
            self.assertEqual(result.size, (20, 30))
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.getpixel((0, 0)), (255, 0, 0, 255))
        self.montage.refresh_from_db()
        self.assertTrue(self.montage.generated_image.name.startswith(f"frames/generated/{self.montage.pk}/"))
        self.assertNotIn("..", self.montage.generated_image.name)
        self.assertEqual(self.montage.sha256, hashlib.sha256(response.content).hexdigest())
        self.assertEqual(self.montage.file_size, len(response.content))
        self.assertEqual(self.montage.downloads_count, 1)
        with self.montage.generated_image.open("rb") as stored:
            self.assertEqual(stored.read(), response.content)

    def test_repeated_identical_backups_reuse_file_and_replacement_deletes_only_after_commit(self):
        self.assertEqual(self.send().status_code, 200)
        self.montage.refresh_from_db()
        old = Path(self.montage.generated_image.path)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(len(list(Path(self.temp_dir.name).rglob("*.png"))), 1)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.assertEqual(self.send(self.png("blue")).status_code, 200)
        self.assertTrue(old.exists())
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertFalse(old.exists())
        self.assertEqual(len(list(Path(self.temp_dir.name).rglob("*.png"))), 1)

    def test_download_updates_backup_and_preserves_files_referenced_by_other_montages(self):
        self.send()
        self.montage.refresh_from_db()
        old = Path(self.montage.generated_image.path)
        shared = GeneratedMontage.objects.create(owner=self.pilot_user, registration=self.registration,
            event=self.event, generated_image=self.montage.generated_image.name)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.send(self.png("blue"), route="montage-download")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(old.exists())
        self.montage.refresh_from_db()
        self.assertNotEqual(self.montage.generated_image.name, shared.generated_image.name)
        with self.montage.generated_image.open("rb") as stored:
            self.assertEqual(stored.read(), result.content)

    def test_failed_row_save_removes_new_file_and_keeps_previous_backup(self):
        self.send()
        self.montage.refresh_from_db()
        old = Path(self.montage.generated_image.path)
        with patch.object(GeneratedMontage, "save", side_effect=DatabaseError("test rollback")):
            with self.assertRaises(DatabaseError):
                self.send(self.png("blue"))
        self.assertTrue(old.exists())
        self.assertEqual(list(Path(self.temp_dir.name).rglob("*.png")), [old])

    def test_upload_limit_shared_between_backup_and_download(self):
        for _ in range(30):
            consume_rate_limit("artwork-files", str(self.pilot_user.pk), limit=30, window_seconds=60)
        for route in ("montage-backup", "montage-download"):
            response = self.send(route=route)
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response["Retry-After"]), 0)
        self.assertFalse(list(Path(self.temp_dir.name).rglob("*.png")))

    def test_segmentation_rate_limit_stops_inference_and_row_creation(self):
        for _ in range(5):
            consume_rate_limit("artwork-segmentation-minute", str(self.pilot_user.pk), limit=5, window_seconds=60)
        with patch("apps.mediaframes.views.remove_background") as inference:
            result = self.client.post(reverse("mediaframes:remove-background"), {
                "registration_id": self.registration.pk,
                "photo": SimpleUploadedFile("photo.png", self.png(), "image/png"),
            })
        self.assertEqual(result.status_code, 429)
        inference.assert_not_called()
        self.assertEqual(GeneratedMontage.objects.count(), 1)

    def test_account_archive_limit_bounds_persisted_montages(self):
        GeneratedMontage.objects.bulk_create([GeneratedMontage(owner=self.pilot_user, registration=self.registration,
            event=self.event) for _ in range(199)])
        with patch("apps.mediaframes.views.remove_background") as inference:
            result = self.client.post(reverse("mediaframes:remove-background"), {
                "registration_id": self.registration.pk,
                "photo": SimpleUploadedFile("photo.png", self.png(), "image/png"),
            })
        self.assertEqual(result.status_code, 429)
        inference.assert_not_called()
        self.assertEqual(GeneratedMontage.objects.count(), 200)
