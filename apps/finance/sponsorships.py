"""Atomic sponsorship receipts: one payment, one ledger entry, reversible history."""
import hashlib
import json
import uuid
from datetime import datetime, time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.permissions import can_manage_event
from apps.core.utils import log_audit
from apps.events.models import Sponsor
from apps.events.sponsorships import decorate_balance
from apps.reporting.selectors import event_timezone
from .models import FinancialEntry, FinancialEntryKind, SponsorPayment, SponsorPaymentMethod


def lock_sponsorship(event, sponsor_id, actor):
    if not can_manage_event(actor, event, "finance"):
        raise PermissionDenied("Acesso restrito à equipe financeira deste evento.")
    # A write before any reads also serializes receipts on SQLite, whose
    # select_for_update is a no-op. All balance mutations use this same lock.
    if not Sponsor.objects.filter(pk=sponsor_id, event=event).update(finance_version=F("finance_version") + 1):
        raise Sponsor.DoesNotExist
    return decorate_balance(Sponsor.objects.select_for_update().get(pk=sponsor_id, event=event))


@transaction.atomic
def update_sponsor_agreement(*, event, sponsor_id, data, actor, request=None):
    sponsor = lock_sponsorship(event, sponsor_id, actor)
    if sponsor.archived_at:raise ValidationError('Restaure o patrocínio antes de alterar o acordo.')
    if data.get("version") != sponsor.finance_version - 1:
        raise ValidationError("Este acordo mudou em outra tela. Reabra a página para conferir os valores antes de salvar.")
    amount = data.get("amount")
    if amount is not None and (not isinstance(amount, int) or amount < 0 or amount > 99999999999):
        raise ValidationError("Informe um valor combinado válido.")
    if sponsor.received_cents and (amount is None or amount < sponsor.received_cents):
        raise ValidationError("O valor combinado não pode ser menor que o total já recebido. Confira ou reverta a baixa incorreta primeiro.")
    before = {"amount_cents": sponsor.agreed_amount_cents, "due_at": str(sponsor.due_at or "")}
    sponsor.agreed_amount_cents, sponsor.due_at = amount, data.get("due_at")
    sponsor.agreement_notes = data.get("agreement_notes", "")
    sponsor.save(update_fields=["agreed_amount_cents", "due_at", "agreement_notes"])
    log_audit(actor=actor, event_id=event.pk, action="SPONSOR_AGREEMENT_UPDATED", entity_type="Sponsor", entity_id=sponsor.pk,
        diff={"before": before, "amount_cents": amount, "due_at": str(sponsor.due_at or "")}, request=request)
    return sponsor


def record_sponsor_payment(*, event, sponsor_id, data, actor, request=None):
    amount = data["amount"]
    if not isinstance(amount, int) or amount <= 0 or amount > 99999999999:
        raise ValidationError("Informe um valor recebido maior que zero.")
    if data["received_on"] > timezone.localdate():
        raise ValidationError("A data do recebimento não pode estar no futuro.")
    if data["method"] not in SponsorPaymentMethod.values or not data.get("confirm_received"):
        raise ValidationError("Confirme a forma e o recebimento do valor.")
    payload = {"event": str(event.pk), "sponsor": sponsor_id, "actor": str(actor.pk), "amount": amount,
        "received_on": str(data["received_on"]), "method": data["method"], "reference": data.get("reference", ""), "notes": data.get("notes", "")}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def existing():
        previous = SponsorPayment.objects.filter(idempotency_key=data["idempotency_key"]).first()
        if previous and previous.submission_hash != digest:
            raise ValidationError("Este formulário já foi usado com outros dados. Reabra a página para um novo recebimento.")
        return previous

    try:
        with transaction.atomic():
            sponsor = lock_sponsorship(event, sponsor_id, actor)
            if sponsor.archived_at:raise ValidationError('Restaure o patrocínio antes de registrar recebimentos.')
            previous = existing()
            if previous:
                return previous, False
            if sponsor.balance_cents is None:
                raise ValidationError("Defina o valor combinado antes de registrar um recebimento.")
            if amount > sponsor.balance_cents:
                raise ValidationError("O recebimento supera o saldo pendente. Confira o valor ou atualize o acordo.")
            payment_id = uuid.uuid4()
            entry = FinancialEntry.objects.create(event=event, kind=FinancialEntryKind.RECEITA_PATROCINIO,
                amount_cents_signed=amount, source_reference=f"SPONSOR-{payment_id}", posted_by=actor,
                description=f"Patrocínio: {sponsor.name}",
                recognized_at=timezone.make_aware(datetime.combine(data["received_on"], time(hour=12)), event_timezone(event)))
            payment = SponsorPayment.objects.create(id=payment_id, sponsorship=sponsor, amount_cents=amount,
                received_on=data["received_on"], method=data["method"], reference=data.get("reference", ""), notes=data.get("notes", ""),
                created_by=actor, idempotency_key=data["idempotency_key"], submission_hash=digest, ledger_entry=entry)
            log_audit(actor=actor, event_id=event.pk, action="SPONSOR_PAYMENT_RECEIVED", entity_type="SponsorPayment", entity_id=payment.pk,
                diff={"sponsor_id": sponsor.pk, "amount_cents": amount, "method": payment.method, "received_on": str(payment.received_on)}, request=request)
            return payment, True
    except IntegrityError:
        previous = existing()
        if previous:
            return previous, False
        raise


