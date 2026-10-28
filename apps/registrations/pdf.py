"""Private, on-demand registration documents. No files or credentials are persisted."""
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from django.conf import settings
from django.utils import timezone

from apps.checkout.models import OrderStatus
from apps.events.models import EventStatus
from .eligibility import eligibility_reason
from .instructions import PICKUP_INSTRUCTIONS
from .models import CommercialState, EntitlementState


def document_state(reg):
    paid = reg.source_order_line.order.status == OrderStatus.PAID
    confirmed = paid and reg.commercial_state == CommercialState.ACTIVE
    if reg.event.status == EventStatus.CANCELED:
        label, confirmed = "EVENTO CANCELADO", False
    elif reg.commercial_state == CommercialState.CANCELED:
        label, confirmed = "INSCRIÇÃO CANCELADA", False
    elif reg.commercial_state == CommercialState.TRANSFERRED:
        label, confirmed = "INSCRIÇÃO TRANSFERIDA", False
    elif reg.source_order_line.order.status in (OrderStatus.REFUNDED, OrderStatus.PARTIALLY_REFUNDED):
        label, confirmed = "PAGAMENTO REEMBOLSADO", False
    elif reg.source_order_line.order.status == OrderStatus.PAID_NEEDS_REVIEW:
        label, confirmed = "PAGAMENTO EM REVISÃO", False
    elif reg.source_order_line.order.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED):
        label, confirmed = "PEDIDO CANCELADO OU EXPIRADO", False
    elif confirmed:
        label = "INSCRIÇÃO CONFIRMADA"
    else:
        label = "PENDENTE DE CONFIRMAÇÃO"
    reason = eligibility_reason(reg)
    return {"label": label, "confirmed": confirmed, "qr_available": not reason, "reason": reason}


def _text(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "Não informado"))
    return escape(text).replace("\n", "<br/>")


