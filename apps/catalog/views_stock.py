from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required
from .models import ProductVariant
from .stock import shirt_rows,configure_shirt_stock


class StockForm(forms.Form):
    variant = forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    version = forms.CharField(widget=forms.HiddenInput)
    total = forms.IntegerField(label='Quantidade total cadastrada',min_value=0,max_value=1000000,
        widget=forms.NumberInput(attrs={'class':'form-control','inputmode':'numeric'}))


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def shirt_stock(request,event_id):
    event=request.management_event
    form=StockForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        variant=get_object_or_404(ProductVariant,pk=form.cleaned_data['variant'],product__event=event,kind='SHIRT_SIZE')
        try:
            configure_shirt_stock(event=event,variant_id=variant.pk,total=form.cleaned_data['total'],
                version=form.cleaned_data['version'],actor=request.user,request=request)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'Estoque salvo. Inscrições e visão geral usam estas mesmas quantidades.')
            return redirect('gestao:shirt_stock',event_id=event.pk)

    rows = shirt_rows(event)
    total_received = sum(r['total'] or 0 for r in rows)
    total_delivered = sum(r['delivered'] or 0 for r in rows)
    total_confirmed = sum(r['confirmed'] or 0 for r in rows)
    total_out = total_delivered + total_confirmed
    total_remaining = max(0, total_received - total_out)

    consignment_summary = {
        'total_received': total_received,
        'total_delivered': total_delivered,
        'total_confirmed': total_confirmed,
        'total_out': total_out,
        'total_remaining': total_remaining,
    }

    return render(request,'gestao/catalog/stock.html',{
        'event': event,
        'rows': rows,
        'products': event.products.filter(includes_shirt=True),
        'form': form,
        'consignment': consignment_summary,
    }, status=400 if form.errors else 200)
