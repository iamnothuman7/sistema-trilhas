from datetime import datetime, timezone
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.models import Organization
from apps.events.models import Event, EventTheme
from apps.mediaframes.events_config import EVENT_VERSIONS, get_event_versions


@override_settings(LANGUAGE_CODE="pt-br", TIME_ZONE="America/Fortaleza")
class PublicEventTemplateTests(SimpleTestCase):
    def event(self, **overrides):
        data = {
            "title": "Expedição Serra em Abril",
            "slug": "expedicao-serra-abril",
            "starts_at": datetime(2027, 4, 18, 12, tzinfo=timezone.utc),
            "ends_at": datetime(2027, 4, 18, 21, tzinfo=timezone.utc),
            "venue": "Praça da Serra",
            "city": "Ubajara",
            "state": "CE",
            "description": None,
            "edition_number": 4,
            "max_attendees": 150,
            "status": "PUBLISHED",
            "is_registration_open": True,
            "timezone": "America/Fortaleza",
            "get_status_display": "Publicado",
            "get_type_display": "Expedição",
            "theme": SimpleNamespace(hero_banner=None, logo_image=None),
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def assert_current_event(self, html):
        self.assertIn("Expedição Serra em Abril", html)
        self.assertIn("18 de abril de 2027", html.lower())
        self.assertIn("Praça da Serra", html)
        self.assertIn("Ubajara", html)
        self.assertNotIn("20 e 21", html.lower())
        self.assertNotIn("1º LOTE", html.upper())
        self.assertNotIn("cartaz_trilhao_2027", html)
        self.assertNotIn("logo_3trilhao_2027", html)
        self.assertNotIn("None", html)

    def test_home_uses_featured_event_fields_without_fixed_poster_or_lot(self):
        html = render_to_string("core/home.html", {"featured_events": [self.event()]})
        self.assert_current_event(html)
        self.assertIn("Confira a programação, as modalidades disponíveis", html)
        self.assertIn("minmax(min(100%, 340px), 1fr)", html)

    def test_home_without_event_does_not_show_a_fictional_highlight(self):
        html = render_to_string("core/home.html", {"featured_events": []})
        self.assertNotIn("EVENTO EM DESTAQUE", html)
        self.assertNotIn("cartaz_trilhao_2027", html)
        self.assertNotIn("2027", html)

    def test_home_prefers_the_registered_banner(self):
        event = self.event(theme=SimpleNamespace(
            hero_banner=SimpleNamespace(url="/media/events/banners/april.jpg"),
            logo_image=SimpleNamespace(url="/media/events/logos/april.png"),
        ))
        html = render_to_string("core/home.html", {"featured_events": [event]})
        self.assertIn('/media/events/banners/april.jpg', html)
        self.assertNotIn('/media/events/logos/april.png', html)

    def test_detail_uses_actual_dates_and_safe_description_fallback(self):
        html = render_to_string("events/detail.html", {"event": self.event(), "products": []})
        self.assert_current_event(html)
        self.assertIn('<title>Expedição Serra em Abril | Equipe Morcego Oficial</title>', html)
        self.assertIn('<meta name="description"', html)
        self.assertIn("Fazer inscrição", html)
        self.assertIn("As modalidades desta edição serão divulgadas", html)

    def test_multi_day_event_shows_both_actual_dates(self):
        event = self.event(ends_at=datetime(2027, 4, 19, 21, tzinfo=timezone.utc))
        for template, context in [
            ("core/home.html", {"featured_events": [event]}),
            ("events/detail.html", {"event": event, "products": []}),
        ]:
            with self.subTest(template=template):
                html = render_to_string(template, context).lower()
                self.assertIn("18 de abril de 2027 a 19 de abril de 2027", html)

    def test_closed_event_does_not_offer_registration(self):
        event = self.event(is_registration_open=False, status="SALES_CLOSED")
        html = render_to_string("events/detail.html", {"event": event, "products": []})
        self.assertIn("Inscrições indisponíveis", html)
        self.assertNotIn("Fazer inscrição", html)

    def test_product_only_promises_recorded_benefits(self):
        product = SimpleNamespace(name="Modalidade com camiseta", description=None,
                                  get_kind_display="Piloto", includes_shirt=True,
                                  includes_meal=False, includes_kit=False)
        html = render_to_string("events/detail.html", {"event": self.event(), "products": [product]})
        self.assertIn("Camiseta oficial", html)
        self.assertNotIn("Kit do participante", html)
        self.assertNotIn("Alimentação conforme a modalidade", html)
        self.assertNotIn("Churrasco", html)
        self.assertNotIn("Caminhão de Apoio", html)
        self.assertNotIn("None", html)

    def test_list_and_frames_use_current_title_and_description_fallbacks(self):
        event = self.event()
        listing = render_to_string("events/list.html", {"active_events": [event], "past_events": []})
        self.assertNotIn("None", listing)
        self.assertIn("Consulte a programação", listing)
        self.assertIn("minmax(min(100%, 350px), 1fr)", listing)
        frames = render_to_string("events/frames.html", {"event": event, "event_versions": [], "selected_version": {}})
        self.assertIn(event.title, frames)
        self.assertNotIn("Trilhão Tianguá 2027", frames)
        self.assertNotIn("logo_3trilhao_2027", frames)


class EventStoryConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Equipe Morcego")
        cls.first = Event.objects.create(
            organization=organization, title="Expedição Serra em Abril", slug="expedicao-abril",
            starts_at=datetime(2027, 4, 18, 12, tzinfo=timezone.utc),
            ends_at=datetime(2027, 4, 18, 21, tzinfo=timezone.utc),
            registration_opens_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            registration_closes_at=datetime(2027, 4, 17, tzinfo=timezone.utc),
            city="Ubajara", state="CE", venue="Praça da Serra", status="PUBLISHED",
        )
        cls.second = Event.objects.create(
            organization=organization, title="Expedição Agosto", slug="expedicao-agosto",
            starts_at=datetime(2028, 8, 9, 20, tzinfo=timezone.utc),
            ends_at=datetime(2028, 8, 10, 1, tzinfo=timezone.utc),
            registration_opens_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            registration_closes_at=datetime(2028, 8, 8, tzinfo=timezone.utc),
            city="Viçosa do Ceará", state="CE", venue="Praça da Matriz", status="PUBLISHED",
        )
        EventTheme.objects.create(event=cls.second, logo_image="events/logos/agosto.png",
                                  hero_banner="events/banners/agosto.jpg")

    def test_two_real_editions_produce_independent_story_data(self):
        first = get_event_versions(self.first)
        second = get_event_versions(self.second)
        for a, b, style in zip(first, second, EVENT_VERSIONS):
            self.assertEqual(a["title"], self.first.title)
            self.assertEqual(b["title"], self.second.title)
            self.assertEqual(a["date_short"], "18 ABR")
            self.assertEqual(b["date_short"], "09 AGO")
            self.assertEqual(a["concentration_time"], "09H00")
            self.assertEqual(b["concentration_time"], "17H00")
            self.assertEqual(b["departure_time"], "22H00")
            self.assertEqual(b["header_time"], "2028")
            self.assertIn("Viçosa do Ceará", b["ticket_subtitle"])
            self.assertIn("Praça da Matriz", b["ticket_subtitle"])
            self.assertTrue(b["logo"].endswith("events/logos/agosto.png"))
            self.assertTrue(b["social_image_url"].endswith("events/banners/agosto.jpg"))
            self.assertIn(self.second.slug, b["filename"])
            self.assertNotIn("2027", str(b))
            self.assertEqual(a["layout"], style["layout"])
            self.assertEqual(b["palette"], style["palette"])
        first[0]["layout"]["photo"]["x"] = 999
        self.assertEqual(get_event_versions(self.first)[0]["layout"]["photo"]["x"], 185)

    def test_event_timezone_controls_calendar_date_and_canvas_time(self):
        self.second.timezone = "America/Manaus"
        self.second.starts_at = datetime(2028, 8, 10, 3, 30, tzinfo=timezone.utc)
        self.second.ends_at = datetime(2028, 8, 10, 7, 30, tzinfo=timezone.utc)
        version = get_event_versions(self.second)[0]
        self.assertEqual(version["date_short"], "09–10 AGO")
        self.assertEqual(version["concentration_time"], "23H30")
        html = render_to_string("events/detail.html", {"event": self.second, "products": []})
        self.assertIn("09 de agosto de 2028 a 10 de agosto de 2028", html.lower())

    def test_event_and_story_pages_render_correct_metadata_without_image_processing(self):
        for event, expected_date in [(self.first, "18 ABR"), (self.second, "09 AGO")]:
            for route in ["events:detail", "events:frames"]:
                with self.subTest(event=event.slug, route=route):
                    response = self.client.get(reverse(route, kwargs={"slug": event.slug}))
                    self.assertEqual(response.status_code, 200)
                    version = response.context["selected_version"]
                    self.assertEqual(version["title"], event.title)
                    self.assertEqual(version["date_short"], expected_date)
                    self.assertContains(response, f'property="og:title" content="{event.title}"')
                    self.assertNotContains(response, "cartaz_trilhao_2027")
                    self.assertNotContains(response, "logo_3trilhao_2027")
                    self.assertNotContains(response, "20 E 21 MAR")
                    self.assertNotContains(response, "None")

    def test_generic_story_has_no_fabricated_edition_date_or_schedule(self):
        version = get_event_versions()[0]
        self.assertEqual(version["date_short"], "A DEFINIR")
        self.assertEqual(version["concentration_time"], "—")
        self.assertEqual(version["departure_time"], "—")
        self.assertNotIn("2027", str(version))
        self.assertTrue(version["logo"].endswith("img/logo_emblem.png"))
