import hashlib
import json
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.utils import log_audit
from .models import Expense, ExpenseStatus, FinancialEntry, FinancialEntryKind
from .receipts import receipt_digest, save_receipt, with_receipt_cleanup


def _post_expense(expense, actor):
    # A stable source links the expense to its ledger entry; row locks protect repeats.
    FinancialEntry.objects.get_or_create(
        event=expense.event,
        kind=FinancialEntryKind.DESPESA,
        source_reference=f"EXPENSE-{expense.pk}",
        defaults={
            'raffle_id':expense.raffle_id,
            "amount_cents_signed": -expense.amount_cents,
            "description": f"Despesa: {expense.description}",
            "posted_by": actor,
            "recognized_at": timezone.make_aware(datetime.combine(expense.paid_at, time(hour=12))),
        },
    )


@with_receipt_cleanup
def create_expense(*, event, data, actor, request=None, _saved_receipts):
    """Create expense, ledger and audit together; repeated submissions return the same expense."""
    vendor = data.get("vendor_id")
    if vendor and vendor.organization_id != event.organization_id:
        raise ValidationError("O fornecedor não pertence à organização deste evento.")
    payload = {
        "event": str(event.pk), "description": data["description"],
        "category": data["category"], "amount_cents": data["amount"],
        "vendor": str(vendor.pk) if vendor else None,
        "due_at": str(data["due_at"]), "paid_at": str(data["paid_at"]),
        "status": data["status"],
    }
    proof = data.get('proof_asset')
    raffle=data.get('raffle')
    if raffle and raffle.event_id!=event.pk:raise ValidationError('O sorteio pertence a outro evento.')
    if raffle:payload['raffle_id']=str(raffle.pk)
    if proof: payload['receipt_sha256'] = receipt_digest(proof)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    try:
        with transaction.atomic():
            previous = Expense.objects.select_for_update().filter(idempotency_key=data["idempotency_key"]).first()
            if previous:
                if previous.event_id != event.pk or previous.submission_hash != digest:
                    raise ValidationError("Esta identificação já foi usada com outros dados. Reabra o formulário.")
                return previous, False
            expense = Expense.objects.create(
                event=event, raffle=raffle, vendor=vendor, category=data["category"],
                description=data["description"], amount_cents=data["amount"],
                due_at=data["due_at"], paid_at=data["paid_at"], status=data["status"],
                idempotency_key=data["idempotency_key"], submission_hash=digest,
            )
            save_receipt(expense, proof, _saved_receipts)
            if expense.status == ExpenseStatus.PAID:
                _post_expense(expense, actor)
            log_audit(actor=actor, event_id=event.pk, action="EXPENSE_CREATED", entity_type="Expense", entity_id=expense.pk,
                      diff={"amount_cents": expense.amount_cents, "status": expense.status,
                            "receipt_sha256": payload.get('receipt_sha256','')}, request=request)
            return expense, True
    except IntegrityError:
        # A concurrent request may have committed the same unique submission key.
        previous = Expense.objects.filter(idempotency_key=data["idempotency_key"]).first()
        if previous and previous.event_id == event.pk and previous.submission_hash == digest:
            return previous, False
        if previous:
            raise ValidationError("Esta identificação já foi usada com outros dados. Reabra o formulário.")
        raise


@with_receipt_cleanup
@transaction.atomic
def settle_expense(*, event, expense_id, actor, paid_at, proof=None, request=None, _saved_receipts):
    expense = Expense.objects.select_for_update().get(pk=expense_id, event=event)
    if paid_at > timezone.localdate():
        raise ValidationError("Um pagamento realizado não pode ter data futura.")
    if expense.status == ExpenseStatus.PAID:
        if save_receipt(expense, proof, _saved_receipts):
            log_audit(actor=actor,event_id=event.pk,action='EXPENSE_RECEIPT_ATTACHED',entity_type='Expense',entity_id=expense.pk,
                diff={'receipt_sha256':receipt_digest(proof)},request=request)
        return expense, False
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError("Somente uma despesa a pagar pode ser quitada.")
    expense.status = ExpenseStatus.PAID
    expense.paid_at = paid_at
    expense.save(update_fields=["status", "paid_at"])
    save_receipt(expense, proof, _saved_receipts)
    _post_expense(expense, actor)
    log_audit(actor=actor, event_id=event.pk, action="EXPENSE_PAID", entity_type="Expense", entity_id=expense.pk,
              diff={"amount_cents": expense.amount_cents, "paid_at": paid_at.isoformat(), 'receipt_sha256':receipt_digest(proof)}, request=request)
    return expense, True


@with_receipt_cleanup
@transaction.atomic
def attach_expense_receipt(*,event,expense_id,actor,proof,request=None,_saved_receipts):
    expense=Expense.objects.select_for_update().get(pk=expense_id,event=event)
    if expense.status==ExpenseStatus.CANCELED:raise ValidationError('Uma despesa cancelada não recebe novos comprovantes.')
    if not proof:raise ValidationError('Selecione o comprovante da saída.')
    changed=save_receipt(expense,proof,_saved_receipts)
    if changed:log_audit(actor=actor,event_id=event.pk,action='EXPENSE_RECEIPT_ATTACHED',entity_type='Expense',entity_id=expense.pk,
        diff={'receipt_sha256':receipt_digest(proof)},request=request)
    return expense,changed


