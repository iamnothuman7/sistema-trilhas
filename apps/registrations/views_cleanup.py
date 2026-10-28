"""Remove unpaid reservations from the working list while preserving payment history."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.checkout.models import Order, OrderStatus, ReceivableDue
from apps.payments.models import ChargeState
from apps.payments.services import _release_capacity
from apps.core.permissions import management_required
from apps.core.utils import log_audit
from .models import Registration, CommercialState


@transaction.atomic
def archive_unpaid_order(order_id, *, actor, request=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if (order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT, OrderStatus.EXPIRED, OrderStatus.CANCELED}
            or order.manual_evidences.exists() or order.financial_entries.exists()
            or order.charge_attempts.filter(state=ChargeState.SUCCEEDED).exists()):
        raise ValidationError('Uma compra com recebimento não pode ser removida como reserva sem pagamento.')
    if not order.is_expired() and order.status not in {OrderStatus.EXPIRED, OrderStatus.CANCELED} and order.charge_attempts.filter(
            state__in=[ChargeState.NEW, ChargeState.SENT, ChargeState.PENDING, ChargeState.UNKNOWN]).exists():
        raise ValidationError('Este pedido tem uma cobrança em andamento. Aguarde sua confirmação ou expiração antes de remover.')
    _release_capacity(order)
    order.status = OrderStatus.CANCELED
    order.save(update_fields=['status', 'updated_at'])
    count = Registration.objects.filter(source_order_line__order=order).update(
        commercial_state=CommercialState.CANCELED, operational_state='NOT_READY', archived_at=timezone.now(), updated_at=timezone.now())
    ReceivableDue.objects.filter(order=order, allocated_paid_cents=0).update(status='CANCELED')
    log_audit(actor=actor, event_id=order.event_id, action='UNPAID_RESERVATION_ARCHIVED', entity_type='Order', entity_id=order.pk,
        diff={'registrations': count}, request=request)
    return count


@management_required
@never_cache
@require_http_methods(['GET', 'POST'])
def registration_remove(request, event_id, reg_id):
    reg = get_object_or_404(Registration.objects.select_related('person', 'source_order_line__order'), pk=reg_id, event_id=event_id)
    order = reg.source_order_line.order
    error = ''
    if request.method == 'POST':
        if request.POST.get('confirm') != 'yes':
            error = 'Confirme a remoção da reserva.'
        else:
            try:
                count = archive_unpaid_order(order.pk, actor=request.user, request=request)
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            else:
                messages.success(request, f'{count} inscrição(ões) sem pagamento removida(s) da lista. As vagas foram liberadas.')
                return redirect('gestao:registration_list', event_id=event_id)
    return render(request, 'gestao/registrations/remove.html', {'event': request.management_event, 'reg': reg, 'order': order,
        'count': Registration.objects.filter(source_order_line__order=order).count(), 'error': error}, status=400 if error else 200)
