import re
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required
from .models_raffles import MotorcycleRaffle,RaffleResult
from .raffle_results import record_draw_result,eligible_tickets


class ResultForm(forms.Form):
    numbers=forms.CharField(label='Números vencedores, na ordem dos prêmios',max_length=150,
        help_text='Transcreva os números apurados no sorteio. Separe com vírgula se houver mais de uma moto.')
    notes=forms.CharField(label='Como o resultado foi apurado',min_length=10,max_length=3000,
        widget=forms.Textarea(attrs={'rows':3}),help_text='Registre o método previsto no regulamento, data e responsáveis pela conferência.')
    confirmed=forms.BooleanField(label='Conferi os números com o resultado oficial. Entendo que não será possível sobrescrever nem realizar novamente este registro.')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget,forms.CheckboxInput):field.widget.attrs['class']='form-control'
    def clean_numbers(self):
        raw=self.cleaned_data['numbers'].strip()
        if not re.fullmatch(r'[0-9]{1,10}(?:\s*,\s*[0-9]{1,10})*',raw):raise forms.ValidationError('Use números separados por vírgula.')
        return [int(value.strip()) for value in raw.split(',')]


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def raffle_result(request,event_id):
    raffle=get_object_or_404(MotorcycleRaffle,event=request.management_event)
    form=ResultForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        try:result,created=record_draw_result(raffle=raffle,actor=request.user,request=request,**form.cleaned_data)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'Resultado registrado e protegido contra nova execução.' if created else 'Este resultado já estava registrado; nada foi duplicado.')
            return redirect('gestao:raffle_result',event_id=event_id)
    from django.utils import timezone
    from urllib.parse import quote
    result=RaffleResult.objects.filter(raffle=raffle).first()
    winners=[]
    for winner in (result.winners if result else []):
        digits=''.join(ch for ch in winner.get('phone','') if ch.isdigit())
        if digits and not digits.startswith('55') and len(digits) in (10,11):digits='55'+digits
        text=f"Parabéns, {winner['name'].split()[0] if winner.get('name') else ''}! O número {winner['number']} ganhou o {winner['prize']}º prêmio do {raffle.title} ({raffle.event.title}). A Equipe Morcego vai combinar a entrega com você."
        winners.append({**winner,'whatsapp':f'https://wa.me/{digits}?text={quote(text)}' if digits else ''})
    checks=[('Regulamento escrito',bool(raffle.rules_text.strip())),
            ('Data do sorteio marcada',bool(raffle.draw_at)),
            ('Horário do sorteio já chegou',bool(raffle.draw_at and raffle.draw_at<=timezone.now())),
            ('Há números concorrendo',eligible_tickets(raffle).exists())]
    return render(request,'gestao/raffles/result.html',{'event':request.management_event,'raffle':raffle,'saved_raffle':True,
        'result':result,'winners':winners,'checks':checks,'ready':all(ok for _,ok in checks),
        'eligible_count':eligible_tickets(raffle).count(),'form':form},status=400 if form.errors else 200)
