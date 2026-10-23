"""Authoritative, atomic reconciliation shared by checkout, webhooks and recovery."""
import base64
import hashlib
import io
import re
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import qrcode
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables

from apps.catalog.models import CapacityHold, CapacityResource, HoldStatus, Lot
from apps.checkout.models import Order, OrderStatus, ReceivableDue
from apps.events.models import LegalDocumentVersion
from apps.finance.models import FinancialEntry, FinancialEntryKind
from apps.registrations.models import (
    Registration, CommercialState, OperationalState, RegistrationCredential,
    Entitlement, EntitlementState,
)
from .gateway import MercadoPagoGateway, GatewayError
from .models import ChargeAttempt, ChargeState, PaymentAllocation, PaymentMethod, PaymentTransaction, Refund, RefundState


class PaymentValidationError(Exception):
    """An authenticated provider resource does not match the local obligation."""


def money_cents(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise PaymentValidationError("invalid_amount") from exc


def provider_datetime(value):
    try:
        parsed = parse_datetime(str(value))
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        raise PaymentValidationError("invalid_provider_date")
    return parsed


def _validate(payment, charge, gateway):
    order = charge.order
    payment_id = str(payment.get("id", ""))
    metadata = payment.get("metadata") or {}
    if not re.fullmatch(r"[0-9]{1,30}", payment_id) or not isinstance(metadata, dict):
        raise PaymentValidationError("invalid_resource")
    if payment.get("external_reference") != order.merchant_reference:
        raise PaymentValidationError("reference_mismatch")
    if str(metadata.get("order_id")) != str(order.id) or str(metadata.get("charge_attempt_id")) != str(charge.id):
        raise PaymentValidationError("origin_mismatch")
    if str(payment.get("collector_id", "")) != gateway.collector_id:
        raise PaymentValidationError("collector_mismatch")
    if payment.get("live_mode") is not (gateway.environment == "production"):
        raise PaymentValidationError("environment_mismatch")
    if payment.get("currency_id") != order.currency or order.currency != "BRL":
        raise PaymentValidationError("currency_mismatch")
    amount = money_cents(payment.get("transaction_amount"))
    if amount != charge.amount_cents or amount != order.total_cents or not amount:
        raise PaymentValidationError("amount_mismatch")
    if charge.provider_payment_ref and charge.provider_payment_ref != payment_id:
        raise PaymentValidationError("payment_id_mismatch")
    if charge.payment_method == PaymentMethod.PIX and payment.get("payment_method_id") != "pix":
        raise PaymentValidationError("method_mismatch")
    if charge.payment_method == PaymentMethod.CREDIT_CARD and payment.get("payment_type_id") != "credit_card":
        raise PaymentValidationError("method_mismatch")
    return payment_id, amount, provider_datetime(payment.get("date_last_updated"))


def _ledger(order, payment_id, kind, amount, suffix, description):
    if amount:
        FinancialEntry.objects.create(event=order.event, order=order, kind=kind,
            amount_cents_signed=amount, source_reference=f"MP-{payment_id}-{suffix}"[:150], description=description)


def _holds_and_resources(order):
    holds = list(CapacityHold.objects.select_for_update().filter(order_id=order.id).order_by("resource_id", "pk"))
    resources = {item.pk: item for item in CapacityResource.objects.select_for_update().filter(
        pk__in={hold.resource_id for hold in holds}).order_by("pk")}
    return holds, resources


def _commit_capacity(order, approved_at):
    if order.event.status in {"CANCELED", "ARCHIVED", "FINISHED"}:
        return False
    holds, resources = _holds_and_resources(order)
    if not holds or any(hold.status != HoldStatus.ACTIVE or approved_at > hold.expires_at for hold in holds):
        return False
    registration_lines = set(order.lines.filter(registration__isnull=False).values_list("pk", flat=True))
    overall_holds = [hold for hold in holds if resources[hold.resource_id].type == "EVENT_OVERALL"]
    if {hold.order_line_id for hold in overall_holds} != registration_lines or sum(hold.quantity for hold in overall_holds) != len(registration_lines):
        return False
    lot_quantities = defaultdict(int)
    for line in order.lines.all():
        if line.lot_id:
            lot_quantities[line.lot_id] += line.quantity
    lots = {lot.pk: lot for lot in Lot.objects.select_for_update().filter(pk__in=lot_quantities).order_by("pk")}
    if any(lots[lot_id].current_sold + quantity > lots[lot_id].max_quantity for lot_id, quantity in lot_quantities.items()):
        return False
    required = defaultdict(int)
    for hold in holds:
        required[hold.resource_id] += hold.quantity
    for resource_id, quantity in required.items():
        resource = resources[resource_id]
        if resource.reserved_count < quantity or resource.reserved_count + resource.committed_count > resource.capacity_total:
            return False
    for resource_id, quantity in required.items():
        resource = resources[resource_id]
        resource.reserved_count -= quantity
        resource.committed_count += quantity
        resource.save(update_fields=["reserved_count", "committed_count"])
    CapacityHold.objects.filter(pk__in=[hold.pk for hold in holds]).update(status=HoldStatus.COMMITTED)
    for lot_id, quantity in lot_quantities.items():
        lot = lots[lot_id]
        lot.current_sold += quantity
        lot.save(update_fields=["current_sold"])
    return True


def _release_capacity(order, *, include_committed=False, expired=False):
    holds, resources = _holds_and_resources(order)
    released_committed = any(hold.status == HoldStatus.COMMITTED for hold in holds) and include_committed
    for hold in holds:
        resource = resources[hold.resource_id]
        if hold.status == HoldStatus.ACTIVE:
            resource.reserved_count = max(0, resource.reserved_count - hold.quantity)
        elif include_committed and hold.status == HoldStatus.COMMITTED:
            resource.committed_count = max(0, resource.committed_count - hold.quantity)
        else:
            continue
        resource.save(update_fields=["reserved_count", "committed_count"])
        hold.status = HoldStatus.EXPIRED if expired else HoldStatus.RELEASED
        hold.save(update_fields=["status"])
    if released_committed:
        lot_quantities = defaultdict(int)
        for line in order.lines.all():
            if line.lot_id:
                lot_quantities[line.lot_id] += line.quantity
        for lot in Lot.objects.select_for_update().filter(pk__in=lot_quantities).order_by("pk"):
            lot.current_sold = max(0, lot.current_sold-lot_quantities[lot.pk])
            lot.save(update_fields=["current_sold"])


@transaction.atomic
def expire_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.is_expired():
        _release_capacity(order, expired=True)
        order.status = OrderStatus.EXPIRED
        order.save(update_fields=["status", "updated_at"])
    return order


def expire_stale_orders(*, event_id=None, limit=100):
    """Release abandoned reservations, including orders that never created a charge."""
    orders = Order.objects.filter(status__in=[OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT], expires_at__lt=timezone.now())
    if event_id:
        orders = orders.filter(event_id=event_id)
    count = 0
    for order_id in list(orders.order_by("expires_at").values_list("pk", flat=True)[:limit]):
        if expire_order(order_id).status == OrderStatus.EXPIRED:
            count += 1
    return count


def required_documents_for_registration(registration):
    """Latest published and effective version of each document kind for this event."""
    latest = {}
    now = timezone.now()
    for document in LegalDocumentVersion.objects.filter(event_id=registration.event_id,
            effective_at__lte=now, published_at__lte=now).order_by("kind", "-version_number"):
        latest.setdefault(document.kind, document)
    return list(latest.values())


@transaction.atomic
def activate_registration_if_eligible(registration):
    """Issue an admission credential after real personal acceptance; never accept terms here.

    Only Registration is locked, preserving Order -> Registration lock ordering when
    this helper is called by reconciliation or a participant's acceptance endpoint.
    """
    reg = Registration.objects.select_for_update(of=("self",)).select_related("source_order_line__order").get(
        pk=getattr(registration, "pk", registration))
    if reg.commercial_state != CommercialState.ACTIVE or reg.source_order_line.order.status != OrderStatus.PAID:
        return False
    required_documents = {document.pk for document in required_documents_for_registration(reg)}
    accepted = set(reg.terms_acceptances.filter(accepting_user__person_links__person_id=reg.person_id).values_list("document_version_id", flat=True))
    if not required_documents or not required_documents <= accepted:
        reg.operational_state = OperationalState.NOT_READY
        reg.save(update_fields=["operational_state", "updated_at"])
        return False
    credential = RegistrationCredential.objects.filter(registration=reg).first()
    if credential is not None and not credential.is_valid:
        # Personal acceptance cannot undo a staff revocation or renew an expired credential.
        reg.operational_state = OperationalState.NOT_READY
        reg.save(update_fields=["operational_state", "updated_at"])
        return False
    if reg.operational_state != OperationalState.CHECKED_IN:
        reg.operational_state = OperationalState.READY
        reg.save(update_fields=["operational_state", "updated_at"])
    if credential is None:
        token = uuid.uuid4().hex
        buffer = io.BytesIO()
        qrcode.make(token).save(buffer, format="PNG")
        values = {"raw_token": token, "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                  "qr_code_image": base64.b64encode(buffer.getvalue()).decode("ascii"), "revoked_at": None,
                  "issued_at": timezone.now(), "version": 1}
        RegistrationCredential.objects.create(registration=reg, **values)
    return True


def _activate_registrations(order):
    # Payment never implies legal acceptance. The participant completes that separately.
    for reg in Registration.objects.select_for_update(of=("self",)).filter(source_order_line__order=order).select_related("source_order_line__product"):
        reg.commercial_state = CommercialState.ACTIVE
        reg.operational_state = OperationalState.NOT_READY
        reg.save(update_fields=["commercial_state", "operational_state", "updated_at"])
        product = reg.source_order_line.product
        benefits_snapshot = reg.source_order_line.item_snapshot_json
        benefits = [("ACESSO_PISTA", "Acesso ao evento")]
        if benefits_snapshot.get("includes_kit", product.includes_kit if product else False):
            benefits.append(("KIT", "Kit do participante"))
        if benefits_snapshot.get("includes_shirt", product.includes_shirt if product else False):
            benefits.append(("CAMISETA", f"Camiseta {reg.shirt_size}"))
        if benefits_snapshot.get("includes_meal", product.includes_meal if product else False):
            benefits.append(("REFEICAO", "Alimentação"))
        for kind, title in benefits:
            Entitlement.objects.get_or_create(registration=reg, benefit_type=kind,
                defaults={"title": title, "quantity_total": 1, "state": EntitlementState.AVAILABLE})
        activate_registration_if_eligible(reg)


def _suspend_registrations(order, *, canceled=False):
    registrations = Registration.objects.filter(source_order_line__order=order)
    updates = {"operational_state": OperationalState.NOT_READY}
    if canceled:
        updates["commercial_state"] = CommercialState.CANCELED
    registrations.update(**updates)
    RegistrationCredential.objects.filter(registration__in=registrations, revoked_at__isnull=True).update(revoked_at=timezone.now())
    Entitlement.objects.filter(registration__in=registrations).update(state=EntitlementState.REVOKED)


@transaction.atomic
def reconcile_payment(payment, *, charge_id=None):
    """Only call with a response obtained through an authenticated MP API request."""
    gateway = MercadoPagoGateway()
    gateway.require_configuration()
    metadata = payment.get("metadata") or {}
    try:
        attempt_id = uuid.UUID(str(charge_id or metadata.get("charge_attempt_id", "")))
        initial_charge = ChargeAttempt.objects.get(pk=attempt_id, provider="mercadopago")
    except (ValueError, TypeError, AttributeError, ChargeAttempt.DoesNotExist) as exc:
        raise PaymentValidationError("unknown_charge") from exc
    # All payments for one order serialize before touching transaction snapshots.
    order = Order.objects.select_for_update().select_related("event").get(pk=initial_charge.order_id)
    charge = ChargeAttempt.objects.select_for_update().get(pk=attempt_id)
    charge.order = order
    payment_id, amount, updated_at = _validate(payment, charge, gateway)
    status = payment.get("status", "")
    if status not in {"approved", "pending", "in_process", "authorized", "rejected", "cancelled", "refunded", "charged_back", "in_mediation"}:
        raise PaymentValidationError("unknown_status")
    snapshot, created = PaymentTransaction.objects.select_for_update().get_or_create(
        provider_payment_id=payment_id, defaults={"charge_attempt": charge, "provider_status": status,
        "provider_updated_at": updated_at, "amount_cents": amount})
    if snapshot.charge_attempt_id != charge.pk:
        raise PaymentValidationError("payment_already_assigned")
    if not created and updated_at < snapshot.provider_updated_at:
        return order
    # Terminal reversals and settled funds never regress to a pending/approved delivery.
    if snapshot.posted_refund_cents and status not in {"refunded", "charged_back", "approved"}:
        return order
    if snapshot.posted_revenue_cents and status in {"pending", "in_process", "authorized", "rejected", "cancelled"}:
        return order
    refund = money_cents(payment.get("transaction_amount_refunded", 0))
    if status in {"refunded", "charged_back"}:
        refund = amount
    if refund > amount or refund < snapshot.posted_refund_cents:
        raise PaymentValidationError("invalid_refund")
    settled = status in {"approved", "refunded", "charged_back"}
    if settled and not snapshot.posted_revenue_cents:
        _ledger(order, payment_id, FinancialEntryKind.RECEITA_INSCRICAO, amount, "REVENUE", "Pagamento confirmado pelo Mercado Pago")
        snapshot.posted_revenue_cents = amount
    fees = payment.get("fee_details")
    if settled and isinstance(fees, list):
        fee = sum(money_cents(item.get("amount", 0)) for item in fees if isinstance(item, dict) and item.get("fee_payer", "collector") == "collector")
        _ledger(order, payment_id, FinancialEntryKind.TAXA_MERCADO_PAGO, snapshot.posted_fee_cents - fee,
                f"FEE-{fee}-{updated_at.isoformat()}", "Tarifa real informada pelo Mercado Pago")
        snapshot.posted_fee_cents = fee
    if refund > snapshot.posted_refund_cents:
        refund_delta = refund-snapshot.posted_refund_cents
        _ledger(order, payment_id, FinancialEntryKind.REEMBOLSO, -(refund - snapshot.posted_refund_cents),
                f"REFUND-{refund}", "Devolução ou contestação confirmada pelo Mercado Pago")
        snapshot.posted_refund_cents = refund
        Refund.objects.get_or_create(request_key=f"MP-{payment_id}-REFUND-{refund}", defaults={
            "order": order, "charge_attempt": charge, "amount_cents": refund_delta,
            "state": RefundState.SUCCEEDED, "provider_ref": payment_id,
            "reason_code": "CHARGEBACK" if status == "charged_back" else "PROVIDER_REFUND",
            "reason_notes": "Atualização autenticada do Mercado Pago; conferência por inscrição necessária para devolução parcial.",
            "completed_at": timezone.now()})

    charge.provider_payment_ref = payment_id
    charge.provider_order_id = str((payment.get("order") or {}).get("id") or payment_id)
    charge.error_code = ""
    charge.error_message = ""
    if status == "approved" and refund == 0:
        charge.state = ChargeState.SUCCEEDED
        if not snapshot.fulfilled_at:
            already_fulfilled = PaymentTransaction.objects.filter(charge_attempt__order=order, fulfilled_at__isnull=False).exclude(pk=snapshot.pk).exists()
            lines = list(order.lines.all())
            correct_lines = sum(line.net_cents for line in lines) == amount
            approved_at = provider_datetime(payment.get("date_approved"))
            if already_fulfilled or order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT} or not correct_lines or not _commit_capacity(order, approved_at):
                order.status = OrderStatus.PAID_NEEDS_REVIEW
                charge.error_code = "FULFILLMENT_REVIEW"
                charge.error_message = "Pagamento recebido. A organização precisa conferir sua reserva antes de liberar a inscrição."
            else:
                order.status = OrderStatus.PAID
                order.save(update_fields=["status", "updated_at"])
                snapshot.fulfilled_at = timezone.now()
                for line in lines:
                    PaymentAllocation.objects.get_or_create(charge_attempt=charge, order_line=line, defaults={"amount_cents": line.net_cents})
                ReceivableDue.objects.filter(order=order, due_type="FULL").update(allocated_paid_cents=amount, status="PAID")
                _activate_registrations(order)
                from apps.identity.onboarding import provision_buyer_account
                provision_buyer_account(order)
    elif refund:
        charge.state = ChargeState.CANCELED
        full = refund == amount
        other_fulfilled = PaymentTransaction.objects.filter(charge_attempt__order=order, fulfilled_at__isnull=False).exclude(pk=snapshot.pk).exists()
        if not snapshot.fulfilled_at and other_fulfilled:
            # Refunding an accidental duplicate must not revoke the original paid ticket.
            order.status = OrderStatus.PAID_NEEDS_REVIEW
        else:
            order.status = OrderStatus.REFUNDED if full else OrderStatus.PARTIALLY_REFUNDED
            _suspend_registrations(order, canceled=full)
            if full:
                _release_capacity(order, include_committed=True)
            ReceivableDue.objects.filter(order=order, due_type="FULL").update(allocated_paid_cents=amount-refund, status="CANCELED" if full else "PARTIAL")
    elif status == "in_mediation":
        charge.state = ChargeState.UNKNOWN
        order.status = OrderStatus.PAID_NEEDS_REVIEW
        _suspend_registrations(order)
    elif status in {"rejected", "cancelled"}:
        charge.state = ChargeState.FAILED if status == "rejected" else ChargeState.CANCELED
        charge.error_message = "O pagamento não foi aprovado. Você pode tentar novamente enquanto a reserva estiver válida."
    else:
        charge.state = ChargeState.PENDING
        if order.status == OrderStatus.RESERVED:
            order.status = OrderStatus.AWAITING_PAYMENT
    if charge.payment_method == PaymentMethod.PIX:
        qr = (payment.get("point_of_interaction") or {}).get("transaction_data") or {}
        charge.pix_copy_paste = str(qr.get("qr_code") or "")
        charge.pix_qr_code = str(qr.get("qr_code_base64") or "")
    else:
        charge.card_last_four = str((payment.get("card") or {}).get("last_four_digits") or "")[:4]
        charge.card_brand = str(payment.get("payment_method_id") or "")[:30]
        charge.card_installments = int(payment.get("installments") or 1)
    if charge.state in {ChargeState.SUCCEEDED, ChargeState.FAILED, ChargeState.CANCELED}:
        charge.resolved_at = timezone.now()
    charge.save()
    order.save(update_fields=["status", "updated_at"])
    snapshot.provider_status = status
    snapshot.provider_updated_at = updated_at
    snapshot.save()
    return order


