"""The raffle view reads the same ledger as the event's general finance."""
import csv
from urllib.parse import urlencode
from django.db.models import Q, Sum
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from apps.core.permissions import management_required
from apps.core.utils import cents_to_reais,escape_csv_cell,log_audit
from apps.finance.models import FinancialEntry,FinancialEntryKind as K,Expense,ExpenseStatus
from apps.reporting.forms import DateRangeForm
from apps.reporting.selectors import in_period
from apps.checkout.models import Order
from .models_raffles import MotorcycleRaffle


@management_required
@never_cache
@require_GET
def raffle_finance(request,event_id):
    event=request.management_event
    raffle=get_object_or_404(MotorcycleRaffle,event=event)
    form=DateRangeForm(request.GET)
    valid=form.is_valid()
    entries=FinancialEntry.objects.filter(event=event).filter(Q(raffle=raffle)|Q(kind__in=[K.RECEITA_SORTEIO,K.ESTORNO_SORTEIO]))
    expenses=Expense.objects.filter(event=event,raffle=raffle,status=ExpenseStatus.PENDING)
    if valid:
        start,end=form.cleaned_data['date_start'],form.cleaned_data['date_end']
        entries=in_period(entries,'recognized_at',start,end)
        expenses=in_period(expenses,'due_at',start,end,date_field=True)
    else:
        entries=entries.none();expenses=expenses.none()
    if request.GET.get('export')=='csv' and valid:
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']=f'attachment; filename="financeiro-sorteio-{event.slug}.csv"'
        response.write('\ufeff')
        writer=csv.writer(response,delimiter=';')
        writer.writerow(['Data','Tipo','Descrição','Valor (R$)','Referência'])
        for entry in entries.order_by('recognized_at','pk').iterator():
            writer.writerow([entry.recognized_at.isoformat(),entry.get_kind_display(),escape_csv_cell(entry.description),
                cents_to_reais(entry.amount_cents_signed),escape_csv_cell(entry.source_reference)])
        log_audit(actor=request.user,event_id=event.pk,action='RAFFLE_FINANCE_EXPORTED',entity_type='MotorcycleRaffle',entity_id=raffle.pk,request=request)
        return response
    values=entries.aggregate(received=Sum('amount_cents_signed',filter=Q(kind=K.RECEITA_SORTEIO)),
        refunds=Sum('amount_cents_signed',filter=Q(kind=K.ESTORNO_SORTEIO)),
        fees=Sum('amount_cents_signed',filter=Q(kind=K.TAXA_MERCADO_PAGO)),
        costs=Sum('amount_cents_signed',filter=Q(kind=K.DESPESA)),net=Sum('amount_cents_signed'))
    totals={key:cents_to_reais(-(value or 0) if key in ['refunds','costs','fees'] else (value or 0)) for key,value in values.items()}
    totals['pending']=cents_to_reais(expenses.aggregate(n=Sum('amount_cents'))['n'] or 0)
    return render(request,'gestao/raffles/finance.html',{'event':event,'raffle':raffle,'totals':totals,'date_form':form,
        'page_obj':Paginator(entries.order_by('-recognized_at','-pk'),30).get_page(request.GET.get('page')),
        'digital_count':raffle.tickets.filter(source='REGISTRATION',state='SOLD',registration__commercial_state='ACTIVE',registration__source_order_line__order__status='PAID').count(),
        'paper_sold':raffle.tickets.filter(source='PAPER',state='SOLD').count(),
        'online_count':raffle.tickets.filter(source='ONLINE',state='SOLD',order_line__order__status='PAID',beneficiary_registration__commercial_state='ACTIVE',beneficiary_registration__source_order_line__order__status='PAID').count(),
        'orders_page':Paginator(Order.objects.filter(event=event,lines__raffle=raffle).distinct().order_by('-created_at','-pk'),20).get_page(request.GET.get('orders_page')),
        'filter_query':urlencode({k:v.isoformat() for k,v in form.cleaned_data.items() if v}) if valid else ''},status=200 if valid else 400)
