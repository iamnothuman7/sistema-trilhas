"""Bounded receipt uploads and storage cleanup when a financial mutation fails."""
import hashlib
import re
import uuid
import warnings
from contextlib import contextmanager
from functools import wraps
from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError

MAX_RECEIPT_BYTES = 15 * 1024 * 1024
RECEIPT_ACCEPT = 'application/pdf,image/jpeg,image/png,image/webp'
RECEIPT_HELP = 'Opcional. PDF, JPG, PNG ou WebP, até 15 MB. O arquivo fica privado, disponível apenas à equipe autorizada.'


def validate_receipt(upload):
    if not upload:
        return upload
    if not 0 < upload.size <= MAX_RECEIPT_BYTES:
        raise ValidationError('O comprovante deve ter até 15 MB e não pode estar vazio.')
    extension = Path(upload.name).suffix.lower()
    if extension not in {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}:
        raise ValidationError('Envie um comprovante em PDF, JPG, PNG ou WebP.')
    upload.seek(0)
    content = upload.read(MAX_RECEIPT_BYTES + 1)
    upload.seek(0)
    if len(content) != upload.size or len(content) > MAX_RECEIPT_BYTES:
        raise ValidationError('Não foi possível ler o arquivo completo. Selecione o comprovante novamente.')
    if extension == '.pdf':
        if not re.match(rb'%PDF-[12]\.[0-9]', content[:8]) or b'%%EOF' not in content[-2048:]:
            raise ValidationError('Este arquivo não parece ser um PDF válido.')
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as img:
                    expected = {'.jpg':'JPEG', '.jpeg':'JPEG', '.png':'PNG', '.webp':'WEBP'}[extension]
                    if img.format != expected or img.width * img.height > 24000000:
                        raise ValueError
                    img.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValidationError('Envie uma imagem válida de até 24 megapixels, com a extensão correta.') from None
    return upload


def receipt_digest(upload):
    validate_receipt(upload)
    if not upload:
        return ''
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks(): digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


@contextmanager
def receipt_files():
    """Call outside the mutation's atomic block so rollback happens before cleanup."""
    saved = []
    try:
        yield saved
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise


def save_receipt(document, upload, saved):
    if not upload:
        return False
    digest = receipt_digest(upload)
    if document.proof_asset:
        with document.proof_asset.open('rb') as current:
            existing = hashlib.sha256()
            for chunk in current.chunks(): existing.update(chunk)
        if existing.hexdigest() == digest:
            return False
        raise ValidationError('Já existe um comprovante neste registro. O arquivo original foi preservado.')
    name = 'comprovante-' + uuid.uuid4().hex + Path(upload.name).suffix.lower()
    document.proof_asset.save(name, upload, save=False)
    saved.append((document.proof_asset.storage, document.proof_asset.name))
    document.save(update_fields=['proof_asset'])
    return True


def with_receipt_cleanup(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with receipt_files() as saved:
            return func(*args, **kwargs, _saved_receipts=saved)
    return wrapped
