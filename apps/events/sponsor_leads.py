"""Prospecting is separate from confirmed agreements and actual receipts."""
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.utils import log_audit
from .models import Event,Sponsor,SponsorProfile,SponsorLead
from .models_sponsors import sponsor_name_key
from .sponsorships import brand_values


@transaction.atomic
def convert_lead(*,event,lead_id,version,actor,profile_id=None,request=None):
    Event.objects.select_for_update().get(pk=event.pk)
    lead=SponsorLead.objects.select_for_update().get(pk=lead_id,event=event)
    if lead.archived_at:raise ValidationError('Restaure o interessado antes de confirmar o patrocínio.')
    if lead.sponsor_id:return lead.sponsor,False
    if lead.revision!=version:raise ValidationError('Este contato mudou em outra aba. Atualize antes de confirmar.')
    profiles=SponsorProfile.objects.select_for_update().filter(organization=event.organization)
    if profile_id:
        profile=profiles.filter(pk=profile_id).first()
        if not profile:raise ValidationError('Selecione uma marca desta organização.')
    else:
        matches=[p for p in profiles if sponsor_name_key(p.name)==lead.name_key]
        if len(matches)>1:raise ValidationError('Há marcas com o mesmo nome. Escolha o cadastro existente para evitar duplicidade.')
        profile=matches[0] if matches else SponsorProfile.objects.create(organization=event.organization,
            name=lead.name,contact_name=lead.contact_name,contact_phone=lead.phone,
            notes='\n'.join(value for value in [lead.address,lead.notes] if value))
    if profile.archived_at:raise ValidationError('Esta marca está excluída. Restaure o cadastro antes de confirmar.')
    sponsor,created=Sponsor.objects.get_or_create(event=event,profile=profile,defaults={**brand_values(profile),'is_public':False})
    if sponsor.archived_at:raise ValidationError('Esta participação está excluída. Restaure o patrocínio antes de confirmar.')
    lead.sponsor=sponsor;lead.status=SponsorLead.Status.CONFIRMED;lead.revision+=1
    lead.save(update_fields=['sponsor','status','revision','updated_at'])
    if lead.previous_edition:
        import uuid
        from .models import SponsorHistory
        SponsorHistory.objects.get_or_create(request_key=uuid.uuid5(uuid.NAMESPACE_URL,f'trilhas-sponsor-lead-history:{lead.pk}'),
            defaults={'profile':profile,'edition':lead.previous_edition,'amount_cents':lead.previous_amount_cents,
                'notes':lead.previous_notes,'created_by':actor})
    log_audit(actor=actor,event_id=event.pk,action='SPONSOR_LEAD_CONVERTED',entity_type='SponsorLead',entity_id=lead.pk,
        diff={'sponsor_id':sponsor.pk,'profile_id':profile.pk,'new_participation':created,'receipt_created':False},request=request)
    return sponsor,True


@transaction.atomic
def archive_lead(*,event,lead_id,revision,actor,restore=False,reason='',request=None):
    from django.utils import timezone
    Event.objects.select_for_update().get(pk=event.pk)
    lead=SponsorLead.objects.select_for_update().get(pk=lead_id,event=event)
    if bool(lead.archived_at)!=bool(restore):return lead,False
    if lead.revision!=revision:raise ValidationError('Este contato mudou em outra aba. Atualize antes de continuar.')
    if not 5<=len(reason.strip())<=1000:raise ValidationError('Descreva o motivo, entre 5 e 1.000 caracteres.')
    lead.archived_at=None if restore else timezone.now()
    lead.revision+=1;lead.save(update_fields=['archived_at','revision','updated_at'])
    log_audit(actor=actor,event_id=event.pk,action='SPONSOR_LEAD_RESTORED' if restore else 'SPONSOR_LEAD_REMOVED',
        entity_type='SponsorLead',entity_id=lead.pk,reason=reason,diff={'sponsor_preserved':lead.sponsor_id},request=request)
    return lead,True
