from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import EventMembership, RoleChoices
from apps.mediaframes.models import FrameTemplate, GeneratedMontage
from tests import test_system as fixtures
from tests import test_team_frames as team_fixtures


class InternalFrameCatalogTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp
    operator = team_fixtures.TeamFrameTests.operator
    other_event = team_fixtures.TeamFrameTests.other_event

    def url(self, event=None):
        return reverse("gestao:official_frame_preview", args=[(event or self.event).pk])

    def test_catalog_displays_one_official_theme_in_both_formats(self):
        self.operator()
        self.event.subdomain = "3trilhaotiangua"
        self.event.save()
        self.assertFalse(FrameTemplate.objects.exists())
        response = self.client.get(reverse("gestao:frames", args=[self.event.pk]))
        for label in ["Terra &amp; Trilha", "Criar Story", "Criar Feed", "Criar arte de piloto", "Criar arte de equipe"]:
            self.assertContains(response, label)
        for label in ['Dark Carbon', 'Laranja Racing', 'Arquivos de molduras adicionados', 'Adicionar moldura']:
            self.assertNotContains(response, label)
        self.assertContains(response, "logo_3trilhao_2027.png")
        self.assertContains(response, 'data-frame-preview data-format="story"', count=1)
        self.assertContains(response, 'data-frame-preview data-format="feed"', count=1)
        self.assertContains(response, 'id="frame-catalog-version"', count=1)
        self.assertNotContains(response, "O gerador público utilizará o layout padrão")

    def test_missing_legacy_file_does_not_render_broken_image_or_link(self):
        self.operator()
        FrameTemplate.objects.create(event=self.event, title="Arquivo antigo", overlay_asset="frames/overlays/missing.png")
        response = self.client.get(reverse("gestao:frames", args=[self.event.pk]))
        self.assertNotContains(response, "Arquivo antigo")
        self.assertNotContains(response, "Arquivos de molduras adicionados")
        self.assertEqual(self.client.post(reverse('gestao:frames', args=[self.event.pk]), {}).status_code, 405)
        self.assertNotContains(response, 'src="/media/frames/overlays/missing.png"')
        self.assertTrue(FrameTemplate.objects.filter(title="Arquivo antigo").exists())

    def test_internal_editor_accepts_photos_without_requiring_organizer_purchase(self):
        self.operator()
        response = self.client.get(self.url(), {"versao": "trilhao-carbon", "formato": "feed"})
        self.assertContains(response, 'data-internal-editor="true"')
        self.assertContains(response, 'data-version="trilhao-fogo" data-format="feed"')
        self.assertEqual(len(response.context['event_versions']), 1)
        self.assertNotContains(response, 'Escolha seu estilo')
        self.assertContains(response, "management-sidebar")
        self.assertContains(response, "story_studio.js")
        self.assertContains(response, 'id="photo-input"')
        self.assertContains(response, 'id="upload-button"')
        self.assertContains(response, 'id="pilot-name-input"')
        self.assertContains(response, 'id="photo-fit"')
        self.assertContains(response, 'id="artwork-subject"')
        self.assertNotContains(response, 'id="story-registration"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertFalse(GeneratedMontage.objects.exists())

    def test_preview_is_scoped_to_content_role_and_event(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.operator(RoleChoices.FINANCE)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        user = self.operator()
        self.assertEqual(self.client.get(self.url(self.other_event())).status_code, 403)
        EventMembership.objects.filter(user=user).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_preview_can_inspect_draft_but_cannot_unlock_public_art(self):
        self.operator()
        self.event.status = "DRAFT"
        self.event.save()
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.post(self.url(), {}).status_code, 405)
        response = self.client.get(reverse("events:frames", args=[self.event.slug]), {"internal_editor": "true", "tipo": "equipe"})
        self.assertNotContains(response, 'id="story-canvas"')
        self.assertEqual(self.client.post(reverse("mediaframes:remove-background"), {}).status_code, 403)

    def test_team_editor_is_available_without_paid_teams_and_preserves_event_scope(self):
        self.operator()
        response = self.client.get(self.url(), {"tipo": "equipe", "formato": "feed"})
        self.assertContains(response, '<option value="team" selected>Equipe confirmada</option>', html=True)
        self.assertContains(response, 'id="team-name-input"')
        self.assertContains(response, 'id="photo-input"')
        self.assertNotContains(response, 'id="story-registration"')
        self.assertNotContains(response, 'Nenhuma equipe confirmada')
        self.assertEqual(self.client.get(self.url(self.other_event()), {"tipo": "equipe"}).status_code, 403)
        invalid = self.client.get(self.url(), {"tipo": "<script>alert(1)</script>"})
        self.assertEqual(invalid.context["artwork_subject"], "pilot")
        self.assertNotContains(invalid, '<script>alert(1)</script>')
