from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Organization
from apps.events.models import Event, EventStatus, MotorcycleRaffle, Sponsor


class PublicCalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Equipe Morcego")

    def event(self, slug, status=EventStatus.PUBLISHED, start=10, end=11):
        now = timezone.now()
        return Event.objects.create(
            organization=self.organization, title=slug, slug=slug,
            status=status, starts_at=now + timedelta(days=start),
            ends_at=now + timedelta(days=end),
            registration_opens_at=now - timedelta(days=30),
            registration_closes_at=now + timedelta(days=5),
        )

    def test_home_and_directory_prioritize_live_events_and_keep_drafts_private(self):
        upcoming = self.event("encontro-futuro")
        live = self.event("encontro-agora", start=-1, end=1)
        expired = self.event("edicao-passada", start=-5, end=-4)
        self.event("rascunho-privado", status=EventStatus.DRAFT)
        self.event("cancelado", status=EventStatus.CANCELED)
        for route, key in [("core:home", "featured_events"), ("events:list", "active_events")]:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(list(response.context[key]), [live, upcoming])
                self.assertEqual(list(response.context["past_events"]), [expired])
                self.assertNotContains(response, "rascunho-privado")
                self.assertNotContains(response, "cancelado")

    def test_raffle_and_sponsors_only_appear_within_their_event(self):
        event = self.event("evento-com-parceiros")
        MotorcycleRaffle.objects.create(event=event, is_public=True)
        Sponsor.objects.create(event=event, name="Parceiro exclusivo da edição")
        home = self.client.get(reverse("core:home"))
        self.assertNotContains(home, "Parceiro exclusivo da edição")
        self.assertNotContains(home, "/sorteio/")
        self.assertNotContains(home, "Vai ter sorteio de moto")
        detail = self.client.get(reverse("events:detail", kwargs={"slug": event.slug}))
        self.assertContains(detail, "Parceiro exclusivo da edição")
        self.assertContains(detail, "/sorteio/")

    def test_edition_logo_is_scoped_and_uploaded_logo_has_priority(self):
        from django.template.loader import render_to_string
        from apps.events.models import EventTheme
        event = self.event("3-trilhao-tiangua-off-road-2027")
        self.assertIn("logo-3trilhao-2027.webp", render_to_string("events/_event_logo.html", {"logo_event": event}))
        other = self.event("outro-encontro")
        self.assertNotIn("logo-3trilhao-2027.webp", render_to_string("events/_event_logo.html", {"logo_event": other}))
        EventTheme.objects.create(event=event, logo_image="events/logos/identidade-nova.png")
        event = Event.objects.select_related("theme").get(pk=event.pk)
        html = render_to_string("events/_event_logo.html", {"logo_event": event})
        self.assertIn("identidade-nova.png", html)
        self.assertNotIn("logo-3trilhao-2027.webp", html)
