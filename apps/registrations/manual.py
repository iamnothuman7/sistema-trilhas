"""Internal registration: real manual receipts, atomic capacity, personal consent later."""
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.checkout.models import Order, OrderStatus, ReceivableDue
from apps.checkout.reservations import reserve_order
from apps.core.permissions import can_manage_event
from apps.core.utils import log_audit
from apps.events.models import Event
from apps.finance.models import FinancialEntry, FinancialEntryKind
from apps.identity.onboarding import provision_buyer_account
from apps.payments.models import ManualPaymentEvidence
from apps.payments.services import _commit_capacity, _activate_registrations
from .forms_manual import PAYMENT_METHODS
from .models import Registration, CommercialState, ManualRegistration


@transaction.atomic
def confirm_manual_receipt(*, entry, actor, data, request=None):
    order = Order.objects.select_for_update().select_related("event").get(pk=entry.registration.source_order_line.order_id)
    if not can_manage_event(actor, order.event, "finance"):
        raise PermissionDenied
    if data.get("method") not in dict(PAYMENT_METHODS) or not data.get("received_confirmed") or not data.get("payment_notes"):
        raise ValidationError("Confirme a forma e a identificação do recebimento.")
    if data.get("amount") is None or data["amount"] * 100 != order.total_cents or order.total_cents <= 0:
        raise ValidationError(f"O recebimento deve corresponder ao valor integral do pedido: R$ {order.formatted_total}.")
    if order.status == OrderStatus.PAID and order.manual_evidences.exists():
        return order  # A repeated confirmation never posts revenue twice.
    if order.charge_attempts.exists():
        raise ValidationError("Este pedido já iniciou uma cobrança no Mercado Pago. Acompanhe essa cobrança para evitar recebimento em duplicidade.")
    if order.status != OrderStatus.RESERVED or order.is_expired():
        raise ValidationError("Esta reserva expirou ou não pode receber confirmação manual. Confira a situação e cadastre uma nova reserva, se necessário.")
    if Registration.objects.filter(source_order_line__order=order).exclude(commercial_state=CommercialState.RESERVED).exists():
        raise ValidationError("A inscrição foi alterada. Confira sua situação antes de confirmar o pagamento.")
    if not _commit_capacity(order, timezone.now()):
        raise ValidationError("Não foi possível confirmar a vaga. Nenhum recebimento foi registrado.")
    order.status = OrderStatus.PAID
    order.save(update_fields=["status", "updated_at"])
    evidence = ManualPaymentEvidence.objects.create(order=order, method=data["method"], amount_cents=order.total_cents,
        verified_by=actor, notes=data["payment_notes"])
    FinancialEntry.objects.create(event=order.event, order=order, kind=FinancialEntryKind.RECEITA_MANUAL,
        amount_cents_signed=order.total_cents, source_reference=f"MANUAL-{evidence.pk}", posted_by=actor,
        description=f"Inscrição manual — {dict(PAYMENT_METHODS)[data['method']]}")
    ReceivableDue.objects.filter(order=order, due_type="FULL").update(allocated_paid_cents=order.total_cents, status="PAID")
    _activate_registrations(order)
    provision_buyer_account(order)
    log_audit(actor=actor, event_id=order.event_id, action="MANUAL_PAYMENT_CONFIRMED", entity_type="Order", entity_id=order.pk,
        diff={"method": data["method"], "amount_cents": order.total_cents}, reason=data["payment_notes"], request=request)
    return order


@transaction.atomic
def create_manual_registration(*, event, actor, data, request_key, request_hash, request=None):
    if not can_manage_event(actor, event, "registrations"):
        raise PermissionDenied
    event = Event.objects.select_for_update().get(pk=event.pk)
    existing = ManualRegistration.objects.filter(request_key=request_key).select_related("registration__source_order_line").first()
    if existing:
        if existing.created_by_id != actor.pk or existing.registration.event_id != event.pk or existing.request_hash != request_hash:
            raise ValidationError("Este formulário já foi utilizado com outros dados. Abra uma nova inscrição.")
        return existing
    lot = data["lot"]
    participant = {key: data[key] for key in ("name", "cpf", "email", "phone", "city", "state", "team", "shirt")}
    participant.update(product_id=str(lot.product_id), lot_id=str(lot.pk))
    order = reserve_order(event=event, participants=[participant], buyer={"name": data["name"], "email": data["email"],
        "phone": data["phone"], "document": data["cpf"]}, manual=True)
    entry = ManualRegistration.objects.create(registration=Registration.objects.get(source_order_line__order=order),
        created_by=actor, request_key=request_key, request_hash=request_hash, notes=data["notes"],
        access_expires_at=timezone.now()+timedelta(days=7))
    if data["payment_state"] == "received":
        confirm_manual_receipt(entry=entry, actor=actor, data=data, request=request)
    log_audit(actor=actor, event_id=event.pk, action="MANUAL_REGISTRATION_CREATED", entity_type="Registration", entity_id=entry.registration_id,
        diff={"order_id": str(order.pk), "payment_state": data["payment_state"], "price_cents": order.total_cents}, reason=data["notes"], request=request)
    return entry
