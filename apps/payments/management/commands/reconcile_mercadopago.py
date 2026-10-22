from django.core.management.base import BaseCommand, CommandError
from apps.payments.gateway import GatewayError
from apps.payments.models import ChargeAttempt, ChargeState
from apps.payments.services import refresh_charge, PaymentValidationError


class Command(BaseCommand):
    help = "Consulta pagamentos existentes e reconcilia estados locais. Nunca cria cobranças."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--charge-id", help="UUID de uma tentativa específica, incluindo pagamentos já aprovados.")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("--limit deve ficar entre 1 e 1000.")
        charges = ChargeAttempt.objects.filter(provider="mercadopago").select_related("order")
        if options["charge_id"]:
            charges = charges.filter(pk=options["charge_id"])
        else:
            charges = charges.filter(state__in=[ChargeState.SENT, ChargeState.PENDING, ChargeState.UNKNOWN])
        successes = failures = 0
        for charge in charges.order_by("sent_at")[:options["limit"]]:
            try:
                refresh_charge(charge)
                successes += 1
            except (GatewayError, PaymentValidationError) as exc:
                failures += 1
                self.stderr.write(f"Tentativa {charge.id}: {getattr(exc, 'code', str(exc))}")
        self.stdout.write(f"Consultadas: {successes}; pendências: {failures}. Nenhuma cobrança criada.")
        if failures:
            raise CommandError("Algumas tentativas ainda precisam de conferência.")
