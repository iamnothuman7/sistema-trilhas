"""Apply the confirmed third Trilhão setup without changing credentials or purchases."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.core.utils import log_audit
from apps.events.models import Event, MotorcycleRaffle
from apps.events.raffle_operations import issue_registration_ticket
from apps.identity.models import User
from apps.payments.connection import can_manage_connection
from apps.payments.models import PaymentFeePolicy
from apps.registrations.models import Registration


class Command(BaseCommand):
    help = 'Preparar a configuração confirmada na reunião; sem --apply, apenas mostrar as mudanças.'

    def add_arguments(self, parser):
        parser.add_argument('--event', required=True, help='Slug exato do evento')
        parser.add_argument('--actor', required=True, help='Administrador responsável pela alteração')
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        event = Event.objects.select_for_update().filter(slug=options['event']).first()
        actor = User.objects.filter(email=options['actor'],is_active=True).first()
        if not event or not actor or not can_manage_connection(actor):
            raise CommandError('Evento ou administrador global inválido.')
        if event.edition_number != 3:
            raise CommandError('Esta configuração se destina à terceira edição confirmada na reunião.')
        raffle = MotorcycleRaffle.objects.select_for_update().filter(event=event).first()
        if raffle and raffle.tickets.exists() and (raffle.paper_start,raffle.paper_end)!=(2000,3000):
            raise CommandError('Já há numeração emitida em outra faixa. Revise a configuração no painel.')
        fees = PaymentFeePolicy.objects.filter(pk=1).first()
        self.stdout.write('Evento: ' + event.slug)
        self.stdout.write('Sorteio: 2 motos; um número por inscrição paga; papel 2000–3000 (1.001 bilhetes).')
        self.stdout.write('Tarifas: preservar regras existentes.' if fees else
            'Novas regras: repasse Pix 0,99%, cartão à vista 4,98%, sem comissão e sem parcelamento adicional. Referência pública, editável no painel.')
        if not options['apply']:
            self.stdout.write('Simulação: nenhum dado alterado.')
            return
        raffle = raffle or MotorcycleRaffle(event=event)
        raffle.motorcycle_count = 2
        raffle.registration_tickets_enabled = True
        raffle.paper_start,raffle.paper_end = 2000,3000
        if raffle.title == 'Sorteio de moto': raffle.title = 'Sorteio de 2 motos'
        raffle.save()
        fees, created = PaymentFeePolicy.objects.get_or_create(pk=1,defaults={
            'enabled':True,'pix_bps':99,'card_bps':498,'max_installments':1,'updated_by':actor})
        count=0
        for reg in Registration.objects.filter(event=event,commercial_state='ACTIVE',source_order_line__order__status='PAID',
                raffle_ticket__isnull=True).select_related('source_order_line__order'):
            if issue_registration_ticket(reg): count+=1
        log_audit(actor=actor,event_id=event.pk,action='MEETING_CONFIGURATION_APPLIED',entity_type='Event',entity_id=event.pk,
            diff={'motorcycle_count':2,'paper_start':2000,'paper_end':3000,'new_digital_tickets':count,'fee_policy_created':created})
        self.stdout.write(self.style.SUCCESS(f'Configuração aplicada. {count} número(s) digital(is) emitido(s). Credenciais e status público preservados.'))
