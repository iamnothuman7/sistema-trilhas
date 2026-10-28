"""A private downloadable card using the same revocable credential as the scanner."""
from io import BytesIO
from pathlib import Path
import qrcode
import reportlab
from PIL import Image, ImageDraw, ImageFont
from django.core.exceptions import PermissionDenied
from .eligibility import credential_is_available


def build_credential_image(reg):
    if not credential_is_available(reg):
        raise PermissionDenied('Conclua as pendências da inscrição antes de salvar o cartão com QR.')
    directory=Path(reportlab.__file__).parent/'fonts'
    font=lambda size,bold=False:ImageFont.truetype(str(directory/('VeraBd.ttf' if bold else 'Vera.ttf')),size)
    image=Image.new('RGB',(1080,1540),'#191512')
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((35,35,1045,1505),radius=38,fill='#24201c',outline='#66503c',width=2)
    draw.rounded_rectangle((75,76,1005,87),radius=5,fill='#ed702a')
    def lines(text,y,size=34,bold=False,max_width=900,color='#f9f4ec'):
        face=font(size,bold); words=str(text or '').split(); rows=[];line=''
        for word in words:
            trial=(line+' '+word).strip()
            if draw.textlength(trial,font=face)>max_width and line:rows.append(line);line=word
            else:line=trial
        if line:rows.append(line)
        for row in rows[:3]:
            # Ellipsize exceptional unbroken user input instead of drawing outside the card.
            while draw.textlength(row,font=face)>max_width:row=row[:-2]
            draw.text((540,y),row,font=face,fill=color,anchor='mt');y+=size+14
        return y
    lines('INSCRIÇÃO CONFIRMADA',120,26,True,color='#ffad70')
    y=lines(reg.event.title,175,43,True)
    y=lines(reg.person.full_name,y+18,32,True)
    lines(f'Inscrição #{reg.registration_number or str(reg.pk)[:8]} · {reg.shirt_size}',y+8,25)
    qr=qrcode.QRCode(version=None,box_size=1,border=4,error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(reg.credential.raw_token);qr.make(fit=True)
    code=qr.make_image(fill_color='black',back_color='white').convert('RGB')
    scale=520//code.width
    code=code.resize((code.width*scale,code.height*scale),Image.Resampling.NEAREST)
    draw.rectangle((280,540,800,1060),fill='white')
    image.paste(code,((1080-code.width)//2,540+(520-code.height)//2))
    lines(reg.credential.raw_token,1080,23,True)
    from apps.events.raffle_operations import confirmed_registration_tickets
    tickets=list(confirmed_registration_tickets(reg).values_list('number',flat=True))
    if tickets:
        label='Número do sorteio' if len(tickets)==1 else f'Sorteio · {len(tickets)} números confirmados'
        lines(label,1145,27,True,color='#ffad70')
        lines(' · '.join(str(n) for n in tickets[:8])+(' · mais no PDF' if len(tickets)>8 else ''),1190,24,True)
    lines('Apresente este QR no credenciamento.',1260,28,True)
    lines('Válido conforme a situação atual da inscrição.',1315,22)
    lines('Os benefícios são conferidos pelo sistema.',1350,22)
    lines('EQUIPE MORCEGO',1430,25,True,color='#ffad70')
    output=BytesIO();image.save(output,format='PNG',optimize=True)
    return output.getvalue()
