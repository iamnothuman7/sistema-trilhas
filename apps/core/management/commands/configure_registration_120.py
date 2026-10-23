"""Review/apply the authorized R$120 pilot offer without touching orders."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Lot, Product, ProductKind
from apps.core.utils import log_audit
from apps.events.models import Event, EventStatus


class Command(BaseCommand):
    help = "Simula inscrição piloto R$120 e retirada de outras modalidades. Use --apply para aplicar ao evento exato."

    def add_arguments(self, parser):
        parser.add_argument("--event", required=True, help="Slug exato do evento autorizado")
        parser.add_argument("--apply", action="store_true", help="Aplica o plano. Sem este argumento é somente leitura.")

    def handle(self, *args, **options):
        with transaction.atomic():
            events = Event.objects.select_for_update() if options["apply"] else Event.objects.all()
            try:
                event = events.get(slug=options["event"])
            except Event.DoesNotExist as exc:
                raise CommandError("Evento não encontrado pelo slug exato.") from exc
            if event.status in {EventStatus.FINISHED, EventStatus.ARCHIVED, EventStatus.CANCELED} or event.ends_at < timezone.now():
                raise CommandError("Não é permitido alterar ofertas de evento encerrado ou cancelado.")
            products = Product.objects.filter(event=event, active=True)
            lots = Lot.objects.filter(event=event, product__event=event, product__active=True,
                product__kind=ProductKind.PILOTO, active=True, closes_at__gte=timezone.now()).order_by("pk")
            if options["apply"]:
                products = products.select_for_update()
                lots = lots.select_for_update()
            products, lots = list(products), list(lots)
            if not lots:
                raise CommandError("Não há lotes ativos de piloto com vendas atuais ou futuras. Nenhum cadastro foi criado.")
            changed_lots = [lot for lot in lots if lot.price_cents != 12000]
            disabled_products = [product for product in products if product.kind != ProductKind.PILOTO]
            plan = {
                "mode": "apply" if options["apply"] else "dry-run", "event": event.slug,
                "lot_changes": [{"id": str(lot.pk), "title": lot.title, "before_cents": lot.price_cents, "after_cents": 12000} for lot in changed_lots],
                "deactivate_products": [{"id": str(product.pk), "name": product.name} for product in disabled_products],
                "preserved": "Pedidos, linhas, valores e snapshots anteriores; cobranças, inscrições, cotas e capacidade não são alterados.",
            }
            if options["apply"] and (changed_lots or disabled_products):
                Lot.objects.filter(pk__in=[lot.pk for lot in changed_lots]).update(price_cents=12000)
                Product.objects.filter(pk__in=[product.pk for product in disabled_products]).update(active=False)
                log_audit(actor=None, event_id=event.pk, action="REGISTRATION_OFFER_120_CONFIGURED",
                          entity_type="Event", entity_id=event.pk, diff=plan)
            self.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2))
