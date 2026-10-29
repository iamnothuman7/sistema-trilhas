"""Record the result of the existing physical draw; never invent draw rules."""
import hashlib,json
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.core.utils import log_audit
from .models import Event
from .models_raffles import MotorcycleRaffle,RaffleResult


def eligible_tickets(raffle):
    return raffle.tickets.filter(state='SOLD').filter(
        Q(source='PAPER',paid_at__isnull=False,amount_cents__gt=0) |
        Q(source='REGISTRATION',registration__commercial_state='ACTIVE',registration__archived_at__isnull=True,
          registration__source_order_line__order__status='PAID',registration__source_order_line__order__is_test=False) |
        Q(source='ONLINE',order_line__order__status='PAID',order_line__order__is_test=False,
          beneficiary_registration__commercial_state='ACTIVE',beneficiary_registration__archived_at__isnull=True,
          beneficiary_registration__source_order_line__order__status='PAID',beneficiary_registration__source_order_line__order__is_test=False))


@transaction.atomic
def record_draw_result(*,raffle,actor,numbers,notes,confirmed,request=None):
    Event.objects.select_for_update().get(pk=raffle.event_id)
    raffle=MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
    existing=RaffleResult.objects.filter(raffle=raffle).first()
    if existing:
        if [winner['number'] for winner in existing.winners]==numbers:return existing,False
        raise ValidationError('Este sorteio já tem resultado. Uma nova execução ou substituição está bloqueada.')
    if not confirmed or len(notes.strip())<10:raise ValidationError('Confirme a conferência e descreva como o resultado foi apurado.')
    if not raffle.draw_at or raffle.draw_at>timezone.now() or not raffle.rules_text.strip():
        raise ValidationError('Defina o regulamento e aguarde a data do sorteio antes de registrar o resultado.')
    if len(numbers)!=raffle.motorcycle_count or len(set(numbers))!=len(numbers):
        raise ValidationError(f'Informe {raffle.motorcycle_count} número(s) vencedor(es) distinto(s), na ordem dos prêmios.')
    tickets=list(eligible_tickets(raffle).select_related('registration__person','beneficiary_registration__person').order_by('number'))
    by_number={ticket.number:ticket for ticket in tickets}
    if any(number not in by_number for number in numbers):raise ValidationError('Um dos números não está confirmado e elegível para esta edição.')
    snapshot=[{'id':str(t.pk),'number':t.number,'source':t.source} for t in tickets]
    winners=[]
    for prize,number in enumerate(numbers,1):
        ticket=by_number[number]
        reg=ticket.registration or ticket.beneficiary_registration
        winners.append({'prize':prize,'number':number,'ticket_id':str(ticket.pk),'name':reg.person.full_name if reg else ticket.buyer_name})
    result=RaffleResult.objects.create(raffle=raffle,winners=winners,eligible_snapshot=snapshot,
        snapshot_sha256=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest(),method_notes=notes.strip(),recorded_by=actor)
    raffle.online_sales_enabled=False;raffle.save(update_fields=['online_sales_enabled','updated_at'])
    log_audit(actor=actor,event_id=raffle.event_id,action='RAFFLE_RESULT_RECORDED',entity_type='RaffleResult',entity_id=result.pk,
        diff={'eligible_count':len(snapshot),'numbers':numbers,'sha256':result.snapshot_sha256},request=request)
    return result,True
