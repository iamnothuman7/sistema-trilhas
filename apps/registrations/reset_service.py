"""
Service to safely reset test registrations, test orders, and operational test data.
Preserves events, lot definitions, categories, sponsor settings, inventory configurations,
and administrator accounts.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.utils import log_audit
from apps.registrations.models import (
    Registration, ManualRegistration, TermsAcceptance,
    RegistrationCredential, RegistrationTransfer, Entitlement, Consumption, WaitlistEntry
)
from apps.checkout.models import Order, OrderLine, OrderCoupon, ReceivableDue, CheckoutSubmission
from apps.payments.models import ChargeAttempt, Refund, ManualPaymentEvidence, PaymentTransaction, PaymentAllocation
from apps.catalog.models import CapacityHold, CapacityResource
from apps.operations.models import CheckinRecord, FieldRecord, OccurrenceRecord
from apps.events.models_raffles import RaffleTicket
from apps.finance.models import FinancialEntry
from apps.identity.models import ClaimToken
from apps.mediaframes.models import GeneratedMontage
from apps.expeditions.models import RoomAssignment, TransportAssignment


def get_reset_preview(event=None):
    """Returns the count of objects that would be affected by a reset."""
    reg_qs = Registration.objects.all()
    if event:
        reg_qs = reg_qs.filter(event=event)
    reg_ids = list(reg_qs.values_list("id", flat=True))
    source_line_ids = list(reg_qs.values_list("source_order_line_id", flat=True))

    order_ids = list(OrderLine.objects.filter(id__in=source_line_ids).values_list("order_id", flat=True).distinct())
    if event:
        event_order_ids = list(Order.objects.filter(event=event).values_list("id", flat=True))
        order_ids = list(set(order_ids + event_order_ids))

    hold_qs = CapacityHold.objects.all()
    if event:
        hold_qs = hold_qs.filter(resource__event=event)

    resource_qs = CapacityResource.objects.all()
    if event:
        resource_qs = resource_qs.filter(event=event)

    raffle_qs = RaffleTicket.objects.filter(
        registration_id__in=reg_ids
    ) | RaffleTicket.objects.filter(
        beneficiary_registration_id__in=reg_ids
    ) | RaffleTicket.objects.filter(
        order_line__order_id__in=order_ids
    ) | RaffleTicket.objects.filter(
        source__in=["REGISTRATION", "ONLINE"]
    )
    if event:
        raffle_qs = raffle_qs.filter(raffle__event=event)
    raffle_qs = raffle_qs.distinct()

    return {
        "registrations": len(reg_ids),
        "orders": len(order_ids),
        "checkins": CheckinRecord.objects.filter(registration_id__in=reg_ids).count(),
        "credentials": RegistrationCredential.objects.filter(registration_id__in=reg_ids).count(),
        "terms_acceptances": TermsAcceptance.objects.filter(registration_id__in=reg_ids).count(),
        "entitlements": Entitlement.objects.filter(registration_id__in=reg_ids).count(),
        "holds": hold_qs.count(),
        "raffle_tickets": raffle_qs.count(),
        "charge_attempts": ChargeAttempt.objects.filter(order_id__in=order_ids).count(),
        "resources_to_reset": resource_qs.count(),
    }


REAL_MONEY_STATUSES = ("PAID", "PARTIALLY_REFUNDED", "PAID_NEEDS_REVIEW", "REFUNDED")


def real_money_orders(event):
    """Pedidos não marcados como teste que já movimentaram dinheiro. Com eles, o reset é recusado."""
    orders = Order.objects.filter(event=event, is_test=False)
    return orders.filter(status__in=REAL_MONEY_STATUSES) | orders.filter(pk__in=FinancialEntry.objects.filter(
        order__event=event).values("order_id")) | orders.filter(pk__in=PaymentTransaction.objects.filter(
        charge_attempt__order__event=event).values("charge_attempt__order_id")) | orders.filter(
        pk__in=ManualPaymentEvidence.objects.filter(order__event=event).values("order_id"))


@transaction.atomic
def reset_test_registrations(*, event=None, dry_run=False, performed_by=None):
    """
    Apaga inscrições e pedidos de teste de UM evento. Recusa se houver pedido real com dinheiro
    (pago, devolvido ou com lançamento/pagamento), para nunca apagar vendas de verdade.
    If dry_run=True, returns stats without persisting changes.
    """
    if event is None:
        raise ValidationError("Escolha um evento. O reset de todos os eventos de uma vez foi desativado.")
    real = list(real_money_orders(event).distinct().values_list("merchant_reference", flat=True)[:5])
    if real:
        raise ValidationError("Este evento já tem pedidos reais com pagamento (" + ", ".join(real) + "). "
            "Zerar testes apagaria vendas de verdade e foi bloqueado. Cancele ou devolva os testes individualmente.")
    reg_qs = Registration.objects.all()
    if event:
        reg_qs = reg_qs.filter(event=event)
    reg_ids = list(reg_qs.values_list("id", flat=True))
    source_line_ids = list(reg_qs.values_list("source_order_line_id", flat=True))

    order_ids = list(OrderLine.objects.filter(id__in=source_line_ids).values_list("order_id", flat=True).distinct())
    if event:
        event_order_ids = list(Order.objects.filter(event=event).values_list("id", flat=True))
        order_ids = list(set(order_ids + event_order_ids))

    hold_qs = CapacityHold.objects.all()
    if event:
        hold_qs = hold_qs.filter(resource__event=event)

    resource_qs = CapacityResource.objects.all()
    if event:
        resource_qs = resource_qs.filter(event=event)

    raffle_qs = RaffleTicket.objects.filter(
        registration_id__in=reg_ids
    ) | RaffleTicket.objects.filter(
        beneficiary_registration_id__in=reg_ids
    ) | RaffleTicket.objects.filter(
        order_line__order_id__in=order_ids
    )
    raffle_qs = raffle_qs.filter(raffle__event=event).distinct()

    stats = {
        "registrations_deleted": len(reg_ids),
        "orders_deleted": len(order_ids),
        "checkins_deleted": CheckinRecord.objects.filter(registration_id__in=reg_ids).count(),
        "field_records_deleted": FieldRecord.objects.filter(registration_id__in=reg_ids).count(),
        "occurrence_records_deleted": OccurrenceRecord.objects.filter(registration_id__in=reg_ids).count(),
        "credentials_deleted": RegistrationCredential.objects.filter(registration_id__in=reg_ids).count(),
        "terms_acceptances_deleted": TermsAcceptance.objects.filter(registration_id__in=reg_ids).count(),
        "transfers_deleted": RegistrationTransfer.objects.filter(
            from_registration_id__in=reg_ids
        ).count() + RegistrationTransfer.objects.filter(to_registration_id__in=reg_ids).count(),
        "manual_registrations_deleted": ManualRegistration.objects.filter(registration_id__in=reg_ids).count(),
        "entitlements_deleted": Entitlement.objects.filter(registration_id__in=reg_ids).count(),
        "consumptions_deleted": Consumption.objects.filter(entitlement__registration_id__in=reg_ids).count(),
        "waitlist_deleted": WaitlistEntry.objects.filter(event=event).count() if event else WaitlistEntry.objects.count(),
        "claim_tokens_deleted": ClaimToken.objects.filter(registration_id__in=reg_ids).count(),
        "room_assignments_deleted": RoomAssignment.objects.filter(registration_id__in=reg_ids).count(),
        "transport_assignments_deleted": TransportAssignment.objects.filter(registration_id__in=reg_ids).count(),
        "montages_unlinked": GeneratedMontage.objects.filter(registration_id__in=reg_ids).count(),
        "raffle_tickets_deleted": raffle_qs.count(),
        "financial_entries_deleted": FinancialEntry.objects.filter(order_id__in=order_ids).count(),
        "charge_attempts_deleted": ChargeAttempt.objects.filter(order_id__in=order_ids).count(),
        "refunds_deleted": Refund.objects.filter(order_id__in=order_ids).count(),
        "manual_payment_evidence_deleted": ManualPaymentEvidence.objects.filter(order_id__in=order_ids).count(),
        "checkout_submissions_deleted": CheckoutSubmission.objects.filter(order_id__in=order_ids).count(),
        "receivable_dues_deleted": ReceivableDue.objects.filter(order_id__in=order_ids).count(),
        "order_coupons_deleted": OrderCoupon.objects.filter(order_id__in=order_ids).count(),
        "order_lines_deleted": OrderLine.objects.filter(order_id__in=order_ids).count(),
        "holds_deleted": hold_qs.count(),
        "resources_reset": resource_qs.count(),
    }

    if dry_run:
        return stats

    # 1. Unlink montages
    GeneratedMontage.objects.filter(registration_id__in=reg_ids).update(registration=None)

    # 2. Operations & Field Records
    CheckinRecord.objects.filter(registration_id__in=reg_ids).delete()
    FieldRecord.objects.filter(registration_id__in=reg_ids).delete()
    OccurrenceRecord.objects.filter(registration_id__in=reg_ids).delete()

    # 3. Expeditions
    RoomAssignment.objects.filter(registration_id__in=reg_ids).delete()
    TransportAssignment.objects.filter(registration_id__in=reg_ids).delete()

    # 4. Identity & Claims
    ClaimToken.objects.filter(registration_id__in=reg_ids).delete()

    # 5. Benefits & Consumptions
    Consumption.objects.filter(entitlement__registration_id__in=reg_ids).delete()
    Entitlement.objects.filter(registration_id__in=reg_ids).delete()

    # 6. Registrations details
    RegistrationCredential.objects.filter(registration_id__in=reg_ids).delete()
    TermsAcceptance.objects.filter(registration_id__in=reg_ids).delete()
    RegistrationTransfer.objects.filter(from_registration_id__in=reg_ids).delete()
    RegistrationTransfer.objects.filter(to_registration_id__in=reg_ids).delete()
    ManualRegistration.objects.filter(registration_id__in=reg_ids).delete()

    # 7. Raffle tickets
    raffle_qs.delete()

    # 8. Financial entries associated with orders
    FinancialEntry.objects.filter(order_id__in=order_ids).delete()

    # 9. Payment records
    PaymentAllocation.objects.filter(charge_attempt__order_id__in=order_ids).delete()
    ManualPaymentEvidence.objects.filter(order_id__in=order_ids).delete()
    Refund.objects.filter(order_id__in=order_ids).delete()
    PaymentTransaction.objects.filter(charge_attempt__order_id__in=order_ids).delete()
    ChargeAttempt.objects.filter(order_id__in=order_ids).delete()

    # 10. Delete Registrations
    # First break self-referential parent_registration FK if any
    Registration.objects.filter(id__in=reg_ids).update(parent_registration=None)
    Registration.objects.filter(id__in=reg_ids).delete()

    # 11. Checkout lines & submissions
    CheckoutSubmission.objects.filter(order_id__in=order_ids).delete()
    ReceivableDue.objects.filter(order_id__in=order_ids).delete()
    OrderCoupon.objects.filter(order_id__in=order_ids).delete()
    OrderLine.objects.filter(order_id__in=order_ids).delete()

    # 12. Delete Orders
    Order.objects.filter(id__in=order_ids).delete()

    # 13. Waitlist if applicable
    if event:
        WaitlistEntry.objects.filter(event=event).delete()
    else:
        WaitlistEntry.objects.all().delete()

    # 14. Delete Capacity Holds
    hold_qs.delete()

    # 15. Reset Capacity Resources
    resource_qs.update(committed_count=0, reserved_count=0)

    # 16. Audit Log
    log_audit(
        actor=performed_by,
        action="RESET_TEST_REGISTRATIONS",
        entity_type="Event",
        entity_id=str(getattr(event, 'slug', 'ALL')),
        reason="Manual test data reset",
        diff=stats
    )

    return stats
