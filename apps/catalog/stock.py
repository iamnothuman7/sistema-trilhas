"""Shirt capacity reuses checkout holds; delivered units never silently return."""
import hashlib
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from apps.core.utils import log_audit
from apps.events.models import Event
from apps.registrations.models import Registration,Entitlement
from .models import CapacityResource,CapacityHold,HoldStatus,ProductVariant


def stock_version(resource):
    return hashlib.sha256(str((resource.pk,resource.capacity_total,resource.reserved_count,resource.committed_count)).encode()).hexdigest() if resource else 'new'


def shirt_rows(event):
    resources={r.reference_id:r for r in CapacityResource.objects.filter(event=event,type='SHIRT_SIZE')}
    rows=[]
    for variant in ProductVariant.objects.filter(product__event=event,kind='SHIRT_SIZE').select_related('product').order_by('product__name','pk'):
        resource=resources.get(str(variant.pk))
        registrations=Registration.objects.filter(event=event,source_order_line__product=variant.product,shirt_size=variant.code,
            source_order_line__isnull=False,source_order_line__order__is_test=False)
        active=registrations.filter(archived_at__isnull=True,commercial_state='ACTIVE')
        shirts=Entitlement.objects.filter(registration__in=registrations,benefit_type='CAMISETA')
        delivered=shirts.aggregate(n=Sum('quantity_used'))['n'] or 0
        active_delivered=shirts.filter(registration__in=active).aggregate(n=Sum('quantity_used'))['n'] or 0
        confirmed=max(0,active.count()-active_delivered)
        reserved=resource.reserved_count if resource else registrations.filter(archived_at__isnull=True,commercial_state='RESERVED',
            source_order_line__order__status__in=['RESERVED','AWAITING_PAYMENT'],source_order_line__order__expires_at__gt=timezone.now()).count()
        rows.append(dict(variant=variant,resource=resource,version=stock_version(resource),reserved=reserved,
            confirmed=confirmed,delivered=delivered,total=resource.capacity_total if resource else None,
            available=resource.available_count if resource else None))
    return rows


def reserve_shirt(*,event,variant,line,expires_at):
    if not variant:return
    resource=CapacityResource.objects.select_for_update().filter(event=event,type='SHIRT_SIZE',reference_id=str(variant.pk)).first()
    if not resource:return # Preserve editions sold to order until an actual stock is configured.
    if resource.available_count<1:raise ValidationError(f'Camiseta {variant.label} esgotada. Escolha outro tamanho disponível.')
    CapacityHold.objects.create(resource=resource,order_id=line.order_id,order_line_id=line.pk,quantity=1,
        status=HoldStatus.ACTIVE,expires_at=expires_at,idempotency_key=f'HOLD-SHIRT-{line.pk}-{resource.pk}')
    resource.reserved_count+=1;resource.save(update_fields=['reserved_count'])


