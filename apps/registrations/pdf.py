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
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=36, leftMargin=36,
        topMargin=96, bottomMargin=36, title=title, author="Equipe Morcego", pageCompression=1)

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#191d1a")); canvas.rect(0, height-80, width, 80, fill=1, stroke=0)
        logo = Path(settings.BASE_DIR) / "frontend/static/img/logo-nav.webp"
        if logo.is_file():
            canvas.drawImage(ImageReader(str(logo)), 36, height-68, width=56, height=48, preserveAspectRatio=True, mask="auto")
        canvas.setFont("TrilhasPDFBold", 13); canvas.setFillColor(colors.white)
        canvas.drawString(104, height-36, "EQUIPE MORCEGO")
        canvas.setFont("TrilhasPDF", 8.5); canvas.setFillColor(colors.HexColor("#d1d8d2"))
        canvas.drawString(104, height-52, "Comprovante Oficial de Inscrição e Credenciamento")
        canvas.setFillColor(orange); canvas.rect(0, height-84, width, 4, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#dfe4df")); canvas.line(36, 32, width-36, 32)
        canvas.setFont("TrilhasPDF", 6.5); canvas.setFillColor(muted)
        canvas.drawString(36, 22, f"Emitido em {issued:%d/%m/%Y às %H:%M} ({reg.event.timezone}) · Apresente este documento no credenciamento")
        canvas.drawRightString(width-36, 22, "Página 1 de 1")
        canvas.restoreState()

    story = [paragraph(title, "title"), paragraph(reg.event.title, "heading")]
    status_box = Table([[paragraph(state["label"], "status")]], colWidths=[available])
    status_box.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#fff2e8")),
        ("BOX", (0,0), (-1,-1), .6, colors.HexColor("#fed7aa")),
        ("LEFTPADDING", (0,0), (-1,-1), 10), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    story += [status_box, Spacer(1, 8)]
    if not state["confirmed"]:
        story.append(paragraph("Este documento informa a situação atual. Não confirma a participação nem autoriza o credenciamento ou a retirada de benefícios."))

    details = [
        ("PARTICIPANTE", reg.person.full_name),
        ("INSCRIÇÃO", f"#{reg.registration_number}" if reg.registration_number else "Número ainda não atribuído"),
        ("MODALIDADE", reg.category_name),
        ("CIDADE / UF", f"{reg.person.city}/{reg.person.state}" if reg.person.city and reg.person.state else "Não informada"),
        ("EQUIPE", reg.team_name or reg.person.team_name or "Não informada"),
        ("CAMISETA", (reg.shirt_size or "Não informada") + (" · " + reg.get_vehicle_type_display() if reg.vehicle_type else "")),
        ("EVENTO", f"{starts:%d/%m/%Y %H:%M} a {ends:%d/%m/%Y %H:%M}"),
        ("LOCAL", reg.event.venue or "A confirmar pela organização"),
        ("SITUAÇÃO", "CONFIRMADO (Apto para Retirada de Kit)" if state["qr_available"] else "Aguardando Confirmação"),
    ]
    from apps.events.models_raffles import RaffleTicket
    from apps.events.raffle_operations import confirmed_registration_tickets
    ticket = RaffleTicket.objects.filter(registration=reg, state='SOLD').first() if state['confirmed'] else None
    if ticket:
        details.append(('SORTEIO MOTO', f"Número da Sorte: #{ticket.number} (1 Ponto Incluso)"))
    extra_tickets = list(confirmed_registration_tickets(reg).filter(source='ONLINE').values_list('number', flat=True)) if state['confirmed'] else []
    if extra_tickets:
        details.append(('PONTOS EXTRAS', ' · '.join(str(n) for n in extra_tickets[:15])))

    order = reg.source_order_line.order
    if order.applied_coupon_code:
        details.append(('CUPOM', f'{order.applied_coupon_code} (Desconto: R$ {order.formatted_discount})'))

    left_table = Table([[paragraph(label, "label"), paragraph(value)] for label, value in details], colWidths=[80, available - 220])
    left_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LINEBELOW", (0,0), (-1,-2), .3, colors.HexColor("#e9ece8"))]))

    if state["qr_available"]:
        qr_buffer = BytesIO()
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
        qr.add_data(reg.credential.raw_token); qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(qr_buffer, format="PNG"); qr_buffer.seek(0)
        qr_cell = [
            Image(qr_buffer, width=105, height=105),
            Spacer(1, 3),
            paragraph(f"#{reg.registration_number}" if reg.registration_number else "", "status"),
            paragraph(reg.credential.raw_token[:16] + "...", "token")
        ]
        qr_box = Table([[qr_cell]], colWidths=[130])
        qr_box.setStyle(TableStyle([("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f8fafc")), ("BOX", (0,0), (-1,-1), .5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6)]))
        main_grid = Table([[left_table, qr_box]], colWidths=[available - 138, 138])
    else:
        main_grid = Table([[left_table]], colWidths=[available])

    main_grid.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story += [main_grid, Spacer(1, 10)]

    benefits = list(reg.entitlements.all().order_by("benefit_type", "pk"))
    if benefits:
        story.append(paragraph("Itens e Benefícios a Retirar no Evento", "heading"))
        rows = [[paragraph("ITEM", "label"), paragraph("QUANTIDADE", "label"), paragraph("SITUAÇÃO", "label")]]
        for benefit in benefits:
            situation = "Entregue" if not benefit.remaining else "Disponível para Retirada"
            rows.append([paragraph(benefit.title), paragraph(f"{benefit.quantity_used}/{benefit.quantity_total}"), paragraph(situation)])
        table = Table(rows, colWidths=[available-170, 75, 95], repeatRows=1)
        table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f1f5f9")),
            ("LINEBELOW", (0,0), (-1,-1), .3, colors.HexColor("#e2e8f0")), ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4)]))
        story += [table, Spacer(1, 8)]

    story.append(paragraph("Instruções de Credenciamento", "heading"))
    instructions_text = (
        "1. Apresente este comprovante (impresso ou no celular) na portaria do evento juntamente com documento oficial com foto. "
        "2. A entrega do kit oficial e camiseta será realizada conforme o tamanho indicado acima. "
        "3. Em caso de dúvidas ou necessidade de transferência, contate a organização oficial da Equipe Morcego."
    )
    story.append(paragraph(instructions_text, "small"))
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
