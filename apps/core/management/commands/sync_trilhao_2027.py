"""
Atualiza (de forma idempotente) o evento oficial para o 3º Trilhão Tianguá Off Road 2027
com base no cartaz oficial: datas 20 e 21/03/2027, subdomínio exclusivo e molduras.
Uso: python manage.py sync_trilhao_2027
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.events.models import Event, EventStatus, EventTheme
from apps.mediaframes.models import FrameTemplate, FrameFormat, AccessPolicy

TZ_CE = dt_timezone(timedelta(hours=-3))


class Command(BaseCommand):
    help = "Sincroniza o 3º Trilhão Tianguá Off Road 2027 (cartaz oficial, subdomínio e molduras)"

    def handle(self, *args, **options):
        event = (
            Event.objects.filter(slug="3-trilhao-tiangua-off-road-2027").first()
            or Event.objects.filter(slug__icontains="trilhao").first()
        )
        if not event:
            self.stdout.write(self.style.WARNING("Nenhum evento encontrado. Rode seed_initial_data primeiro."))
            return

        event.title = "3º Trilhão Tianguá Off Road 2027"
        event.slug = "3-trilhao-tiangua-off-road-2027"
        event.subdomain = "3trilhaotiangua"
        event.edition_number = 3
        event.status = EventStatus.PUBLISHED
        event.venue = "Polo de Lazer de Tianguá / Praça dos Eucaliptos"
        event.starts_at = datetime(2027, 3, 20, 6, 0, tzinfo=TZ_CE)
        event.ends_at = datetime(2027, 3, 21, 18, 0, tzinfo=TZ_CE)
        if not event.registration_opens_at or event.registration_opens_at > timezone.now():
            event.registration_opens_at = timezone.now() - timedelta(days=1)
        event.registration_closes_at = datetime(2027, 3, 19, 23, 59, 59, tzinfo=TZ_CE)
        event.max_attendees = 350
        event.description = (
            "O maior e mais aguardado trilhão off-road do Ceará e Piauí! Já tem data marcada: "
            "20 e 21 de Março de 2027 na Serra da Ibiapaba, Tianguá - CE. Concentração oficial, "
            "café da manhã reforçado, pontos de apoio e hidratação na serra, resgate 4x4, camisa oficial, "
            "troféu de participação e almoço de confraternização da Equipe Morcego."
        )
        event.save()

        theme, _ = EventTheme.objects.get_or_create(event=event)
        theme.primary_color = "#F97316"
        theme.secondary_color = "#F59E0B"
        theme.accent_color = "#EF4444"
        theme.surface_color = "#140A06"
        theme.save()

        for lot in event.lots.all():
            if "1º" in lot.title:
                lot.opens_at = timezone.now() - timedelta(days=1)
                lot.closes_at = datetime(2027, 1, 31, 23, 59, 59, tzinfo=TZ_CE)
            elif "2º" in lot.title:
                lot.opens_at = datetime(2027, 2, 1, 0, 0, tzinfo=TZ_CE)
                lot.closes_at = datetime(2027, 3, 19, 23, 59, 59, tzinfo=TZ_CE)
            else:
                lot.opens_at = timezone.now() - timedelta(days=1)
                lot.closes_at = datetime(2027, 3, 19, 23, 59, 59, tzinfo=TZ_CE)
            lot.save()

        templates = [
            (FrameFormat.FEED_1080x1350, "Moldura Oficial 2027 - Feed Instagram", 1350, "frames/overlays/frame_feed_2027.png", 1230),
            (FrameFormat.STORY_1080x1920, "Moldura Oficial 2027 - Stories & Reels", 1920, "frames/overlays/frame_story_2027.png", 1740),
        ]
        for fmt, title, height, asset, text_y in templates:
            FrameTemplate.objects.update_or_create(
                event=event,
                format=fmt,
                defaults={
                    "title": title,
                    "width": 1080,
                    "height": height,
                    "overlay_asset": asset,
                    "access_policy": AccessPolicy.PUBLIC,
                    "is_active": True,
                    "text_overlay_enabled": True,
                    "text_position_y": text_y,
                    "text_color": "#FFFFFF",
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"Evento sincronizado: {event.title} | slug={event.slug} | {event.microsite_url}"
        ))
