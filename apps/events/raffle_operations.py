"""Internal ticket numbering, paper distribution and auditable manual receipts."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from apps.core.utils import log_audit
from apps.finance.models import FinancialEntry, FinancialEntryKind
from .models_raffles import MotorcycleRaffle, RaffleTicket


@transaction.atomic
def issue_registration_ticket(reg):
    if reg.commercial_state != 'ACTIVE' or reg.source_order_line.order.status != 'PAID': return None
    raffle=MotorcycleRaffle.objects.select_for_update().filter(event_id=reg.event_id,registration_tickets_enabled=True).first()
    if not raffle: return None
    existing=RaffleTicket.objects.filter(registration=reg).first()
    if existing: return existing
    number=(raffle.tickets.filter(source='REGISTRATION').aggregate(n=Max('number'))['n'] or 0)+1
    if raffle.paper_start<=number<=raffle.paper_end: number=raffle.paper_end+1
    return RaffleTicket.objects.create(raffle=raffle,registration=reg,source='REGISTRATION',state='SOLD',number=number,paid_at=timezone.now())


@transaction.atomic
def create_paper_batch(raffle, actor, request=None):
    raffle=MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
    if not 0<=raffle.paper_end-raffle.paper_start<5000:
        raise ValidationError('A faixa deve conter até 5.000 números.')
    existing=dict(raffle.tickets.filter(number__gte=raffle.paper_start,number__lte=raffle.paper_end).values_list('number','source'))
    if any(source!='PAPER' for source in existing.values()):
        raise ValidationError('Há números de inscrições dentro desta faixa. Revise a numeração antes de imprimir.')
    rows=[RaffleTicket(raffle=raffle,number=n,source='PAPER') for n in range(raffle.paper_start,raffle.paper_end+1) if n not in existing]
    RaffleTicket.objects.bulk_create(rows)
    log_audit(actor=actor,event_id=raffle.event_id,action='RAFFLE_PAPER_BATCH_CREATED',entity_type='MotorcycleRaffle',entity_id=raffle.pk,
        diff={'start':raffle.paper_start,'end':raffle.paper_end,'created':len(rows)},request=request)
    return len(rows)


@transaction.atomic
def record_paper_ticket(*, ticket_id, actor, point, buyer_name, buyer_phone, amount_cents, confirmed, request=None):
    ticket=RaffleTicket.objects.select_for_update().select_related('raffle').get(pk=ticket_id)
    if ticket.source!='PAPER' or ticket.state=='VOID': raise ValidationError('Este número não está disponível para registrar venda em papel.')
    if point and point.raffle_id!=ticket.raffle_id: raise ValidationError('O ponto precisa pertencer a este sorteio.')
    if ticket.state=='SOLD':
        if (ticket.amount_cents == amount_cents and ticket.buyer_name == buyer_name and ticket.buyer_phone == buyer_phone
                and ticket.point_id == (point.pk if point else None) and confirmed):
            return ticket
        raise ValidationError('Este bilhete já tem recebimento confirmado. Não altere o valor de um pagamento registrado.')
    if not confirmed or amount_cents<=0 or not buyer_name:
        raise ValidationError('Informe o comprador, o valor recebido e confirme a conferência do pagamento.')
    ticket.point=point;ticket.buyer_name=buyer_name;ticket.buyer_phone=buyer_phone;ticket.amount_cents=amount_cents
    ticket.state='SOLD';ticket.paid_at=timezone.now();ticket.recorded_by=actor;ticket.save()
    FinancialEntry.objects.create(event_id=ticket.raffle.event_id,kind=FinancialEntryKind.RECEITA_SORTEIO,
        amount_cents_signed=amount_cents,source_reference=f'RAFFLE-{ticket.pk}-SALE',posted_by=actor,
        description=f'Bilhete {ticket.number} — {ticket.raffle.title}'[:255])
    log_audit(actor=actor,event_id=ticket.raffle.event_id,action='RAFFLE_PAPER_PAYMENT_CONFIRMED',entity_type='RaffleTicket',entity_id=ticket.pk,
        diff={'number':ticket.number,'amount_cents':amount_cents},request=request)
    return ticket
