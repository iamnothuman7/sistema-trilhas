"""Link each ledger row to its editable source, without mutating the ledger."""
from uuid import UUID
from django.db.models import Q
from django.urls import reverse
from .models import Expense, SponsorPayment
from apps.registrations.models import Registration
from apps.events.models import RaffleTicket


def attach_entry_actions(entries, event):
    rows=list(entries)
    expense_ids={};ticket_ids={}
    for row in rows:
        if row.source_reference.startswith('EXPENSE-'):
            try: expense_ids[row.pk]=UUID(row.source_reference[8:44])
            except ValueError: pass
        if row.source_reference.startswith('RAFFLE-'):
            try:ticket_ids[row.pk]=UUID(row.source_reference[7:43])
            except ValueError:pass
    expenses={row.pk:row for row in Expense.objects.filter(event=event,pk__in=expense_ids.values())}
    payments={}
    for payment in SponsorPayment.objects.filter(sponsorship__event=event).select_related('sponsorship').filter(Q(ledger_entry_id__in=[r.pk for r in rows])|Q(reversal_entry_id__in=[r.pk for r in rows])):
        payments[payment.ledger_entry_id]=payments[payment.reversal_entry_id]=payment
    tickets={t.pk:t for t in RaffleTicket.objects.filter(raffle__event=event,source='PAPER',pk__in=ticket_ids.values())}
    registrations={r.source_order_line.order_id:r for r in Registration.objects.filter(event=event,
        source_order_line__order_id__in=[r.order_id for r in rows if r.order_id]).select_related('source_order_line__order')}
    for row in rows:
        row.action_url=''; row.action_label='';row.remove_url=''
        if row.movement_id:
            row.action_url=reverse('gestao:movement_restore' if row.movement.archived_at else 'gestao:movement_edit',args=[event.pk,row.movement_id])
            row.action_label='Restaurar movimentação' if row.movement.archived_at else 'Editar movimentação'
            if not row.movement.archived_at:row.remove_url=reverse('gestao:movement_remove',args=[event.pk,row.movement_id])
        elif row.pk in expense_ids and expense_ids[row.pk] in expenses:
            expense=expenses[expense_ids[row.pk]]
            row.action_url=reverse('gestao:expense_restore' if expense.status=='CANCELED' else 'gestao:expense_edit',args=[event.pk,expense.pk])
            row.action_label='Restaurar despesa' if expense.status=='CANCELED' else 'Editar despesa'
            if expense.status!='CANCELED':row.remove_url=reverse('gestao:expense_remove',args=[event.pk,expense.pk])
        elif row.pk in payments:
            payment=payments[row.pk]
            if payment.reversed_at or payment.sponsorship.archived_at:
                row.action_url=reverse('gestao:sponsor_detail',args=[event.pk,payment.sponsorship_id]);row.action_label='Ver patrocínio e histórico'
            else:
                row.action_url=reverse('gestao:sponsor_payment_edit',args=[event.pk,payment.sponsorship_id,payment.pk]);row.action_label='Editar recebimento'
            if not payment.reversed_at:row.remove_url=reverse('gestao:sponsor_reversal',args=[event.pk,payment.sponsorship_id,payment.pk])
        elif row.pk in ticket_ids and ticket_ids[row.pk] in tickets:
            ticket=tickets[ticket_ids[row.pk]]
            row.action_url=reverse('gestao:raffle_finance',args=[event.pk]);row.action_label='Ver sorteio e histórico'
            if ticket.state!='VOID':
                row.action_url=reverse('gestao:raffle_ticket_edit',args=[event.pk,ticket.pk]);row.action_label='Editar bilhete'
                row.remove_url=reverse('gestao:raffle_ticket_remove',args=[event.pk,ticket.pk])
        elif row.order_id in registrations:
            registration=registrations[row.order_id]
            row.action_url=reverse('gestao:registration_detail',args=[event.pk,registration.pk])
            row.action_label='Abrir inscrição e pagamento'
            if not registration.archived_at and registration.source_order_line.order.status in ('PAID','PARTIALLY_REFUNDED'):
                row.remove_url=reverse('gestao:registration_refund',args=[event.pk,registration.pk])
    return rows
