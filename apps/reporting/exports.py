import csv
import io
from django.http import HttpResponse
from apps.core.utils import escape_csv_cell, cents_to_reais
from apps.registrations.models import Registration

def export_participants_csv(event) -> HttpResponse:
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

    registrations = Registration.objects.filter(event=event).select_related(
        "person", "source_order_line", "source_order_line__order"
    ).order_by("registration_number", "created_at")

    for reg in registrations:
        order = getattr(reg.source_order_line, "order", None)
        writer.writerow([
            escape_csv_cell(str(reg.registration_number or "")),
            escape_csv_cell(reg.person.full_name),
            escape_csv_cell(reg.person.email or ""),
            escape_csv_cell(reg.person.phone or ""),
            escape_csv_cell(reg.person.city or ""),
            escape_csv_cell(reg.person.state or ""),
            escape_csv_cell(reg.team_name or reg.person.team_name or ""),
            escape_csv_cell(reg.category_name or ""),
            escape_csv_cell(reg.shirt_size or ""),
            escape_csv_cell(reg.get_commercial_state_display()),
            escape_csv_cell(reg.get_operational_state_display()),
            escape_csv_cell(reg.created_at.strftime("%d/%m/%Y %H:%M")),
            escape_csv_cell(order.merchant_reference if order else "")
        ])

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    filename = f"inscritos_{event.slug}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_financial_csv(event) -> HttpResponse:
    """
    Gera extrato financeiro gerencial detalhado do evento em CSV.
    """
    from apps.finance.models import FinancialEntry
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

    entries = FinancialEntry.objects.filter(event=event).order_by("-recognized_at")
    for entry in entries:
        writer.writerow([
            escape_csv_cell(entry.recognized_at.strftime("%d/%m/%Y %H:%M")),
            escape_csv_cell(entry.get_kind_display()),
            escape_csv_cell(entry.formatted_amount),
            escape_csv_cell(entry.source_reference),
            escape_csv_cell(entry.description)
        ])

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    filename = f"financeiro_{event.slug}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
