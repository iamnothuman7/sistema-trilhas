"""Event artwork uploads cannot replace the institutional hero or other assets."""
import io
import tempfile
from datetime import timedelta

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Organization
from apps.events.models import Event, EventStatus, EventTheme
from apps.identity.models import EventMembership, RoleChoices, User


class EventArtworkScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organization.objects.create(name="Equipe das artes")
        now = timezone.now()
        dates = dict(
            status=EventStatus.PUBLISHED, starts_at=now + timedelta(days=20),
            ends_at=now + timedelta(days=21), registration_opens_at=now - timedelta(days=1),
            registration_closes_at=now + timedelta(days=19),
        )
        cls.event = Event.objects.create(organization=org, title="Evento das artes", slug="evento-das-artes", **dates)
        cls.other = Event.objects.create(organization=org, title="Outro evento das artes", slug="outro-evento-das-artes", **dates)
        for event, name in ((cls.event, "primeiro"), (cls.other, "outro")):
            EventTheme.objects.create(
                event=event, hero_banner=f"events/banners/{name}.png",
                logo_image=f"events/logos/{name}.png", cover_image=f"events/covers/{name}-16x9.png",
                square_image=f"events/covers/{name}-1x1.png",
            )
        cls.manager = User.objects.create_user(email="artes@example.test", full_name="Gestão de artes")
        EventMembership.objects.create(user=cls.manager, event=cls.event, role_code=RoleChoices.CONTENT)

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client.force_login(self.manager)

    def image(self, name):
        data = io.BytesIO()
        Image.new("RGBA", (32, 18), (230, 100, 20, 100)).save(data, format="PNG")
        return SimpleUploadedFile(name, data.getvalue(), content_type="image/png")

    def asset_names(self, event):
        theme = EventTheme.objects.get(event=event)
        return {field: getattr(theme, field).name for field in ("hero_banner", "cover_image", "square_image", "logo_image")}

    def hero(self, response):
        html = response.content.decode()
        return html.split('<section class="trail-hero ', 1)[1].split("</section>", 1)[0]

    def home(self):
        return self.client.get(reverse("core:home"))

    def update_image(self, field):
        response = self.client.post(
            reverse("gestao:event_appearance", args=[self.event.pk]),
            {field: self.image(f"novo-{field}.png")},
        )
        self.assertEqual(response.status_code, 302)

    def test_changing_event_banner_preserves_institutional_hero_other_event_and_covers(self):
        before = self.asset_names(self.event)
        other_before = self.asset_names(self.other)
        home_hero = self.hero(self.home())
        self.update_image("hero_banner")
        after = self.asset_names(self.event)
        self.assertNotEqual(after["hero_banner"], before["hero_banner"])
        self.assertEqual({k: v for k, v in after.items() if k != "hero_banner"},
                         {k: v for k, v in before.items() if k != "hero_banner"})
        self.assertEqual(self.asset_names(self.other), other_before)
        self.assertEqual(self.hero(self.home()), home_hero)
        self.assertIn("trail-hero-desktop-v2.webp", home_hero)
        self.assertIn("trail-hero-mobile-v2.webp", home_hero)
        detail = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertIn(after["hero_banner"], self.hero(detail))
        self.assertContains(detail, 'property="og:image" content="http://testserver/media/' + before["cover_image"] + '"')
        other_detail = self.client.get(reverse("events:detail", args=[self.other.slug]))
        self.assertIn(other_before["hero_banner"], self.hero(other_detail))
        self.assertNotIn(after["hero_banner"], self.hero(other_detail))

    def test_changing_event_cover_updates_its_preview_without_replacing_any_hero_or_square(self):
        before = self.asset_names(self.event)
        other_before = self.asset_names(self.other)
        home_before = self.home()
        home_hero = self.hero(home_before)
        home_social = home_before.context['social'].copy()
        self.update_image("cover_image")
        after = self.asset_names(self.event)
        self.assertNotEqual(after["cover_image"], before["cover_image"])
        for field in ("hero_banner", "square_image", "logo_image"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.asset_names(self.other), other_before)
        home_after = self.home()
        self.assertEqual(self.hero(home_after), home_hero)
        self.assertEqual(home_after.context['social'], home_social)
        self.assertContains(home_after, before["square_image"])
        detail = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertIn(before["hero_banner"], self.hero(detail))
        self.assertContains(detail, 'property="og:image" content="http://testserver/media/' + after["cover_image"] + '"')

    def test_changing_square_card_does_not_replace_event_hero_social_cover_or_other_event(self):
        before = self.asset_names(self.event)
        other_before = self.asset_names(self.other)
        home_hero = self.hero(self.home())
        self.update_image("square_image")
        after = self.asset_names(self.event)
        self.assertNotEqual(after["square_image"], before["square_image"])
        for field in ("hero_banner", "cover_image", "logo_image"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.asset_names(self.other), other_before)
        self.assertEqual(self.hero(self.home()), home_hero)
        self.assertContains(self.home(), after["square_image"])
        detail = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertContains(detail, 'property="og:image" content="http://testserver/media/' + before["cover_image"] + '"')

    def test_clearing_event_banner_uses_event_fallback_without_changing_other_images(self):
        before = self.asset_names(self.event)
        home_hero = self.hero(self.home())
        response = self.client.post(reverse("gestao:event_appearance", args=[self.event.pk]), {"hero_banner-clear": "on"})
        self.assertEqual(response.status_code, 302)
        after = self.asset_names(self.event)
        self.assertFalse(after["hero_banner"])
        for field in ("cover_image", "square_image", "logo_image"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.hero(self.home()), home_hero)
        detail = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertIn("trail-hero-desktop-v2.webp", self.hero(detail))

    def test_appearance_editor_explains_which_page_each_asset_changes(self):
        response = self.client.get(reverse("gestao:event_appearance", args=[self.event.pk]))
        self.assertContains(response, "Topo da página deste evento")
        self.assertContains(response, "Catálogo e compartilhamento deste evento · 16:9")
        self.assertContains(response, "Card deste evento na agenda · 1:1")
        self.assertContains(response, "Não altera o banner institucional da Equipe Morcego")
        self.assertContains(response, "versões próprias para celular e computador")
