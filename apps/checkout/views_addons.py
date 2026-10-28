import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_http_methods
from apps.core.rate_limits import rate_limit_request
from .views import private_checkout
from .addons import eligible_registrations, inventory_items, reserve_addons


@private_checkout
@login_required
@require_http_methods(['GET','POST'])
def registration_addons(request, registration_id):
    reg=get_object_or_404(eligible_registrations(request.user).select_related('event','person'),pk=registration_id)
    from apps.payments.services import expire_stale_orders
    expire_stale_orders(event_id=reg.event_id)
    items=inventory_items(reg.event)
    if request.method=='POST':
        decision=rate_limit_request(request,'addon-checkout',ip_limit=30,account=str(request.user.pk),account_limit=15,window_seconds=900)
        if not decision.allowed:
            return render(request,'checkout/addons.html',{'reg':reg,'items':items,'error':'Muitas tentativas. Aguarde alguns minutos.'},status=429)
        try:
            token=signing.loads(request.POST.get('request_token',''),salt='addon-purchase',max_age=3600)
            if token['registration']!=str(reg.pk) or token['user']!=str(request.user.pk): raise ValueError
            quantities={}
            for name in request.POST:
                if name.startswith('quantity_'):
                    item_id=str(uuid.UUID(name.removeprefix('quantity_')))
                    quantity=int(request.POST[name] or '0')
                    if quantity<0 or quantity>20: raise ValueError
                    if quantity: quantities[item_id]=quantity
            order=reserve_addons(registration_id=reg.pk,user=request.user,items=quantities,request_key='ADDON-'+str(uuid.UUID(token['key'])))
        except (signing.BadSignature,ValueError,TypeError,KeyError,ValidationError) as exc:
            messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Revise os produtos e tente novamente.')
        else:
            return redirect('checkout:order_payment',order_id=order.pk)
    token=signing.dumps({'registration':str(reg.pk),'user':str(request.user.pk),'key':str(uuid.uuid4())},salt='addon-purchase')
    return render(request,'checkout/addons.html',{'reg':reg,'items':items,'request_token':token})
