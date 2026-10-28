"""Printable numbered paper tickets and confirmed-number cutouts."""
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


def build_tickets_pdf(raffle, tickets, *, globo=False):
    output=BytesIO();pdf=canvas.Canvas(output,pagesize=A4)
    pdf.setTitle(f'Bilhetes — {raffle.event.title}')
    width,height=A4;margin=32
    columns,rows=(4,10) if globo else (1,6)
    cell_width=(width-margin*2)/columns;cell_height=(height-110)/rows
    per_page=columns*rows
    pages=max(1,(len(tickets)+per_page-1)//per_page)
    for page in range(pages):
        pdf.setFillColor(HexColor('#17120f'));pdf.setFont('Helvetica-Bold',12)
        title='Números confirmados para recorte' if globo else 'Bilhetes em papel · canhoto e participante'
        pdf.drawString(margin,height-32,title)
        pdf.setFont('Helvetica',9)
        pdf.drawString(margin,height-48,raffle.event.title[:95])
        pdf.drawRightString(width-margin,22,f'Página {page+1} de {pages}')
        if not tickets:
            pdf.drawString(margin,height-95,'Nenhum bilhete disponível para esta seleção. Gere o lote no painel interno.')
        for index,ticket in enumerate(tickets[page*per_page:(page+1)*per_page]):
            col,row=index%columns,index//columns;x=margin+col*cell_width;y=height-70-(row+1)*cell_height
            pdf.setStrokeColor(HexColor('#a8a29e'));pdf.setDash(3,3);pdf.rect(x,y,cell_width,cell_height);pdf.setDash()
            if globo:
                pdf.setFont('Helvetica-Bold',30);pdf.drawCentredString(x+cell_width/2,y+cell_height*.42,f'{ticket.number:04d}')
                pdf.setFont('Helvetica',7);pdf.drawCentredString(x+cell_width/2,y+12,{'REGISTRATION':'INSCRIÇÃO','ONLINE':'EXTRA ONLINE','PAPER':'PAPEL'}[ticket.source])
            else:
                split=x+cell_width*.36
                pdf.setDash(3,3);pdf.line(split,y,split,y+cell_height);pdf.setDash()
                for left,right,label in [(x,split,'CANHOTO'),(split,x+cell_width,'PARTICIPANTE')]:
                    pdf.setFillColor(HexColor('#a3440a'));pdf.setFont('Helvetica-Bold',8);pdf.drawString(left+12,y+cell_height-16,label)
                    pdf.setFillColor(HexColor('#17120f'));pdf.setFont('Helvetica-Bold',24);pdf.drawRightString(right-12,y+cell_height-30,f'{ticket.number:04d}')
                    pdf.setFont('Helvetica-Bold',8)
                    for line_no,line in enumerate(simpleSplit(raffle.event.title,'Helvetica-Bold',8,right-left-24)[:2]):
                        pdf.drawString(left+12,y+cell_height-49-line_no*11,line)
                    pdf.setFont('Helvetica',8)
                    if label=='CANHOTO':
                        pdf.drawString(left+12,y+29,'Nome: _______________________')
                        pdf.drawString(left+12,y+15,'Fone: ________________________')
                    else:
                        model=raffle.motorcycle_model or f'{raffle.motorcycle_count} moto(s)'
                        pdf.drawString(left+12,y+29,model[:57])
                        pdf.drawString(left+12,y+15,'Guarde este bilhete para conferir seu número.')
        pdf.showPage()
    pdf.save();return output.getvalue()
