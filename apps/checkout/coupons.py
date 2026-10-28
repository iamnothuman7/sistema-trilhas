"""Return only abandoned unpaid coupon reservations, once, without changing history."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Coupon, CapacityHold
from apps.core.utils import log_audit
from .models import Order, OrderCoupon, OrderStatus, ReceivableDue


@transaction.atomic
def release_unpaid_coupon_usage(order):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT, OrderStatus.EXPIRED, OrderStatus.CANCELED}:
        return False
    applied = OrderCoupon.objects.select_for_update().filter(order=order, usage_released_at__isnull=True).first()
    if not applied:
        return False
    # A status alone cannot erase a use which already received money, including
    # an imported/manual receipt or an order later refunded to the purchaser.
    from apps.payments.models import PaymentAllocation
    if (order.manual_evidences.exists() or order.financial_entries.exists() or order.refunds.exists()
            or order.charge_attempts.filter(Q(state='SUCCEEDED') | Q(transactions__posted_revenue_cents__gt=0)
                | Q(transactions__fulfilled_at__isnull=False)).exists()
            or ReceivableDue.objects.filter(order=order, allocated_paid_cents__gt=0).exists()
            or PaymentAllocation.objects.filter(charge_attempt__order=order, amount_cents__gt=0).exists()
            or CapacityHold.objects.filter(order_id=order.pk, status='COMMITTED').exists()):
        return False
    coupon = Coupon.objects.select_for_update().get(pk=applied.coupon_id)
    other_uses = OrderCoupon.objects.filter(coupon=coupon, usage_released_at__isnull=True).exclude(pk=applied.pk).count()
    coupon.used_count = max(0, coupon.used_count - 1, other_uses)
    coupon.save(update_fields=['used_count'])
    applied.usage_released_at = timezone.now()
    applied.save(update_fields=['usage_released_at'])
    log_audit(event_id=order.event_id, action='UNPAID_COUPON_USAGE_RELEASED', entity_type='OrderCoupon', entity_id=applied.pk,
        diff={'order_id': str(order.pk), 'coupon_id': str(coupon.pk), 'code_at_purchase': applied.display_code})
    return True
