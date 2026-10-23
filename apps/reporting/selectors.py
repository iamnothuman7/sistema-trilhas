from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db.models import Sum, Count, Q
from django.db.models.functions import Trim, TruncDate
from django.utils import timezone
from apps.events.models import Event
from apps.checkout.models import Order, OrderStatus
from apps.registrations.models import Registration, CommercialState, OperationalState, Entitlement, EntitlementState
from apps.finance.models import FinancialEntry, FinancialEntryKind, Expense, ExpenseStatus
from apps.catalog.models import CapacityResource
from apps.core.utils import cents_to_reais

def get_event_kpi_summary(event: Event) -> dict:
    """
    Retorna os principais KPIs e métricas consolidadas do evento para o painel de gestão.
    """
    # Inscrições
    regs = Registration.objects.filter(event=event)
    total_registered = regs.count()
    active_confirmed = regs.filter(commercial_state=CommercialState.ACTIVE).count()
    ready_for_checkin = regs.filter(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.READY).count()
    checked_in_count = regs.filter(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.CHECKED_IN).count()
    pending_terms = regs.filter(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.NOT_READY).count()

    # Vendas e Pedidos
    orders = Order.objects.filter(event=event)
    orders_paid = orders.filter(status=OrderStatus.PAID)
    orders_pending = orders.filter(status__in=[OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT], expires_at__gte=timezone.now())

    total_gross_cents = orders_paid.aggregate(s=Sum("gross_cents"))["s"] or 0
    total_discount_cents = orders_paid.aggregate(s=Sum("discount_cents"))["s"] or 0
    pending_to_receive_cents = orders_pending.aggregate(s=Sum("total_cents"))["s"] or 0

    # Resultado Líquido Gerencial
    # The ledger keeps receipts/refunds even after the order changes status.
    financial = get_financial_summary(event)
    total_received_cents = financial["revenue_cents"]
    total_fees_cents = financial["fees_cents"]
    total_expenses_cents = financial["paid_expenses_cents"]
    net_profit_cents = financial["net_cents"]

    # Capacidade Geral
    cap_resource = CapacityResource.objects.filter(event=event, type="EVENT_OVERALL").first()
    max_capacity = cap_resource.capacity_total if cap_resource else event.max_attendees
    occupied = cap_resource.reserved_count + max(active_confirmed, cap_resource.committed_count) if cap_resource else active_confirmed
    vacancies_left = max(0, max_capacity - occupied)

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
        "total_refunds_cents": financial["refunds_cents"],
        "pending_expenses_cents": financial["pending_expenses_cents"],
        "net_profit_cents": net_profit_cents,
        "shirts_total": shirts_total,
        "shirts_delivered": shirts_delivered,
    }


REVENUE_KINDS = (
    FinancialEntryKind.RECEITA_INSCRICAO,
    FinancialEntryKind.RECEITA_ADICIONAL,
    FinancialEntryKind.RECEITA_MANUAL,
)


