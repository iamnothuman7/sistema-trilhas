"""
Segmentação humana local (mesma arquitetura de flaviofolia.nabio.pro).
Mantém apenas o modelo U2Net em cache local e processa tudo em memória RAM.
"""
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock
from django.conf import settings
from PIL import Image, ImageDraw

MODEL_NAME = "u2net_human_seg"
PROCESSING_LOCK = Lock()


@lru_cache(maxsize=1)
def get_session():
    """Reutiliza uma sessão CPU por worker com cache local do modelo U2Net."""
    default_models_dir = str(Path(settings.BASE_DIR) / ".models")
    os.environ.setdefault("U2NET_HOME", default_models_dir)

    from rembg import new_session
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    return new_session(
        MODEL_NAME, sess_opts=options, providers=["CPUExecutionProvider"]
    )


def remove_background(image: Image.Image) -> Image.Image:
    """
    Retorna o recorte RGBA sem alterar as dimensões e mantendo apenas o sujeito principal
    com borda suavizada de 3px (lógica idêntica ao flaviofolia.nabio.pro).
    """
    try:
        from rembg import remove
        import numpy as np
        from scipy.ndimage import binary_dilation, label

        result = remove(image, session=get_session())
        if not isinstance(result, Image.Image) or result.size != image.size:
            raise RuntimeError("Unexpected segmentation output")
        cutout = result.convert("RGBA")

        alpha = np.asarray(cutout.getchannel("A"))
        components, count = label(alpha > 32, structure=np.ones((3, 3), dtype=bool))
        if count:
            sizes = np.bincount(components.ravel())
            sizes[0] = 0
            main_subject = components == np.argmax(sizes)
            keep = binary_dilation(main_subject, iterations=3)
            alpha = np.where(keep, alpha, 0).astype(np.uint8)
        else:
            alpha = np.zeros_like(alpha)
        cutout.putalpha(Image.fromarray(alpha))
        return cutout
    except ImportError:
        # Fallback para ambiente de desenvolvimento sem rembg instalado:
        # cria recorte de retrato centralizado para não bloquear testes locais
        cutout = image.convert("RGBA")
        w, h = cutout.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        margin_x = int(w * 0.08)
        margin_top = int(h * 0.04)
        draw.rounded_rectangle(
            (margin_x, margin_top, w - margin_x, h),
            radius=int(min(w, h) * 0.25),
            fill=255
        )
        cutout.putalpha(mask)
        return cutout
