"""Link each ledger row to its editable source, without mutating the ledger."""
from uuid import UUID
from django.db.models import Q
from django.urls import reverse
from .models import Expense, SponsorPayment
from apps.registrations.models import Registration


def attach_entry_actions(entries, event):
    rows=list(entries)
    expense_ids={}
    for row in rows:
        if row.source_reference.startswith('EXPENSE-'):
            try: expense_ids[row.pk]=UUID(row.source_reference[8:44])
            except ValueError: pass
    expenses={row.pk:row for row in Expense.objects.filter(event=event,pk__in=expense_ids.values())}
    payments={}
    for payment in SponsorPayment.objects.filter(sponsorship__event=event).filter(Q(ledger_entry_id__in=[r.pk for r in rows])|Q(reversal_entry_id__in=[r.pk for r in rows])):
        payments[payment.ledger_entry_id]=payments[payment.reversal_entry_id]=payment.sponsorship_id
    registrations={r.source_order_line.order_id:r.pk for r in Registration.objects.filter(event=event,
        source_order_line__order_id__in=[r.order_id for r in rows if r.order_id]).select_related('source_order_line')}
    for row in rows:
        row.action_url=''; row.action_label=''
        if row.movement_id:
            row.action_url=reverse('gestao:movement_restore' if row.movement.archived_at else 'gestao:movement_edit',args=[event.pk,row.movement_id])
            row.action_label='Restaurar movimentação' if row.movement.archived_at else 'Editar movimentação'
        elif row.pk in expense_ids and expense_ids[row.pk] in expenses:
            expense=expenses[expense_ids[row.pk]]
            row.action_url=reverse('gestao:expense_restore' if expense.status=='CANCELED' else 'gestao:expense_edit',args=[event.pk,expense.pk])
            row.action_label='Restaurar despesa' if expense.status=='CANCELED' else 'Editar despesa'
        elif row.pk in payments:
            row.action_url=reverse('gestao:sponsor_detail',args=[event.pk,payments[row.pk]])
            row.action_label='Gerenciar patrocínio'
        elif row.order_id in registrations:
            row.action_url=reverse('gestao:registration_detail',args=[event.pk,registrations[row.order_id]])
            row.action_label='Abrir inscrição e pagamento'
    return rows