@with_receipt_cleanup
@transaction.atomic
def update_expense(*,event,expense_id,data,actor,request=None,_saved_receipts):
    from .forms import expense_version
    expense=Expense.objects.select_for_update().get(pk=expense_id,event=event)
    if data.get('version')!=expense_version(expense):raise ValidationError('Esta despesa já foi alterada. Atualize a página.')
    if expense.status==ExpenseStatus.CANCELED:raise ValidationError('O histórico de uma despesa anulada não pode ser reescrito.')
    if expense.status==ExpenseStatus.PAID and data['status']!=ExpenseStatus.PAID:raise ValidationError('Use Anular lançamento para uma despesa paga.')
    vendor=data.get('vendor_id')
    if vendor and vendor.organization_id!=event.organization_id:raise ValidationError('Fornecedor de outra organização.')
    reason=data.get('reason','').strip()
    if len(reason)<10:raise ValidationError('Descreva o motivo da correção.')
    if data['paid_at'] and data['paid_at']>timezone.localdate():raise ValidationError('Um pagamento realizado não pode ter data futura.')
    old_amount,old_paid,old_status,old_raffle=expense.amount_cents,expense.paid_at,expense.status,expense.raffle_id
    raffle=data.get('raffle')
    if raffle and raffle.event_id!=event.pk:raise ValidationError('O sorteio pertence a outro evento.')
    version=data['version']
    for field in ['description','category','due_at','paid_at','status']:setattr(expense,field,data[field])
    expense.vendor=vendor;expense.amount_cents=data['amount'];expense.raffle=raffle;expense.save()
    save_receipt(expense,data.get('proof_asset'),_saved_receipts)
    if old_status==ExpenseStatus.PAID and (old_amount!=expense.amount_cents or old_paid!=expense.paid_at or old_raffle!=expense.raffle_id):
        if not old_paid:raise ValidationError('Corrija a data do pagamento original antes de ajustar esta saída.')
        for suffix,amount,date in [('REVERSAL',old_amount,old_paid),('CORRECTION',-expense.amount_cents,expense.paid_at)]:
            FinancialEntry.objects.create(event=event,raffle_id=old_raffle if suffix=='REVERSAL' else expense.raffle_id,kind=FinancialEntryKind.DESPESA,amount_cents_signed=amount,
                recognized_at=timezone.make_aware(datetime.combine(date,time(hour=12))),posted_by=actor,
                source_reference=f'EXPENSE-{expense.pk}-{version[:20]}-{suffix}',description=f'Correção de despesa: {expense.description}'[:255])
    elif old_status==ExpenseStatus.PENDING and expense.status==ExpenseStatus.PAID:_post_expense(expense,actor)
    log_audit(actor=actor,event_id=event.pk,action='EXPENSE_EDITED',entity_type='Expense',entity_id=expense.pk,reason=reason,
        diff={'old_amount_cents':old_amount,'amount_cents':expense.amount_cents,'old_status':old_status,'status':expense.status},request=request)
    return expense


@transaction.atomic
def void_paid_expense(*,event,expense_id,actor,version,reason,confirmed,request=None):
    from .forms import expense_version
    expense=Expense.objects.select_for_update().get(pk=expense_id,event=event)
    if expense.status==ExpenseStatus.CANCELED:return expense,False
    if version!=expense_version(expense):raise ValidationError('A despesa mudou. Atualize a página antes de anular.')
    if expense.status!=ExpenseStatus.PAID:raise ValidationError('Use Cancelar para uma despesa ainda não paga.')
    if not expense.paid_at:raise ValidationError('O pagamento original não tem data. Corrija o cadastro antes de anular.')
    if not confirmed or not 10<=len(reason.strip())<=1000:raise ValidationError('Confirme a anulação e descreva o motivo, entre 10 e 1.000 caracteres.')
    FinancialEntry.objects.create(event=event,raffle_id=expense.raffle_id,kind=FinancialEntryKind.DESPESA,amount_cents_signed=expense.amount_cents,
        recognized_at=timezone.make_aware(datetime.combine(expense.paid_at,time(hour=12))),posted_by=actor,
        source_reference=f'EXPENSE-{expense.pk}-VOID',description=f'Anulação de despesa: {expense.description}'[:255])
    expense.status=ExpenseStatus.CANCELED;expense.save(update_fields=['status'])
    log_audit(actor=actor,event_id=event.pk,action='PAID_EXPENSE_VOIDED',entity_type='Expense',entity_id=expense.pk,reason=reason,request=request)
    return expense,True


@transaction.atomic
def cancel_pending_expense(*, event, expense_id, actor, reason, request=None):
    reason = (reason or "").strip()
    if not reason or len(reason) > 500:
        raise ValidationError("Informe o motivo do cancelamento, com até 500 caracteres.")
    expense = Expense.objects.select_for_update().get(pk=expense_id, event=event)
    if expense.status == ExpenseStatus.CANCELED:
        return expense, False
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError("Uma despesa paga precisa de ajuste financeiro; seu histórico não pode ser apagado.")
    expense.status = ExpenseStatus.CANCELED
    expense.save(update_fields=["status"])
    log_audit(actor=actor, event_id=event.pk, action="EXPENSE_CANCELED", entity_type="Expense", entity_id=expense.pk,
              reason=reason, request=request)
    return expense, True
