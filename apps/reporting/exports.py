import csv
import io
from decimal import Decimal
from django.http import HttpResponse
from django.utils import timezone
from apps.core.utils import escape_csv_cell, cents_to_reais
from apps.registrations.models import Registration
from apps.checkout.models import Order
from .selectors import financial_entries, in_period, event_timezone


def safe_text(value):
    """Text fields stay text in Excel, including formulas hidden after whitespace."""
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return escape_csv_cell(text)


def decimal_amount(cents):
    # This value is generated from an integer, so signed numbers remain numeric CSV cells.
    return f"{Decimal(int(cents)) / Decimal(100):.2f}".replace(".", ",")


def csv_response(output, filename):
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response

def export_participants_csv(event, start=None, end=None) -> HttpResponse:
    """
    Gera exportação CSV de inscritos do evento com cabeçalho UTF-8 BOM para Excel
    e proteção contra CSV Formula Injection.
    """
    output = io.StringIO()
    # UTF-8 BOM para compatibilidade com Microsoft Excel
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Cabeçalho
    writer.writerow([
        "Número",
        "Nome do Piloto",
        "E-mail",
        "Telefone / WhatsApp",
        "Cidade",
        "UF",
        "Equipe / Moto Clube",
        "Modalidade / Pacote",
        "Tamanho da Camiseta",
        "Situação Comercial",
        "Situação Operacional",
        "Data de Inscrição",
        "Código do Pedido"
    ])

    registrations = in_period(Registration.objects.filter(event=event), "created_at", start, end, tz=event_timezone(event)).select_related(
        "person", "source_order_line", "source_order_line__order"
    ).order_by("registration_number", "created_at")

    for reg in registrations:
        order = getattr(reg.source_order_line, "order", None)
        writer.writerow([
            safe_text(str(reg.registration_number or "")),
            safe_text(reg.person.full_name),
            safe_text(reg.person.email or ""),
            safe_text(reg.person.phone or ""),
            safe_text(reg.person.city or ""),
            safe_text(reg.person.state or ""),
            safe_text(reg.team_name or reg.person.team_name or ""),
            safe_text(reg.category_name or ""),
            safe_text(reg.shirt_size or ""),
            safe_text(reg.get_commercial_state_display()),
            safe_text(reg.get_operational_state_display()),
            timezone.localtime(reg.created_at, event_timezone(event)).strftime("%d/%m/%Y %H:%M"),
            safe_text(order.merchant_reference if order else "")
        ])

    return csv_response(output, f"inscritos_{event.slug}.csv")


def export_financial_csv(event, start=None, end=None) -> HttpResponse:
    """
    Gera extrato financeiro gerencial detalhado do evento em CSV.
    """
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    writer.writerow([
        "Data / Hora",
        "Tipo de Movimento",
        "Valor (R$)",
        "Referência de Origem",
        "Descrição"
    ])

    entries = financial_entries(event, start, end).order_by("-recognized_at", "-pk")
    for entry in entries:
        writer.writerow([
            timezone.localtime(entry.recognized_at, event_timezone(event)).strftime("%d/%m/%Y %H:%M"),
            safe_text(entry.get_kind_display()),
            decimal_amount(entry.amount_cents_signed),
            safe_text(entry.source_reference),
            safe_text(entry.description)
        ])

    return csv_response(output, f"financeiro_{event.slug}.csv")


def export_orders_csv(event, start=None, end=None):
    output = io.StringIO("", newline="")
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Pedido", "Criado em", "Comprador", "E-mail", "Situação", "Bruto (R$)", "Desconto (R$)", "Total do pedido (R$)"])
    orders = in_period(Order.objects.filter(event=event), "created_at", start, end, tz=event_timezone(event)).order_by("created_at", "pk")
    for order in orders.iterator():
        writer.writerow([
            safe_text(order.merchant_reference), timezone.localtime(order.created_at, event_timezone(event)).strftime("%d/%m/%Y %H:%M"),
            safe_text(order.purchaser_name), safe_text(order.purchaser_email), safe_text(order.get_status_display()),
            decimal_amount(order.gross_cents), decimal_amount(order.discount_cents), decimal_amount(order.total_cents),
        ])
    return csv_response(output, f"pedidos_{event.slug}.csv")


def export_summary_csv(event, report, start=None, end=None):
    output = io.StringIO("", newline="")
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Evento", safe_text(event.title)])
    writer.writerow(["Período inicial", start.isoformat() if start else "Todo o histórico"])
    writer.writerow(["Período final", end.isoformat() if end else "Todo o histórico"])
    writer.writerow(["Indicador", "Valor", "Unidade"])
    for label, value in [
        ("Inscrições criadas", report["registrations"]["total"]),
        ("Inscrições ativas", report["registrations"]["active"]),
        ("Credenciados entre as inscrições filtradas", report["registrations"]["checked_in"]),
        ("Pedidos criados", report["orders_count"]),
    ]:
        writer.writerow([label, value, "quantidade"])
    for label, key in [
        ("Receitas registradas", "revenue_cents"), ("Reembolsos", "refunds_cents"),
        ("Tarifas registradas", "fees_cents"), ("Despesas pagas no razão", "paid_expenses_cents"),
        ("Resultado do período", "net_cents"), ("Despesas a pagar por vencimento", "pending_expenses_cents"),
    ]:
        writer.writerow([label, decimal_amount(report["finance"][key]), "BRL"])
    return csv_response(output, f"resumo_{event.slug}.csv")
