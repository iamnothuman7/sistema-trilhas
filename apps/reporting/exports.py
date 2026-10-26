import csv
import io
from decimal import Decimal
from django.http import HttpResponse
from django.utils import timezone
from apps.core.utils import escape_csv_cell, cents_to_reais
from apps.registrations.models import Registration
from apps.checkout.models import Order
from .selectors import financial_entries, in_period, event_timezone


def table_csv(event, name, headers, rows):
    output = io.StringIO(newline='')
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(headers)
    for row in rows:
        writer.writerow([format(value, '.2f').replace('.', ',') if isinstance(value, Decimal)
            else value if isinstance(value, int) else safe_text(value) for value in row])
    return csv_response(output, f'{name}_{event.slug}.csv')


AGGREGATE_REPORTS = {
    'statuses': ('Situação dos pedidos', 'orders_by_status', ['Situação', 'Pedidos', 'Total (R$)'], ['label', 'count', 'total_cents']),
    'categories': ('Categorias', 'categories', ['Categoria', 'Inscrições'], ['category_name', 'count']),
    'shirts': ('Tamanhos de camiseta', 'shirts', ['Tamanho', 'Inscrições ativas'], ['shirt_size', 'count']),
    'teams': ('Equipes', 'teams', ['Equipe', 'Inscrições ativas'], ['name', 'count']),
    'cities': ('Cidades', 'cities', ['Cidade', 'UF', 'Inscrições ativas'], ['name', 'state', 'count']),
    'daily': ('Movimentação diária', 'daily', ['Dia', 'Movimentações', 'Receitas (R$)', 'Resultado (R$)'], ['day', 'count', 'revenue_cents', 'net_cents']),
}


def export_aggregate_csv(event, start=None, end=None, *, section, report=None):
    from .selectors import get_event_report
    report = report if report is not None else get_event_report(event, start, end)
    _, key, headers, fields = AGGREGATE_REPORTS[section]
    rows = ([Decimal(item.get(field) or 0) / 100 if field.endswith('_cents') else item.get(field) for field in fields] for item in report[key])
    return table_csv(event, section, headers, rows)


def export_expenses_csv(event, start=None, end=None):
    from apps.finance.models import Expense
    entries = in_period(Expense.objects.filter(event=event), 'due_at', start, end, date_field=True).select_related('vendor').order_by('due_at', 'pk')
    return table_csv(event, 'despesas', ['Descrição', 'Categoria', 'Fornecedor', 'Vencimento', 'Pago em', 'Situação', 'Valor (R$)'],
        ([item.description, item.get_category_display(), item.vendor.name if item.vendor else '', item.due_at, item.paid_at,
          item.get_status_display(), Decimal(item.amount_cents) / 100] for item in entries.iterator()))


def export_sponsorships_csv(event, start=None, end=None):
    from apps.events.sponsorships import decorate_balance, sponsorships_with_balances
    # Agreements are a current-position report, including agreements without a due date.
    sponsors = (decorate_balance(s) for s in sponsorships_with_balances(event.sponsors.all()))
    return table_csv(event, 'patrocinios_saldos_atuais',
        ['Patrocinador', 'Cota', 'Exibição autorizada', 'Combinado (R$)', 'Recebido líquido (R$)', 'A receber (R$)', 'Vencimento', 'Situação atual', 'Observações do acordo'],
        ([s.name, s.get_tier_display(), 'Sim' if s.is_public else 'Não',
          Decimal(s.agreed_amount_cents) / 100 if s.agreed_amount_cents is not None else '', Decimal(s.received_cents) / 100,
          Decimal(s.balance_cents) / 100 if s.balance_cents is not None else '', s.due_at, s.payment_label, s.agreement_notes] for s in sponsors))


def export_sponsor_receipts_csv(event, start=None, end=None):
    from apps.finance.models import SponsorPayment
    payments = in_period(SponsorPayment.objects.filter(sponsorship__event=event), 'received_on', start, end, date_field=True).select_related('sponsorship', 'created_by', 'reversed_by')
    return table_csv(event, 'recebimentos_patrocinadores',
        ['ID da baixa', 'Patrocinador', 'Recebido em', 'Valor original (R$)', 'Forma', 'Referência', 'Situação atual', 'Operador', 'Revertido em', 'Motivo da reversão', 'Observações'],
        ([str(p.pk), p.sponsorship.name, p.received_on, Decimal(p.amount_cents) / 100, p.get_method_display(), p.reference,
          'Revertido' if p.reversed_at else 'Confirmado', p.created_by.full_name if p.created_by else '', p.reversed_at.isoformat() if p.reversed_at else '', p.reversal_reason, p.notes] for p in payments.iterator()))