@transaction.atomic
def configure_shirt_stock(*,event,variant_id,total,version,actor,request=None):
    Event.objects.select_for_update().get(pk=event.pk)
    variant=ProductVariant.objects.get(pk=variant_id,product__event=event,kind='SHIRT_SIZE')
    resource=CapacityResource.objects.select_for_update().filter(event=event,type='SHIRT_SIZE',reference_id=str(variant.pk)).first()
    if version!=stock_version(resource):raise ValidationError('O estoque mudou em outra aba ou compra. Atualize antes de salvar.')
    if not isinstance(total,int) or not 0<=total<=1000000:raise ValidationError('Informe uma quantidade de 0 a 1.000.000.')
    if not resource:resource=CapacityResource.objects.create(event=event,type='SHIRT_SIZE',reference_id=str(variant.pk),capacity_total=total)
    # Rebuild this resource from actual registrations, not stale numeric counters.
    registrations=Registration.objects.filter(event=event,source_order_line__product=variant.product,shirt_size=variant.code).select_related('source_order_line__order')
    expected={}
    for reg in registrations:
        order=reg.source_order_line.order
        if order.is_test:continue
        used=reg.entitlements.filter(benefit_type='CAMISETA').aggregate(n=Sum('quantity_used'))['n'] or 0
        if used:status=HoldStatus.COMMITTED if reg.commercial_state=='ACTIVE' and not reg.archived_at else HoldStatus.RETAINED
        elif reg.archived_at:continue
        elif reg.commercial_state=='ACTIVE':status=HoldStatus.COMMITTED
        elif reg.commercial_state=='RESERVED' and order.status in ['RESERVED','AWAITING_PAYMENT'] and order.expires_at>timezone.now():status=HoldStatus.ACTIVE
        else:continue
        expected[reg.source_order_line_id]=(reg,status)
    reserved=sum(status==HoldStatus.ACTIVE for _,status in expected.values())
    committed=len(expected)-reserved
    if total<reserved+committed:raise ValidationError(f'Já existem {reserved+committed} unidades reservadas, confirmadas ou entregues. Cadastre ao menos essa quantidade.')
    before={'total':resource.capacity_total,'reserved':resource.reserved_count,'committed':resource.committed_count}
    for line_id,(reg,status) in expected.items():
        CapacityHold.objects.update_or_create(idempotency_key=f'HOLD-SHIRT-{line_id}-{resource.pk}',defaults=dict(resource=resource,
            order_id=reg.source_order_line.order_id,order_line_id=line_id,quantity=1,status=status,expires_at=reg.source_order_line.order.expires_at))
    resource.holds.exclude(order_line_id__in=expected).exclude(status__in=[HoldStatus.RELEASED,HoldStatus.EXPIRED]).update(status=HoldStatus.RELEASED)
    resource.capacity_total=total;resource.reserved_count=reserved;resource.committed_count=committed
    resource.save(update_fields=['capacity_total','reserved_count','committed_count'])
    log_audit(actor=actor,event_id=event.pk,action='SHIRT_STOCK_CONFIGURED',entity_type='CapacityResource',entity_id=resource.pk,
        diff={'before':before,'total':total,'reserved':reserved,'committed':committed},request=request)
    return resource


def move_shirt_hold(reg,target):
    """Called under Event then Registration locks, before changing a size."""
    holds=list(CapacityHold.objects.select_for_update().filter(order_line_id=reg.source_order_line_id,resource__type='SHIRT_SIZE',
        status__in=[HoldStatus.ACTIVE,HoldStatus.COMMITTED]))
    resources={r.pk:r for r in CapacityResource.objects.select_for_update().filter(event_id=reg.event_id,type='SHIRT_SIZE').order_by('pk')}
    destination=next((r for r in resources.values() if r.reference_id==str(target.pk)),None)
    if any(h.resource_id==(destination.pk if destination else None) for h in holds):return
    if destination and destination.available_count<1:raise ValidationError('Não há camiseta disponível nesse tamanho.')
    for hold in holds:
        resource=resources[hold.resource_id];field='reserved_count' if hold.status==HoldStatus.ACTIVE else 'committed_count'
        setattr(resource,field,max(0,getattr(resource,field)-hold.quantity));resource.save(update_fields=[field])
        hold.status=HoldStatus.RELEASED;hold.save(update_fields=['status'])
    if destination:
        state=HoldStatus.COMMITTED if reg.commercial_state=='ACTIVE' else HoldStatus.ACTIVE
        CapacityHold.objects.update_or_create(idempotency_key=f'HOLD-SHIRT-{reg.source_order_line_id}-{destination.pk}',
            defaults=dict(resource=destination,order_id=reg.source_order_line.order_id,order_line_id=reg.source_order_line_id,
                quantity=1,status=state,expires_at=reg.source_order_line.order.expires_at))
        field='committed_count' if state==HoldStatus.COMMITTED else 'reserved_count'
        setattr(destination,field,getattr(destination,field)+1);destination.save(update_fields=[field])
