"""Manual cash records with atomic corrections, reversals and restoration."""
import hashlib
import json
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.utils import log_audit
from apps.events.models import Event
from apps.reporting.selectors import event_timezone
from .models import ManualMovement, FinancialEntry, FinancialEntryKind, SponsorPaymentMethod
from .receipts import receipt_digest, save_receipt, with_receipt_cleanup


FIELDS = ('kind', 'description', 'amount_cents', 'occurred_on', 'method', 'reference', 'notes')


def snapshot(row):
    return {field: str(getattr(row, field)) for field in FIELDS}


def validate(data):
    if data['kind'] not in ManualMovement.Kind.values or data['method'] not in SponsorPaymentMethod.values:
        raise ValidationError('Selecione um tipo e uma forma válidos.')
    if not isinstance(data['amount_cents'], int) or not 0 < data['amount_cents'] <= 99999999999:
        raise ValidationError('Informe um valor entre R$ 0,01 e R$ 999.999.999,99.')
    if data['occurred_on'] > timezone.localdate():
        raise ValidationError('Registre somente movimentações já realizadas.')
    if data['kind'] == ManualMovement.Kind.TRANSFER and not data['reference'].strip():
        raise ValidationError('Informe as contas de origem e destino da transferência.')


def post(row, actor, *, reversal=False):
    kind = FinancialEntryKind.RECEITA_MANUAL if row.kind == ManualMovement.Kind.INCOME else FinancialEntryKind.TRANSFERENCIA_BANCO
    amount = row.amount_cents if row.kind == ManualMovement.Kind.INCOME else -row.amount_cents
    FinancialEntry.objects.create(event=row.event, movement=row, kind=kind,
        amount_cents_signed=-amount if reversal else amount,
        recognized_at=timezone.make_aware(datetime.combine(row.occurred_on, time(hour=12)), event_timezone(row.event)),
        source_reference=f'MANUAL-{row.pk}-{row.revision}-' + ('REVERSAL' if reversal else 'POST'),
        posted_by=actor, description=(('Correção / exclusão: ' if reversal else '') + row.description)[:255])


@with_receipt_cleanup
@transaction.atomic
def save_movement(*, event, data, actor, movement_id=None, request=None, _saved_receipts):
    Event.objects.select_for_update().get(pk=event.pk)
    validate(data)
    proof = data.get('proof_asset')
    if movement_id:
        row = ManualMovement.objects.select_for_update().get(pk=movement_id, event=event)
        if row.archived_at:
            raise ValidationError('Restaure a movimentação antes de editar.')
        if row.revision != data['revision']:
            raise ValidationError('Esta movimentação mudou em outra aba. Atualize antes de salvar.')
        if len(data.get('reason', '').strip()) < 5:
            raise ValidationError('Informe o motivo da correção.')
        before = snapshot(row)
        monetary_change = any(getattr(row, field) != data[field] for field in ('kind', 'amount_cents', 'occurred_on'))
        if monetary_change:
            post(row, actor, reversal=True)
        for field in FIELDS:
            setattr(row, field, data[field])
        row.revision += 1
        row.save()
        if monetary_change:
            post(row, actor)
        action = 'MANUAL_MOVEMENT_EDITED'
    else:
        payload = {field: str(data[field]) for field in FIELDS}
        payload.update(event=str(event.pk), receipt=receipt_digest(proof))
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        previous = ManualMovement.objects.filter(request_key=data['request_key']).first()
        if previous:
            if previous.event_id != event.pk or previous.submission_hash != digest:
                raise ValidationError('Este formulário já foi usado com outros dados. Reabra a página.')
            return previous, False
        row = ManualMovement.objects.create(event=event, created_by=actor, request_key=data['request_key'],
            submission_hash=digest, **{field: data[field] for field in FIELDS})
        before = {}
        post(row, actor)
        action = 'MANUAL_MOVEMENT_CREATED'
    save_receipt(row, proof, _saved_receipts)
    log_audit(actor=actor, event_id=event.pk, action=action, entity_type='ManualMovement', entity_id=row.pk,
        diff={'before': before, 'after': snapshot(row)}, reason=data.get('reason', ''), request=request)
    return row, True


@transaction.atomic
def archive_movement(*, event, movement_id, revision, actor, reason, restore=False, request=None):
    Event.objects.select_for_update().get(pk=event.pk)
    row = ManualMovement.objects.select_for_update().get(event=event, pk=movement_id)
    if bool(row.archived_at) != bool(restore):
        return row, False
    if row.revision != revision:
        raise ValidationError('Esta movimentação mudou. Atualize a página antes de continuar.')
    if not 5 <= len(reason.strip()) <= 1000:
        raise ValidationError('Informe o motivo, entre 5 e 1.000 caracteres.')
    row.revision += 1
    post(row, actor, reversal=not restore)
    row.archived_at = None if restore else timezone.now()
    row.save(update_fields=['archived_at', 'revision', 'updated_at'])
    log_audit(actor=actor, event_id=event.pk, action='MANUAL_MOVEMENT_RESTORED' if restore else 'MANUAL_MOVEMENT_REMOVED',
        entity_type='ManualMovement', entity_id=row.pk, reason=reason,
        diff={'amount_cents': row.amount_cents, 'kind': row.kind, 'revision': row.revision}, request=request)
    return row, True
