"""
Views da API de remoção de fundo por IA (U2Net) e backup das montagens 3D Pop-Out (estilo Flávio Folia / NaBio).
"""
import hashlib
import logging
import warnings
from io import BytesIO
from time import perf_counter

from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.views.decorators.cache import never_cache
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.events.models import Event
from .background import PROCESSING_LOCK, remove_background
from .models import GeneratedMontage
from .access import authorized_pilot_art

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = 13 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _authorized_montage(request, montage_id):
    if not request.user.is_authenticated or not request.user.is_active:
        return None
    montage = GeneratedMontage.objects.filter(pk=montage_id, owner=request.user, registration__isnull=False).first()
    if not montage or not authorized_pilot_art(request.user, montage.registration_id):
        return None
    return montage


@never_cache
def story_access(request, registration_id):
    if request.method != "GET":
        return _error("Método não permitido.", 405)
    if not authorized_pilot_art(request.user, registration_id):
        return _error("A arte exige uma inscrição de piloto paga e vinculada à sua conta.", 403)
    return JsonResponse({"allowed": True})


@never_cache
def remove_photo_background(request):
    if request.method != "POST":
        response = _error("Envie sua foto para criar o recorte.", 405)
        response["Allow"] = "POST"
        return response

    if not request.user.is_authenticated or not request.user.is_active:
        return _error("Entre na sua conta para criar a arte do piloto confirmado.", 403)

    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES:
            return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)
        registration = authorized_pilot_art(request.user, request.POST.get("registration_id"))
        if not registration:
            return _error("A arte exige uma inscrição de piloto paga e vinculada à sua conta.", 403)
        photo = request.FILES.get("photo")
    except (RequestDataTooBig, TooManyFilesSent):
        return _error("Envie uma única foto de até 12 MB.", 413)
    except (ValueError, MultiPartParserError):
        return _error("Não foi possível receber essa foto. Tente novamente.")

    if photo is None:
        return _error("Escolha uma foto para continuar.")
    if photo.size > MAX_UPLOAD_BYTES:
        return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)

    try:
        raw = photo.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return _error("A foto é muito grande. Escolha uma imagem de até 12 MB.", 413)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ALLOWED_FORMATS:
                    return _error("Use uma foto JPG, PNG ou WebP.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    return _error("Escolha uma foto com até 12 megapixels.", 413)
                if getattr(source, "is_animated", False):
                    return _error("Escolha uma foto estática JPG, PNG ou WebP.")
                source.verify()
            with Image.open(BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return _error("A resolução da foto é muito grande. Escolha outra imagem.", 413)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return _error("Não foi possível abrir essa foto. Tente outra imagem.")

    if not PROCESSING_LOCK.acquire(blocking=False):
        image.close()
        response = _error("Já estamos preparando uma foto. Tente novamente em instantes.", 503)
        response["Retry-After"] = "3"
        return response

    event = registration.event
    montage = None
    try:
        montage = GeneratedMontage.objects.create(
            event=event,
            owner=request.user,
            registration=registration,
            pilot_name=registration.person.full_name[:120],
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("montage_start_log_failed")

    try:
        inference_started = perf_counter()
        cutout = remove_background(image)
        inference_ms = (perf_counter() - inference_started) * 1000
        encode_started = perf_counter()
        cutout.info.clear()
        output = BytesIO()
        cutout.save(output, format="PNG", compress_level=3)
        cutout.close()
        encode_ms = (perf_counter() - encode_started) * 1000
    except Exception:
        logger.exception("Local background removal failed")
        return _error("Não foi possível preparar o recorte agora. Tente novamente.", 503)
    finally:
        PROCESSING_LOCK.release()
        image.close()

    response = HttpResponse(output.getvalue(), content_type="image/png")
    response["Server-Timing"] = f"segmentation;dur={inference_ms:.1f}, encode;dur={encode_ms:.1f}"
    if montage:
        response["X-Montage-ID"] = str(montage.pk)
    response["Content-Disposition"] = 'inline; filename="foto-sem-fundo.png"'
    return response


@never_cache
def backup_montage(request, montage_id):
    if request.method != "POST":
        return _error("Método não permitido.", 405)

    montage = _authorized_montage(request, montage_id)
    if not montage:
        return _error("Montagem não encontrada.", 404)

    uploaded = request.FILES.get("image")
    if not uploaded or uploaded.size > MAX_UPLOAD_BYTES:
        return _error("Arquivo inválido.", 400)

    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    try:
        with Image.open(BytesIO(raw)) as img:
            if img.format != "PNG":
                return _error("Formato inválido.", 400)
            img.verify()
    except Exception:
        return _error("PNG inválido.", 400)

    version = request.POST.get("version", "trilhao-fogo")[:60]
    pilot_name = montage.registration.person.full_name[:120]
    team_name = request.POST.get("team_name", "").strip()[:120]
    digest = hashlib.sha256(raw).hexdigest()

    montage.version = version
    if pilot_name:
        montage.pilot_name = pilot_name
    if team_name:
        montage.team_name = team_name
    montage.sha256 = digest
    montage.file_size = len(raw)
    montage.generated_image.save(f"{version}-{digest[:12]}.png", ContentFile(raw), save=True)

    return JsonResponse({"ok": True, "archive_id": digest})


@never_cache
def deliver_download(request, montage_id):
    if request.method != "POST":
        return _error("Método não permitido.", 405)

    montage = _authorized_montage(request, montage_id)
    if not montage:
        return _error("Montagem indisponível para esta conta ou inscrição.", 403)
    uploaded = request.FILES.get("image")
    if not uploaded or uploaded.size > MAX_UPLOAD_BYTES:
        return _error("Imagem ausente.", 400)

    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    download_id = request.POST.get("download_id", "")
    version = request.POST.get("version", "trilhao-fogo")[:60]
    pilot_name = montage.registration.person.full_name[:120]
    team_name = request.POST.get("team_name", "").strip()[:120]

    if montage:
        montage.downloads_count += 1
        montage.version = version
        if pilot_name:
            montage.pilot_name = pilot_name
        if team_name:
            montage.team_name = team_name
        if not montage.generated_image:
            digest = hashlib.sha256(raw).hexdigest()
            montage.sha256 = digest
            montage.file_size = len(raw)
            montage.generated_image.save(f"{version}-{digest[:12]}.png", ContentFile(raw), save=False)
        montage.save()

    response = HttpResponse(raw, content_type="image/png")
    if download_id:
        response["X-Download-ID"] = download_id
    return response
