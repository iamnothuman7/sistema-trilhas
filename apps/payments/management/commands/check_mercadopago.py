from django.core.management.base import BaseCommand, CommandError
from apps.payments.gateway import MercadoPagoGateway, GatewayError


class Command(BaseCommand):
    help = "Verifica configuração sem exibir segredos. --verify-account consulta apenas /users/me."

    def add_arguments(self, parser):
        parser.add_argument("--verify-account", action="store_true", help="Consulta autenticada, somente leitura, da conta própria configurada.")

    def handle(self, *args, **options):
        gateway = MercadoPagoGateway()
        self.stdout.write("Fonte: " + {"panel": "painel (snapshot ativo)", "environment": "ambiente do servidor", "unavailable": "credenciais salvas indisponíveis"}.get(gateway.configuration_source, "configuração"))
        self.stdout.write(f"Ambiente: {gateway.environment}")
        issues = gateway.configuration_issues(card=True)
        self.stdout.write("Configuração: " + ("pendente: " + ", ".join(issues) if issues else "completa"))
        self.stdout.write("Webhook: " + ("assinatura configurada" if "MP_WEBHOOK_SECRET" not in issues else "assinatura pendente"))
        if options["verify_account"]:
            try:
                account = gateway.get_account()
            except GatewayError as exc:
                raise CommandError(f"Não foi possível consultar a conta ({exc.code}).") from exc
            account_id = str(account.get("id", ""))
            if not account_id.isdigit():
                raise CommandError("Resposta sem identificador de conta válido.")
            self.stdout.write(f"ID da conta autenticada: {account_id}")
            if account_id != gateway.collector_id:
                raise CommandError("MP_COLLECTOR_ID não corresponde à conta autenticada. Corrija antes de cobrar.")
            self.stdout.write(self.style.SUCCESS("Conta recebedora conferida. Nenhuma cobrança foi criada."))
        if issues:
            raise CommandError("Pagamentos online permanecem indisponíveis até completar a configuração da Equipe Morcego.")
        self.stdout.write(self.style.SUCCESS("Configuração local válida; isto não comprova uma transação real nem a entrega do webhook."))
