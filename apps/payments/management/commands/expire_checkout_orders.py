from django.core.management.base import BaseCommand, CommandError
from apps.payments.services import expire_stale_orders


class Command(BaseCommand):
    help = "Libera reservas vencidas de pedidos sem confirmação, inclusive checkouts abandonados sem cobrança."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 10000:
            raise CommandError("--limit deve ficar entre 1 e 10000.")
        count = expire_stale_orders(limit=options["limit"])
        self.stdout.write(f"Reservas encerradas: {count}. Pedidos pagos não foram alterados.")
