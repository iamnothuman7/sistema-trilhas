import uuid,hashlib
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.http import require_http_methods
from apps.core.rate_limits import rate_limit_request
from .addons import eligible_registrations
from .raffle_purchases import available_numbers,reserve_raffle_numbers
from .views import private_checkout


@private_checkout
@login_required
@require_http_methods(['GET','POST'])
def raffle_numbers(request,registration_id):
    reg=get_object_or_404(eligible_registrations(request.user).select_related('person','event'),pk=registration_id)
    from apps.payments.services import expire_stale_orders
    from apps.events.models import MotorcycleRaffle
    expire_stale_orders(event_id=reg.event_id)
    raffle=get_object_or_404(MotorcycleRaffle,event=reg.event,is_public=True)
    status=200;quantity=request.POST.get('quantity','1')
    token=request.POST.get('request_token','')
    if request.method=='POST':
        decision=rate_limit_request(request,'raffle-checkout',ip_limit=30,account=str(request.user.pk),account_limit=15,window_seconds=900)
        if not decision.allowed:status=429;messages.error(request,'Muitas tentativas. Aguarde alguns minutos.')
        else:
            try:
                signed=signing.loads(token,salt='raffle-numbers',max_age=3600)
                if signed['registration']!=str(reg.pk) or signed['user']!=str(request.user.pk):raise ValueError
                if signed.get('rules')!=hashlib.sha256((raffle.rules_text+str(raffle.draw_at)).encode()).hexdigest():
                    raise ValidationError('O regulamento foi atualizado. Leia a versão abaixo antes de continuar.')
                if request.POST.get('rules_accepted')!='yes':raise ValidationError('Leia e aceite o regulamento do sorteio para continuar.')
                order=reserve_raffle_numbers(registration_id=reg.pk,user=request.user,quantity=int(quantity),request_key='RAFFLE-'+str(uuid.UUID(signed['key'])),rules_accepted=True,request=request)
            except (signing.BadSignature,ValueError,TypeError,KeyError,ValidationError) as error:
                status=400;messages.error(request,' '.join(error.messages) if isinstance(error,ValidationError) else 'Revise a quantidade e tente novamente.')
            else:return redirect('checkout:order_payment',order_id=order.pk)
    if request.method=='GET' or status>=400:
        token=signing.dumps({'registration':str(reg.pk),'user':str(request.user.pk),'key':str(uuid.uuid4()),
            'rules':hashlib.sha256((raffle.rules_text+str(raffle.draw_at)).encode()).hexdigest()},salt='raffle-numbers')
    return render(request,'checkout/raffle_numbers.html',{'reg':reg,'raffle':raffle,'available':available_numbers(raffle),
        'request_token':token,'quantity':quantity},status=status)
