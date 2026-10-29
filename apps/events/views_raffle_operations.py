"""Private raffle operations. This does not enable public quota checkout."""
from io import BytesIO
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_GET
from apps.core.permissions import management_required, can_manage_event
from apps.core.utils import log_audit
from apps.finance.forms import parse_amount_cents
from apps.finance.models import FinancialEntry, FinancialEntryKind
from .models_raffles import MotorcycleRaffle, RaffleTicket, RaffleSalesPoint
from .raffle_operations import create_paper_batch, record_paper_ticket


def tickets_for_event(raffle):
    from .raffle_results import eligible_tickets
    return eligible_tickets(raffle).select_related('registration__person','point')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def raffle_operations(request, event_id):
    raffle=get_object_or_404(MotorcycleRaffle,event=request.management_event)
    status = 200
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='batch':
                count=create_paper_batch(raffle,request.user,request)
                messages.success(request,f'{count} novos bilhetes gerados. Números existentes foram preservados.')
            elif action=='point':
                name=request.POST.get('name','').strip();contact=request.POST.get('contact','').strip()
                if not name or len(name)>120 or len(contact)>150: raise ValidationError('Informe um nome de até 120 caracteres e contato de até 150.')
                point,_=RaffleSalesPoint.objects.get_or_create(raffle=raffle,name=name,defaults={'contact':contact})
                log_audit(actor=request.user,event_id=event_id,action='RAFFLE_POINT_CREATED',entity_type='RaffleSalesPoint',entity_id=point.pk,request=request)
                messages.success(request,'Ponto cadastrado.')
            elif action=='distribute':
                point=get_object_or_404(RaffleSalesPoint,raffle=raffle,pk=request.POST.get('point'),active=True)
                start,end=int(request.POST.get('start','')),int(request.POST.get('end',''))
                if not raffle.paper_start<=start<=end<=raffle.paper_end: raise ValidationError('Use números dentro da faixa em papel desta edição.')
                with transaction.atomic():
                    from .models import Event
                    Event.objects.select_for_update().get(pk=event_id)
                    locked=MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
                    if hasattr(locked,'result'):raise ValidationError('O resultado já foi registrado. Não distribua novos bilhetes.')
                    count=raffle.tickets.filter(source='PAPER',number__gte=start,number__lte=end,state__in=['AVAILABLE','DISTRIBUTED']).update(point=point,state='DISTRIBUTED')
                    log_audit(actor=request.user,event_id=event_id,action='RAFFLE_TICKETS_DISTRIBUTED',entity_type='MotorcycleRaffle',entity_id=raffle.pk,
                        diff={'point':str(point.pk),'start':start,'end':end,'count':count},request=request)
                messages.success(request,f'{count} bilhete(s) atribuídos ao ponto. Bilhetes pagos ou cancelados não foram alterados.')
            elif action=='sale':
                if not can_manage_event(request.user,request.management_event,'finance'): raise ValidationError('Recebimentos exigem acesso financeiro.')
                number=int(request.POST.get('number',''))
                ticket=get_object_or_404(RaffleTicket,raffle=raffle,number=number,source='PAPER')
                point=get_object_or_404(RaffleSalesPoint,raffle=raffle,pk=request.POST['point']) if request.POST.get('point') else ticket.point
                name=request.POST.get('buyer_name','').strip();phone=request.POST.get('buyer_phone','').strip()
                if len(name)>150 or len(phone)>30: raise ValidationError('Revise o nome e o telefone do comprador.')
                record_paper_ticket(ticket_id=ticket.pk,actor=request.user,point=point,buyer_name=name,buyer_phone=phone,
                    amount_cents=parse_amount_cents(request.POST.get('amount')),confirmed=request.POST.get('confirmed')=='on',request=request)
                messages.success(request,'Recebimento confirmado e lançado no financeiro principal.')
            elif action=='void':
                reason=request.POST.get('reason','').strip()
                if len(reason)<10 or len(reason)>1000: raise ValidationError('Descreva o motivo do cancelamento, de 10 a 1.000 caracteres.')
                with transaction.atomic():
                    from .models import Event
                    Event.objects.select_for_update().get(pk=event_id)
                    ticket=get_object_or_404(RaffleTicket.objects.select_for_update(),raffle=raffle,number=int(request.POST.get('number','')),source='PAPER')
                    if ticket.state!='VOID':
                        if ticket.amount_cents and (not can_manage_event(request.user,request.management_event,'finance') or request.POST.get('returned')!='on'):
                            raise ValidationError('Confirme a devolução do valor recebido antes de cancelar um bilhete pago.')
                        if ticket.amount_cents:
                            FinancialEntry.objects.create(event_id=event_id,raffle=raffle,kind=FinancialEntryKind.ESTORNO_SORTEIO,
                                amount_cents_signed=-ticket.amount_cents,source_reference=f'RAFFLE-{ticket.pk}-VOID',posted_by=request.user,
                                description=f'Devolução do bilhete {ticket.number}')
                        ticket.state='VOID';ticket.save(update_fields=['state'])
                        log_audit(actor=request.user,event_id=event_id,action='RAFFLE_TICKET_VOIDED',entity_type='RaffleTicket',entity_id=ticket.pk,reason=reason,request=request)
                messages.success(request,'Bilhete cancelado. O número permanece no histórico e não será reutilizado.')
            else: raise ValidationError('Selecione uma operação válida.')
        except (ValidationError, ValueError, TypeError) as exc:
            status = 400
            messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Revise os números e os campos informados.')
        else:
            return redirect('gestao:raffle_operations',event_id=event_id)
    points=raffle.sales_points.annotate(total=Count('tickets'),sold=Count('tickets',filter=Q(tickets__state='SOLD')),
        received=Sum('tickets__amount_cents',filter=Q(tickets__state='SOLD'))).order_by('name')
    tickets=raffle.tickets.select_related('point','registration__person').order_by('number')
    number=request.GET.get('number','').strip();state=request.GET.get('state','')
    if number.isascii() and number.isdigit() and len(number)<=10: tickets=tickets.filter(number=int(number))
    elif number: tickets=tickets.none()
    if state in RaffleTicket.State.values: tickets=tickets.filter(state=state)
    return render(request,'gestao/raffles/operations.html',{'event':request.management_event,'raffle':raffle,'points':points,
        'page_obj':Paginator(tickets,50).get_page(request.GET.get('page')),'ticket_states':RaffleTicket.State.choices,
        'total':raffle.tickets.count(),'confirmed':tickets_for_event(raffle).filter(state='SOLD').count(),
        'paper_count':raffle.paper_end-raffle.paper_start+1,'number_filter':number,'state_filter':state,
        'pagination_query': urlencode({'number': number, 'state': state})}, status=status)


@management_required
@never_cache
@require_GET
def raffle_print(request, event_id):
    raffle=get_object_or_404(MotorcycleRaffle,event=request.management_event)
    globo=request.GET.get('formato')=='globo'
    tickets=tickets_for_event(raffle).filter(state='SOLD') if globo else raffle.tickets.filter(source='PAPER').exclude(state='VOID')
    # Every confirmed entry must reach the draw. Loading only the two fields
    # used by the PDF avoids carrying buyer/registration data for large batches.
    tickets=list(tickets.order_by('number').select_related(None).only('number','source'))
    from .raffle_pdf import build_tickets_pdf
    content=build_tickets_pdf(raffle,tickets,globo=globo)
    log_audit(actor=request.user,event_id=event_id,action='RAFFLE_TICKETS_EXPORTED',entity_type='MotorcycleRaffle',entity_id=raffle.pk,
        diff={'format':'globo' if globo else 'paper','ticket_count':len(tickets)},request=request)
    response=HttpResponse(content,content_type='application/pdf')
    response['Content-Disposition']=f'attachment; filename="bilhetes-{raffle.event.slug}-{ "globo" if globo else "papel" }.pdf"'
    return response