@transaction.atomic
def reverse_sponsor_payment(*, event, sponsor_id, payment_id, actor, reason, request=None, correct_original_period=False):
    reason = (reason or "").strip()
    if not 5 <= len(reason) <= 500:
        raise ValidationError("Descreva o motivo da reversão, de 5 a 500 caracteres.")
    sponsor = lock_sponsorship(event, sponsor_id, actor)
    payment = SponsorPayment.objects.select_for_update().get(pk=payment_id, sponsorship=sponsor)
    if payment.reversed_at:
        return payment, False
    now = timezone.now()
    entry = FinancialEntry.objects.create(event=event, kind=FinancialEntryKind.ESTORNO_PATROCINIO,
        amount_cents_signed=-payment.amount_cents, source_reference=f"SPONSOR-REVERSAL-{payment.pk}",
        posted_by=actor, recognized_at=payment.ledger_entry.recognized_at if correct_original_period else now, description=f"Reversão de patrocínio: {sponsor.name}")
    payment.reversed_at, payment.reversed_by = now, actor
    payment.reversal_reason, payment.reversal_entry = reason, entry
    payment.save(update_fields=["reversed_at", "reversed_by", "reversal_reason", "reversal_entry"])
    log_audit(actor=actor, event_id=event.pk, action="SPONSOR_PAYMENT_REVERSED", entity_type="SponsorPayment", entity_id=payment.pk,
        diff={"sponsor_id": sponsor.pk, "amount_cents": payment.amount_cents}, reason=reason, request=request)
    return payment, True


@transaction.atomic
def edit_sponsor_payment(*,event,sponsor_id,payment_id,data,actor,request=None):
    from apps.core.record_actions import record_version
    sponsor=lock_sponsorship(event,sponsor_id,actor)
    old=SponsorPayment.objects.select_for_update().get(pk=payment_id,sponsorship=sponsor)
    previous=SponsorPayment.objects.filter(idempotency_key=data['idempotency_key']).first()
    if previous:
        # record_sponsor_payment checks the complete original submission fingerprint.
        return record_sponsor_payment(event=event,sponsor_id=sponsor_id,data=data,actor=actor,request=request)
    if old.reversed_at:raise ValidationError('Esta baixa já foi revertida ou substituída. Edite o recebimento atual.')
    if data['version']!=record_version(old):raise ValidationError('Este recebimento mudou em outra aba. Atualize a página.')
    if not 5<=len(data.get('reason','').strip())<=500:raise ValidationError('Informe o motivo da correção.')
    reverse_sponsor_payment(event=event,sponsor_id=sponsor_id,payment_id=old.pk,actor=actor,reason=data['reason'],request=request,correct_original_period=True)
    replacement,_=record_sponsor_payment(event=event,sponsor_id=sponsor_id,data=data,actor=actor,request=request)
    log_audit(actor=actor,event_id=event.pk,action='SPONSOR_PAYMENT_CORRECTED',entity_type='SponsorPayment',entity_id=old.pk,
        diff={'replacement_id':str(replacement.pk),'amount_cents':replacement.amount_cents},reason=data['reason'],request=request)
    return replacement,True
