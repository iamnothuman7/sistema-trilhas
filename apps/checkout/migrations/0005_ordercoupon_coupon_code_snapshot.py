from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def preserve_existing_codes(apps, schema_editor):
    OrderCoupon = apps.get_model('checkout', 'OrderCoupon')
    alias = schema_editor.connection.alias
    batch = []
    for applied in OrderCoupon.objects.using(alias).filter(coupon_code_snapshot='').select_related('coupon').iterator(chunk_size=500):
        applied.coupon_code_snapshot = applied.coupon.code_upper
        batch.append(applied)
        if len(batch) == 500:
            OrderCoupon.objects.using(alias).bulk_update(batch, ['coupon_code_snapshot'], batch_size=500)
            batch = []
    if batch:
        OrderCoupon.objects.using(alias).bulk_update(batch, ['coupon_code_snapshot'], batch_size=500)


def release_proven_unpaid_expired_uses(apps, schema_editor):
    OrderCoupon = apps.get_model('checkout', 'OrderCoupon')
    Coupon = apps.get_model('catalog', 'Coupon')
    ChargeAttempt = apps.get_model('payments', 'ChargeAttempt')
    ManualPaymentEvidence = apps.get_model('payments', 'ManualPaymentEvidence')
    PaymentTransaction = apps.get_model('payments', 'PaymentTransaction')
    PaymentAllocation = apps.get_model('payments', 'PaymentAllocation')
    Refund = apps.get_model('payments', 'Refund')
    FinancialEntry = apps.get_model('finance', 'FinancialEntry')
    ReceivableDue = apps.get_model('checkout', 'ReceivableDue')
    CapacityHold = apps.get_model('catalog', 'CapacityHold')
    alias = schema_editor.connection.alias
    rows = OrderCoupon.objects.using(alias).filter(order__status__in=['EXPIRED', 'CANCELED'], usage_released_at__isnull=True)
    for applied in rows.iterator(chunk_size=500):
        order_id = applied.order_id
        # Migration never calls a provider. An unresolved charge is uncertain,
        # so only orders without such attempts or receipt evidence are released.
        if (ChargeAttempt.objects.using(alias).filter(order_id=order_id).exclude(state__in=['NEW', 'FAILED', 'CANCELED']).exists()
                or PaymentTransaction.objects.using(alias).filter(charge_attempt__order_id=order_id).filter(Q(posted_revenue_cents__gt=0) | Q(fulfilled_at__isnull=False)).exists()
                or ManualPaymentEvidence.objects.using(alias).filter(order_id=order_id).exists()
                or FinancialEntry.objects.using(alias).filter(order_id=order_id).exists()
                or Refund.objects.using(alias).filter(order_id=order_id).exists()
                or PaymentAllocation.objects.using(alias).filter(charge_attempt__order_id=order_id, amount_cents__gt=0).exists()
                or ReceivableDue.objects.using(alias).filter(order_id=order_id, allocated_paid_cents__gt=0).exists()
                or CapacityHold.objects.using(alias).filter(order_id=order_id, status='COMMITTED').exists()):
            continue
        coupon = Coupon.objects.using(alias).get(pk=applied.coupon_id)
        other_uses = OrderCoupon.objects.using(alias).filter(coupon_id=applied.coupon_id, usage_released_at__isnull=True).exclude(pk=applied.pk).count()
        coupon.used_count = max(0, coupon.used_count - 1, other_uses)
        coupon.save(using=alias, update_fields=['used_count'])
        OrderCoupon.objects.using(alias).filter(pk=applied.pk, usage_released_at__isnull=True).update(usage_released_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ('checkout', '0004_orderline_raffle'),
        ('payments', '0002_providerevent_deduplication_key_paymenttransaction'),
        ('finance', '0002_initial'),
    ]
    operations = [
        migrations.AddField(
            model_name='ordercoupon', name='coupon_code_snapshot',
            field=models.CharField('Código do cupom na compra', max_length=50, blank=True, default='', editable=False),
        ),
        migrations.AddField(
            model_name='ordercoupon', name='usage_released_at',
            field=models.DateTimeField('Uso devolvido por reserva sem pagamento', blank=True, null=True, editable=False),
        ),
        migrations.RunPython(preserve_existing_codes, migrations.RunPython.noop),
        migrations.RunPython(release_proven_unpaid_expired_uses, migrations.RunPython.noop),
    ]
