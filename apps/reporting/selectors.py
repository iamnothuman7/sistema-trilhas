from django.db.models import Sum, Count, Q
from django.utils import timezone
from apps.events.models import Event
from apps.checkout.models import Order, OrderStatus
from apps.registrations.models import Registration, CommercialState, OperationalState, Entitlement, EntitlementState
from apps.finance.models import FinancialEntry, Expense
from apps.catalog.models import CapacityResource

def get_event_kpi_summary(event: Event) -> dict:
    """
    Retorna os principais KPIs e métricas consolidadas do evento para o painel de gestão.
    """
    # Inscrições
    regs = Registration.objects.filter(event=event)
    total_registered = regs.count()
    active_confirmed = regs.filter(commercial_state=CommercialState.ACTIVE).count()
    ready_for_checkin = regs.filter(operational_state=OperationalState.READY).count()
    checked_in_count = regs.filter(operational_state=OperationalState.CHECKED_IN).count()
    pending_terms = regs.filter(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.NOT_READY).count()

    # Vendas e Pedidos
    orders = Order.objects.filter(event=event)
    orders_paid = orders.filter(status=OrderStatus.PAID)
    orders_pending = orders.filter(status__in=[OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT])

    total_gross_cents = orders_paid.aggregate(s=Sum("gross_cents"))["s"] or 0
    total_discount_cents = orders_paid.aggregate(s=Sum("discount_cents"))["s"] or 0
    total_received_cents = orders_paid.aggregate(s=Sum("total_cents"))["s"] or 0
    pending_to_receive_cents = orders_pending.aggregate(s=Sum("total_cents"))["s"] or 0

    # Despesas e Resultado
    expenses = Expense.objects.filter(event=event, status="PAID")
    total_expenses_cents = expenses.aggregate(s=Sum("amount_cents"))["s"] or 0

    # Taxas Mercado Pago estimadas/reais
    fee_entries = FinancialEntry.objects.filter(event=event, kind="TAXA_MERCADO_PAGO")
    total_fees_cents = abs(fee_entries.aggregate(s=Sum("amount_cents_signed"))["s"] or 0)

    # Resultado Líquido Gerencial
    net_profit_cents = total_received_cents - total_fees_cents - total_expenses_cents

    # Capacidade Geral
    cap_resource = CapacityResource.objects.filter(event=event, type="EVENT_OVERALL").first()
    max_capacity = cap_resource.capacity_total if cap_resource else event.max_attendees
    vacancies_left = max(0, max_capacity - active_confirmed)

    # Kits e Camisetas
    shirt_entitlements = Entitlement.objects.filter(registration__event=event, benefit_type="CAMISETA")
    shirts_total = shirt_entitlements.aggregate(s=Sum("quantity_total"))["s"] or 0
    shirts_delivered = shirt_entitlements.aggregate(s=Sum("quantity_used"))["s"] or 0

    return {
        "total_registered": total_registered,
        "active_confirmed": active_confirmed,
        "ready_for_checkin": ready_for_checkin,
        "checked_in_count": checked_in_count,
        "pending_terms": pending_terms,
        "max_capacity": max_capacity,
        "vacancies_left": vacancies_left,
        "total_gross_cents": total_gross_cents,
        "total_discount_cents": total_discount_cents,
        "total_received_cents": total_received_cents,
        "pending_to_receive_cents": pending_to_receive_cents,
        "total_fees_cents": total_fees_cents,
        "total_expenses_cents": total_expenses_cents,
        "net_profit_cents": net_profit_cents,
        "shirts_total": shirts_total,
        "shirts_delivered": shirts_delivered,
    }