def event_timezone(event):
    try:
        return ZoneInfo(event.timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.get_default_timezone()


def in_period(queryset, field, start=None, end=None, *, date_field=False, tz=None):
    """Inclusive calendar dates, interpreted in the event timezone when supplied."""
    if start:
        lower = start if date_field else timezone.make_aware(datetime.combine(start, time.min), tz)
        queryset = queryset.filter(**{field + "__gte": lower})
    if end:
        upper = end if date_field else timezone.make_aware(datetime.combine(end, time.max), tz)
        queryset = queryset.filter(**{field + "__lte": upper})
    return queryset


def financial_entries(event, start=None, end=None):
    return in_period(FinancialEntry.objects.filter(event=event), "recognized_at", start, end, tz=event_timezone(event))


def get_financial_summary(event, start=None, end=None):
    entries = financial_entries(event, start, end)
    values = entries.aggregate(
        revenue=Sum("amount_cents_signed", filter=Q(kind__in=REVENUE_KINDS)),
        refunds=Sum("amount_cents_signed", filter=Q(kind=FinancialEntryKind.REEMBOLSO)),
        fees=Sum("amount_cents_signed", filter=Q(kind=FinancialEntryKind.TAXA_MERCADO_PAGO)),
        expenses=Sum("amount_cents_signed", filter=Q(kind=FinancialEntryKind.DESPESA)),
        transfers=Sum("amount_cents_signed", filter=Q(kind=FinancialEntryKind.TRANSFERENCIA_BANCO)),
        count=Count("pk"),
    )
    pending = in_period(Expense.objects.filter(event=event, status=ExpenseStatus.PENDING), "due_at", start, end, date_field=True)
    pending_values = pending.aggregate(total=Sum("amount_cents"), count=Count("pk"))
    paid_records = in_period(Expense.objects.filter(event=event, status=ExpenseStatus.PAID), "paid_at", start, end, date_field=True)
    paid_record_total = paid_records.aggregate(total=Sum("amount_cents"))["total"] or 0
    result = {
        "revenue_cents": values["revenue"] or 0,
        "refunds_cents": -(values["refunds"] or 0),
        "fees_cents": -(values["fees"] or 0),
        "paid_expenses_cents": -(values["expenses"] or 0),
        "pending_expenses_cents": pending_values["total"] or 0,
        "transfers_cents": values["transfers"] or 0,
        "entry_count": values["count"],
        "pending_expense_count": pending_values["count"],
    }
    result["net_cents"] = result["revenue_cents"] - result["refunds_cents"] - result["fees_cents"] - result["paid_expenses_cents"]
    result["expense_reconciliation_delta_cents"] = paid_record_total - result["paid_expenses_cents"]
    for key, value in list(result.items()):
        if key.endswith("_cents"):
            result[key[:-6] + "_formatted"] = cents_to_reais(value)
    return result


def get_event_report(event, start=None, end=None):
    registrations = in_period(Registration.objects.filter(event=event), "created_at", start, end, tz=event_timezone(event))
    active_registrations = registrations.filter(commercial_state=CommercialState.ACTIVE)
    # Only aggregate names and counts; no individual participant data is exposed.
    missing_group = Q(name__isnull=True) | Q(name="") | Q(name__iregex=r"^n[ãa]o\s*informad[oa]$")
    teams = list(active_registrations.annotate(name=Trim("team_name")).exclude(missing_group)
                 .values("name").annotate(count=Count("pk")).order_by("-count", "name"))
    cities = list(active_registrations.annotate(name=Trim("person__city"), state=Trim("person__state"))
                  .exclude(missing_group).values("name", "state").annotate(count=Count("pk"))
                  .order_by("-count", "name", "state"))
    orders = in_period(Order.objects.filter(event=event), "created_at", start, end, tz=event_timezone(event))
    counts = registrations.aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(commercial_state=CommercialState.ACTIVE)),
        checked_in=Count("pk", filter=Q(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.CHECKED_IN)),
        awaiting_terms=Count("pk", filter=Q(commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.NOT_READY)),
    )
    order_totals = orders.aggregate(count=Count("pk"), amount=Sum("total_cents"))
    statuses = dict(OrderStatus.choices)
    orders_by_status = list(orders.values("status").annotate(count=Count("pk"), total_cents=Sum("total_cents")).order_by("status"))
    for item in orders_by_status:
        item["label"] = statuses.get(item["status"], item["status"])
    daily = list(
        financial_entries(event, start, end)
        .exclude(kind=FinancialEntryKind.TRANSFERENCIA_BANCO)
        .annotate(day=TruncDate("recognized_at", tzinfo=event_timezone(event)))
        .values("day")
        .annotate(
            revenue_cents=Sum("amount_cents_signed", filter=Q(kind__in=REVENUE_KINDS)),
            net_cents=Sum("amount_cents_signed"),
            count=Count("pk"),
        ).order_by("-day")
    )
    return {
        "registrations": counts,
        "orders_count": order_totals["count"],
        "orders_total_cents": order_totals["amount"] or 0,
        "orders_by_status": orders_by_status,
        "categories": list(registrations.values("category_name").annotate(count=Count("pk")).order_by("-count", "category_name")),
        "shirts": list(active_registrations.values("shirt_size").annotate(count=Count("pk")).order_by("shirt_size")),
        "teams": teams,
        "cities": cities,
        "finance": get_financial_summary(event, start, end),
        "daily": daily,
    }
