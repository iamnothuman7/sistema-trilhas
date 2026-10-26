from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from uuid import UUID
from apps.core.permissions import management_required
from apps.core.utils import log_audit
from apps.events.models import Event
from .models import Product, Lot, ProductVariant, Coupon, CapacityResource
from .forms_management import ProductForm, LotForm, VariantForm, CouponForm

EDITORS = {'produto': (Product, ProductForm, 'produto'), 'lote': (Lot, LotForm, 'lote'),
           'tamanho': (ProductVariant, VariantForm, 'tamanho de camiseta'), 'cupom': (Coupon, CouponForm, 'cupom')}


@management_required
@never_cache
@require_http_methods(['GET', 'POST'])
def catalog_editor(request, event_id, kind, object_id=None):
    if kind not in EDITORS: raise Http404
    model, form_class, label = EDITORS[kind]
    if object_id and kind != 'tamanho':
        try: object_id = str(UUID(str(object_id)))
        except (ValueError, TypeError): raise Http404
    with transaction.atomic():
        event = get_object_or_404(Event.objects.select_for_update(), pk=event_id)
        resources = []
        if kind == 'lote' and object_id:
            resources = list(CapacityResource.objects.select_for_update().filter(event=event, type='LOT_TOTAL', reference_id=object_id).order_by('pk'))
        queryset = model.objects.select_for_update().filter(**({'product__event': event} if kind == 'tamanho' else {'event': event}))
        try:
            if object_id:
                instance = get_object_or_404(queryset, pk=object_id)
            elif kind in ('lote', 'tamanho'):
                product = get_object_or_404(Product, event=event, pk=request.GET.get('product'))
                instance = model(product=product, **({'event': event, 'opens_at': event.registration_opens_at, 'closes_at': event.registration_closes_at} if kind == 'lote' else {}))
                if kind == 'lote':
                    previous = product.lots.order_by('-opens_at').first()
                    instance.price_cents = previous.price_cents if previous else 12000
                    instance.max_quantity = min(100, event.max_attendees)
            else:
                instance = model(event=event)
        except (ValueError, TypeError, ValidationError): raise Http404
        if kind == 'tamanho' and instance.kind != 'SHIRT_SIZE': raise Http404
        form = form_class(request.POST if request.method == 'POST' else None, instance=instance, event=event)
        if request.method == 'POST' and form.is_valid():
            if kind == 'lote':
                occupied = max([instance.current_sold] + [max(instance.current_sold, resource.committed_count) + resource.reserved_count for resource in resources])
                if form.cleaned_data['max_quantity'] < occupied:
                    form.add_error('max_quantity', f'Existem {occupied} vagas vendidas ou reservadas. Mantenha ao menos essa quantidade.')
            if not form.errors:
                before = form.original_version
                saved = form.save()
                if kind == 'lote':
                    for resource in resources:
                        resource.capacity_total = saved.max_quantity
                        resource.save(update_fields=['capacity_total'])
                log_audit(request=request, event_id=event.pk, action='CATALOG_UPDATED' if object_id else 'CATALOG_CREATED',
                    entity_type=model.__name__, entity_id=saved.pk, diff={'fields': form.changed_data, 'previous_version': before})
                messages.success(request, f'{label.capitalize()} salvo. Os pedidos existentes mantêm os valores e benefícios contratados.')
                return redirect('gestao:catalog', event_id=event.pk)
    return render(request, 'gestao/catalog/editor.html', {'event': event, 'form': form, 'item': instance,
        'editor_title': ('Editar ' if object_id else 'Novo ') + label}, status=400 if request.method == 'POST' else 200)