def export_checkins_csv(event, start=None, end=None):
    from apps.operations.models import CheckinRecord
    entries = in_period(CheckinRecord.objects.filter(event=event), 'occurred_at', start, end, tz=event_timezone(event)).select_related('registration__person', 'operator').order_by('occurred_at', 'pk')
    return table_csv(event, 'credenciamentos', ['Inscrição', 'Participante', 'Data e hora', 'Forma', 'Operador'],
        ([item.registration.registration_number, item.registration.person.full_name, timezone.localtime(item.occurred_at, event_timezone(event)).isoformat(),
          item.get_checkin_type_display(), item.operator.full_name] for item in entries.iterator()))


def export_benefits_csv(event, start=None, end=None):
    from apps.registrations.models import Entitlement
    entries = in_period(Entitlement.objects.filter(registration__event=event), 'registration__created_at', start, end, tz=event_timezone(event)).select_related('registration__person').order_by('registration__registration_number', 'pk')
    return table_csv(event, 'beneficios', ['Inscrição', 'Participante', 'Benefício', 'Descrição', 'Camiseta', 'Concedidos', 'Entregues', 'Restantes', 'Situação'],
        ([item.registration.registration_number, item.registration.person.full_name, item.get_benefit_type_display(), item.title, item.registration.shirt_size,
          item.quantity_total, item.quantity_used, 0 if item.state == 'REVOKED' else item.remaining, item.get_state_display()] for item in entries.iterator()))


def export_deliveries_csv(event, start=None, end=None):
    from apps.registrations.models import Consumption
    entries = in_period(Consumption.objects.filter(entitlement__registration__event=event), 'occurred_at', start, end, tz=event_timezone(event)).select_related('entitlement__registration__person', 'operator').order_by('occurred_at', 'pk')
    return table_csv(event, 'entregas', ['Inscrição', 'Participante', 'Benefício', 'Quantidade', 'Ação', 'Data e hora', 'Operador', 'Local'],
        ([item.entitlement.registration.registration_number, item.entitlement.registration.person.full_name, item.entitlement.title, item.quantity, item.action,
          timezone.localtime(item.occurred_at, event_timezone(event)).isoformat(), item.operator.full_name, item.location] for item in entries.iterator()))


def export_bundle(event, exporters, start=None, end=None):
    import tempfile
    import zipfile
    from django.http import FileResponse
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, exporter in exporters.items():
                archive.writestr(f'{name}.csv', exporter(event, start, end).content)
            archive.writestr('LEIA-ME.txt', '\n'.join([
                f'Evento: {event.title}', f'Fuso: {event.timezone}', f'De: {start or "início do histórico"}', f'Até: {end or "fim do histórico"}',
                'CSV UTF-8 com separador ponto e vírgula; abra no Excel ou LibreOffice.',
                'Inscrições, pedidos e benefícios: data de criação da inscrição/pedido; situação atual.',
                'Financeiro: reconhecimento. Despesas: vencimento. Credenciamentos e entregas: data da operação.',
                'Patrocínios: saldos atuais de todos os acordos, independente do período. Recebimentos de patrocinadores: data do recebimento, com situação atual. Reversões constam no extrato financeiro na data da correção.',
                'Equipes, cidades e tamanhos correspondem às tabelas do painel de relatórios, não ao ranking público normalizado.',
                'Inclui todos os relatórios permitidos para esta conta, sem limitar à página atual.',
                'Arquivos: ' + ', '.join(exporters),
            ]))
        output.seek(0)
        response = FileResponse(output, as_attachment=True, filename=f'relatorios_{event.slug}.zip', content_type='application/zip')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except Exception:
        output.close()
        raise


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
        ("Patrocínios recebidos líquidos no período (já incluídos nas receitas)", "sponsor_received_cents"),
        ("Patrocínios a receber (saldo atual de todos os acordos)", "sponsor_outstanding_cents"),
        ("Patrocínios combinados (total atual)", "sponsor_agreed_cents"),
    ]:
        writer.writerow([label, decimal_amount(report["finance"][key]), "BRL"])
    return csv_response(output, f"resumo_{event.slug}.csv")