@sensitive_variables()
def submit_charge(charge, **card_data):
    gateway = MercadoPagoGateway()
    try:
        payment = gateway.create_pix_payment(charge) if charge.payment_method == PaymentMethod.PIX else gateway.create_card_payment(charge, **card_data)
        return reconcile_payment(payment, charge_id=charge.id)
    except GatewayError as exc:
        # A timeout does not mean rejection. Keep this attempt blocked for recovery.
        ChargeAttempt.objects.filter(pk=charge.id, state=ChargeState.SENT).update(
            state=ChargeState.UNKNOWN if exc.uncertain else ChargeState.FAILED,
            error_code=exc.code, error_message=str(exc))
        raise
    except PaymentValidationError:
        ChargeAttempt.objects.filter(pk=charge.id, state=ChargeState.SENT).update(
            state=ChargeState.UNKNOWN, error_code="RECONCILIATION_REQUIRED",
            error_message="A organização está conferindo a confirmação do pagamento. Não pague novamente.")
        raise


def refresh_charge(charge):
    gateway = MercadoPagoGateway()
    if charge.provider_payment_ref:
        return reconcile_payment(gateway.get_payment(charge.provider_payment_ref), charge_id=charge.id)
    results = gateway.find_payments(charge)
    for result in results:
        # Always obtain the complete, latest authenticated resource after search.
        reconcile_payment(gateway.get_payment(result["id"]), charge_id=charge.id)
    return Order.objects.get(pk=charge.order_id)
