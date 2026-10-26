"""Bounded decoding of artwork uploads, independent of MIME and filename."""
import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = 13 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000


class InvalidArtwork(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def canonical_png(uploaded):
    """Return actual, static PNG pixels without metadata or appended content."""
    if uploaded is None:
        raise InvalidArtwork("Escolha uma imagem PNG para continuar.")
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise InvalidArtwork("A imagem deve ter até 12 MB.", 413)
    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidArtwork("A imagem deve ter até 12 MB.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format != "PNG" or getattr(source, "is_animated", False):
                    raise InvalidArtwork("Use uma imagem PNG estática.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise InvalidArtwork("A imagem deve ter até 12 megapixels.", 413)
                source.verify()
            with Image.open(BytesIO(raw)) as source:
                source.load()
                # A new image copies only pixels, not EXIF, text, profiles or
                # arbitrary bytes after the PNG end marker.
                pixels = source.convert("RGBA")
                pixels.info.clear()
                try:
                    output = BytesIO()
                    pixels.save(output, format="PNG", compress_level=3)
                finally:
                    pixels.close()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise InvalidArtwork("A resolução da imagem é muito grande.", 413) from exc
    except InvalidArtwork:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidArtwork("Não foi possível abrir o PNG. Escolha outra imagem.") from exc
    value = output.getvalue()
    if len(value) > MAX_UPLOAD_BYTES:
        raise InvalidArtwork("A imagem deve ter até 12 MB após o processamento.", 413)
    return value
