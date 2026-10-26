"""Apply the organizer's confirmed dates and meeting place to this edition only."""
import json
import re
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.utils import log_audit
from apps.events.models import Event


class Command(BaseCommand):
    help = "Atualiza o 3º Trilhão para 19–21/03/2027 no estacionamento do Mix Mateus. Sem --apply, apenas mostra."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        event = Event.objects.select_for_update().filter(slug="3-trilhao-tiangua-off-road-2027", edition_number=3).first()
        if not event:
            raise CommandError("Edição específica não encontrada; nenhum evento foi alterado.")
        zone = ZoneInfo(event.timezone)
        start, end = timezone.localtime(event.starts_at, zone), timezone.localtime(event.ends_at, zone)
        if (start.year, start.month, end.year, end.month) != (2027, 3, 2027, 3):
            raise CommandError("O calendário mudou; confira o mês e o ano antes de aplicar.")
        description = event.description or ""
        description = re.sub(r"(?:19,\s*)*20 e 21 de março de 2027", "19, 20 e 21 de março de 2027",
                             description, flags=re.IGNORECASE)
        if "Mix Mateus" not in description:
            description = description.rstrip() + " Concentração no estacionamento do Mix Mateus, em Tianguá–CE."
        desired = {"starts_at": start.replace(day=19), "ends_at": end.replace(day=21),
                   "venue": "Estacionamento do Mix Mateus", "city": "Tianguá", "state": "CE", "description": description.strip()}
        changes = {field: {"before": str(getattr(event, field)), "after": str(value)}
                   for field, value in desired.items() if getattr(event, field) != value}
        if options["apply"] and changes:
            for field in changes:
                setattr(event, field, desired[field])
            event.save(update_fields=[*changes, "updated_at"])
            log_audit(event_id=event.pk, action="EVENT_CONFIRMED_CALENDAR_UPDATED", entity_type="Event",
                      entity_id=event.pk, diff=changes)
        self.stdout.write(json.dumps({"applied": bool(options["apply"]), "changes": changes}, ensure_ascii=False))