def _fonts():
    # Vera ships with ReportLab, including its license; the PDF embeds the fonts.
    directory = Path(reportlab.__file__).parent / "fonts"
    for name, filename in (("TrilhasPDF", "Vera.ttf"), ("TrilhasPDFBold", "VeraBd.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(directory / filename)))


def build_registration_pdf(reg, *, generated_at=None):
    """Caller must authorize the registration. Generate from current database state."""
    _fonts()
    state = document_state(reg)
    try:
        zone = ZoneInfo(reg.event.timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = timezone.get_default_timezone()
    issued = timezone.localtime(generated_at or timezone.now(), zone)
    starts = timezone.localtime(reg.event.starts_at, zone)
    ends = timezone.localtime(reg.event.ends_at, zone)
    orange, ink, muted = colors.HexColor("#ea580c"), colors.HexColor("#202221"), colors.HexColor("#59635e")
    styles = {
        "body": ParagraphStyle("body", fontName="TrilhasPDF", fontSize=9, leading=13, textColor=ink, spaceAfter=5),
        "small": ParagraphStyle("small", fontName="TrilhasPDF", fontSize=7.5, leading=10.5, textColor=muted),
        "label": ParagraphStyle("label", fontName="TrilhasPDFBold", fontSize=8, leading=11, textColor=muted),
        "title": ParagraphStyle("title", fontName="TrilhasPDFBold", fontSize=19, leading=24, textColor=ink, spaceAfter=8),
        "heading": ParagraphStyle("heading", fontName="TrilhasPDFBold", fontSize=11, leading=15, textColor=ink, spaceBefore=8, spaceAfter=5, keepWithNext=True),
        "status": ParagraphStyle("status", fontName="TrilhasPDFBold", fontSize=10, leading=14, textColor=orange),
        "token": ParagraphStyle("token", fontName="Courier", fontSize=7.5, leading=11, alignment=TA_CENTER, textColor=ink),
    }
    paragraph = lambda value, style="body": Paragraph(_text(value), styles[style])
    output = BytesIO()
    width, height = A4
    available = width - 80
    title = "Comprovante de inscrição" if state["confirmed"] else "Situação da inscrição"
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=40, leftMargin=40,
        topMargin=113, bottomMargin=54, title=title, author="Equipe Morcego", pageCompression=1)

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#191d1a")); canvas.rect(0, height-87, width, 87, fill=1, stroke=0)
        logo = Path(settings.BASE_DIR) / "frontend/static/img/logo-nav.webp"
        if logo.is_file():
            canvas.drawImage(ImageReader(str(logo)), 40, height-74, width=64, height=53, preserveAspectRatio=True, mask="auto")
        canvas.setFont("TrilhasPDFBold", 14); canvas.setFillColor(colors.white)
        canvas.drawString(119, height-39, "EQUIPE MORCEGO")
        canvas.setFont("TrilhasPDF", 9); canvas.setFillColor(colors.HexColor("#d1d8d2"))
        canvas.drawString(119, height-57, "Área do participante")
        canvas.setFillColor(orange); canvas.rect(0, height-91, width, 4, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#dfe4df")); canvas.line(40, 47, width-40, 47)
        canvas.setFont("TrilhasPDF", 7); canvas.setFillColor(muted)
        canvas.drawString(40, 35, f"Emitido em {issued:%d/%m/%Y às %H:%M} ({reg.event.timezone})")
        canvas.drawString(40, 24, "Documento pessoal - consulte a situação atual na área do participante")
        canvas.drawRightString(width-40, 24, f"Página {doc.page}")
        canvas.restoreState()

    story = [paragraph(title, "title"), paragraph(reg.event.title, "heading")]
    status_box = Table([[paragraph(state["label"], "status")]], colWidths=[available])
    status_box.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#fff2e8")),
        ("BOX", (0,0), (-1,-1), .6, colors.HexColor("#fed7aa")),
        ("LEFTPADDING", (0,0), (-1,-1), 12), ("TOPPADDING", (0,0), (-1,-1), 10), ("BOTTOMPADDING", (0,0), (-1,-1), 10)]))
    story += [status_box, Spacer(1, 12)]
    if not state["confirmed"]:
        story.append(paragraph("Este documento informa a situação atual. Não confirma a participação nem autoriza o credenciamento ou a retirada de benefícios."))
    details = [
        ("PARTICIPANTE", reg.person.full_name),
        ("INSCRIÇÃO", f"#{reg.registration_number}" if reg.registration_number else "Número ainda não atribuído"),
        ("MODALIDADE", reg.category_name),
        ("EQUIPE", reg.team_name or reg.person.team_name or "Não informada"),
        ("CAMISETA", (reg.shirt_size or "Não informada") + (" · " + reg.get_vehicle_type_display() if reg.vehicle_type else "")),
        ("EVENTO", f"{starts:%d/%m/%Y %H:%M} a {ends:%d/%m/%Y %H:%M} ({reg.event.timezone})"),
        ("LOCAL", reg.event.venue or "A confirmar pela organização"),
        ("PAGAMENTO", reg.source_order_line.order.get_status_display()),
        ("OPERAÇÃO", reg.get_operational_state_display() if state["qr_available"] else "Credenciamento e retirada bloqueados no momento"),
    ]
    from apps.events.models_raffles import RaffleTicket
    from apps.events.raffle_operations import confirmed_registration_tickets
    ticket = RaffleTicket.objects.filter(registration=reg, state='SOLD').first() if state['confirmed'] else None
    if ticket:
        details.append(('NÚMERO DO SORTEIO', str(ticket.number)))
    order = reg.source_order_line.order
    if order.applied_coupon_code:
        details.append(('CUPOM NA COMPRA', f'{order.applied_coupon_code} · Desconto no pedido: R$ {order.formatted_discount}'))
    if order.payment_fee_cents:
        details.append(('VALOR DO PEDIDO', f'Itens R$ {order.formatted_base_total} + tarifa R$ {order.formatted_payment_fee} = R$ {order.formatted_total}'))
    data_table = Table([[paragraph(label, "label"), paragraph(value)] for label, value in details], colWidths=[95, available-95])
    data_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
        ("LINEBELOW", (0,0), (-1,-2), .3, colors.HexColor("#e9ece8"))]))
    story.append(data_table)
    extra_tickets=list(confirmed_registration_tickets(reg).filter(source='ONLINE').values_list('number',flat=True)) if state['confirmed'] else []
    if extra_tickets:
        story.append(paragraph('Números extras confirmados no sorteio','heading'))
        for offset in range(0,len(extra_tickets),20):
            story.append(paragraph(' · '.join(str(number) for number in extra_tickets[offset:offset+20])))
    if state["qr_available"]:
        qr_buffer = BytesIO()
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
        qr.add_data(reg.credential.raw_token); qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(qr_buffer, format="PNG"); qr_buffer.seek(0)
        qr_cell = [Image(qr_buffer, width=126, height=126), paragraph(reg.credential.raw_token, "token")]
        content = [paragraph("Sua credencial", "heading"), paragraph("Apresente o QR Code à equipe para consultar a inscrição e os benefícios disponíveis."),
            paragraph("O pagamento e o aceite foram conferidos na emissão. Cancelamento, revogação ou alteração posterior podem impedir o uso.", "small")]
        credential = Table([[content, qr_cell]], colWidths=[available-170, 170])
        credential.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("ALIGN", (1,0), (1,0), "CENTER"),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f6f8f5")), ("LEFTPADDING", (0,0), (-1,-1), 12),
            ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8)]))
        story += [Spacer(1, 12), credential]
    else:
        story += [Spacer(1, 9), paragraph("QR Code indisponível", "heading"), paragraph(state["reason"])]
    benefits = list(reg.entitlements.all().order_by("benefit_type", "pk"))
    if benefits:
        story.append(paragraph("Benefícios da inscrição", "heading"))
        rows = [[paragraph("BENEFÍCIO", "label"), paragraph("ENTREGUE", "label"), paragraph("SITUAÇÃO", "label")]]
        for benefit in benefits:
            situation = "Revogado" if benefit.state == EntitlementState.REVOKED else (
                "Concluído" if not benefit.remaining else (f"{benefit.remaining} disponível(is)" if state["qr_available"] else f"Saldo {benefit.remaining}; retirada bloqueada"))
            rows.append([paragraph(benefit.title), paragraph(f"{benefit.quantity_used}/{benefit.quantity_total}"), paragraph(situation)])
        table = Table(rows, colWidths=[available-180, 70, 110], repeatRows=1)
        table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f1f3ef")),
            ("LINEBELOW", (0,0), (-1,-1), .3, colors.HexColor("#e1e5de")), ("TOPPADDING", (0,0), (-1,-1), 5)]))
        story.append(table)
    story.append(paragraph("Credenciamento e retirada", "heading"))
    for index, instruction in enumerate(PICKUP_INSTRUCTIONS, 1):
        story.append(paragraph(f"{index}. {instruction}", "small")); story.append(Spacer(1, 2))
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
