import hashlib
import json
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.utils import log_audit
from .models import Expense, ExpenseStatus, FinancialEntry, FinancialEntryKind


def _post_expense(expense, actor):
    # A stable source links the expense to its ledger entry; row locks protect repeats.
    FinancialEntry.objects.get_or_create(
        event=expense.event,
        kind=FinancialEntryKind.DESPESA,
        source_reference=f"EXPENSE-{expense.pk}",
        defaults={
            "amount_cents_signed": -expense.amount_cents,
            "description": f"Despesa: {expense.description}",
            "posted_by": actor,
            "recognized_at": timezone.make_aware(datetime.combine(expense.paid_at, time(hour=12))),
        },
    )


def create_expense(*, event, data, actor, request=None):
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
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    try:
        with transaction.atomic():
            previous = Expense.objects.select_for_update().filter(idempotency_key=data["idempotency_key"]).first()
            if previous:
                if previous.event_id != event.pk or previous.submission_hash != digest:
                    raise ValidationError("Esta identificação já foi usada com outros dados. Reabra o formulário.")
                return previous, False
            expense = Expense.objects.create(
                event=event, vendor=vendor, category=data["category"],
                description=data["description"], amount_cents=data["amount"],
                due_at=data["due_at"], paid_at=data["paid_at"], status=data["status"],
                idempotency_key=data["idempotency_key"], submission_hash=digest,
            )
            if expense.status == ExpenseStatus.PAID:
                _post_expense(expense, actor)
            log_audit(actor=actor, event_id=event.pk, action="EXPENSE_CREATED", entity_type="Expense", entity_id=expense.pk,
                      diff={"amount_cents": expense.amount_cents, "status": expense.status}, request=request)
            return expense, True
    except IntegrityError:
        # A concurrent request may have committed the same unique submission key.
        previous = Expense.objects.filter(idempotency_key=data["idempotency_key"]).first()
        if previous and previous.event_id == event.pk and previous.submission_hash == digest:
            return previous, False
        if previous:
            raise ValidationError("Esta identificação já foi usada com outros dados. Reabra o formulário.")
        raise


@transaction.atomic
def settle_expense(*, event, expense_id, actor, paid_at, request=None):
    expense = Expense.objects.select_for_update().get(pk=expense_id, event=event)
    if paid_at > timezone.localdate():
        raise ValidationError("Um pagamento realizado não pode ter data futura.")
    if expense.status == ExpenseStatus.PAID:
        return expense, False
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError("Somente uma despesa a pagar pode ser quitada.")
    expense.status = ExpenseStatus.PAID
    expense.paid_at = paid_at
    expense.save(update_fields=["status", "paid_at"])
    _post_expense(expense, actor)
    log_audit(actor=actor, event_id=event.pk, action="EXPENSE_PAID", entity_type="Expense", entity_id=expense.pk,
              diff={"amount_cents": expense.amount_cents, "paid_at": paid_at.isoformat()}, request=request)
    return expense, True


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
